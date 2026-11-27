"""采集入口。

用法：
    python -m collector --source yingjiesheng --pages 3
"""
from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass, field

from store import db as db_module
from store import queries

from .base import BrowserSession, BrowserUnavailable, FetchResult, RateLimiter, is_waf_page
from .normalize import build_dedupe_key
from .pipeline import build_job, is_cs_job
from .sources import get_adapter
from .sources.yingjiesheng import ALL_CITIES, DEFAULT_CITIES


@dataclass
class Stats:
    pages_ok: int = 0
    pages_failed: int = 0
    rows_seen: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    skipped_non_cs: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"列表页 成功 {self.pages_ok} / 失败 {self.pages_failed}；"
            f"候选 {self.rows_seen}；新增 {self.added}，更新 {self.updated}，"
            f"跳过(字段不足) {self.skipped}，跳过(非计算机类) {self.skipped_non_cs}，"
            f"失败 {self.failed}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="采集岗位信息到本地库")
    parser.add_argument("--source", required=True, help="站点标识，如 yingjiesheng")
    parser.add_argument("--pages", type=int, default=5, help="列表页总数上限，默认 5")
    parser.add_argument(
        "--cities",
        default=",".join(DEFAULT_CITIES),
        help=f"城市标识，逗号分隔，默认 {','.join(DEFAULT_CITIES)}",
    )
    parser.add_argument(
        "--all-pages",
        action="store_true",
        help="全量模式：忽略 --pages，按各城市实际末页翻完（9 城约 4300 条）",
    )
    parser.add_argument(
        "--list-urls-file",
        default=None,
        help="从文件逐行读取列表页 URL（补采失败页用），忽略 --all-pages/--pages/--cities",
    )
    parser.add_argument(
        "--zhuanye-city",
        default=None,
        help="专业招聘入口的城市 slug（如 zhengzhou，150 条 / 3 页），优先于其他入口参数",
    )
    parser.add_argument(
        "--default-city",
        default=None,
        help="城市兜底：当详情城市为「其它」或空时填入（如郑州页面采来的岗位填郑州）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条详情，0 为不限")
    parser.add_argument(
        "--session-size",
        type=int,
        default=0,
        help="每多少条详情重建一次浏览器会话，0 表示全程一个会话。站点触发 WAF 时建议 20",
    )
    parser.add_argument("--delay", type=float, default=3.0, help="请求间隔秒，默认 3")
    parser.add_argument("--dry-run", action="store_true", help="只抓取与归一化，不写库")
    parser.add_argument(
        "--keep-other",
        action="store_true",
        help="保留无法归入任何计算机方向的岗位（默认丢弃，保证采集范围为计算机/IT 类）",
    )
    parser.add_argument(
        "--keywords",
        default=None,
        help="51job 采集关键词，逗号分隔；不传则用内置 11 个词",
    )
    parser.add_argument(
        "--job-area",
        default="170000",
        help="51job 地区代码，默认 170000（河南省）",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="51job 每个关键词最多翻多少页，0 表示翻完；试跑可设 1",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="写入数据库路径，默认 data/jobs.db",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每条处理结果")
    return parser


def _session_batches(rows: list, size: int):
    """分批提供浏览器会话；每 size 条重建一次，避免同会话请求过多触发 WAF。"""
    if size <= 0:
        size = len(rows) or 1
    for start in range(0, len(rows), size):
        batch = list(enumerate(rows[start:start + size], start=start + 1))
        with BrowserSession() as session:
            yield session, batch


def collect(args: argparse.Namespace) -> tuple[Stats, int]:
    stats = Stats()
    adapter = get_adapter(args.source)
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    if args.zhuanye_city:
        if not hasattr(adapter, "zhuanye_list_urls"):
            print("该站点不支持专业招聘入口")
            return stats, 1
        list_urls = adapter.zhuanye_list_urls(args.zhuanye_city)
    elif args.list_urls_file:
        with open(args.list_urls_file, encoding="utf-8") as fh:
            list_urls = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
    elif args.all_pages:
        # 全量模式强制覆盖全部城市，忽略 --cities 默认只取 4 城的限制
        if hasattr(adapter, "list_all_pages"):
            cities = list(ALL_CITIES)
            list_urls = adapter.list_all_pages(cities)
        else:
            print("该站点尚未实现全量翻页")
            return stats, 1
    else:
        list_urls = adapter.list_pages(cities, args.pages)

    if not list_urls:
        print("没有可抓取的列表页，请检查 --pages 与 --cities")
        return stats, 1

    limiter = RateLimiter(args.delay)
    rows = []
    seen_urls: set[str] = set()

    with BrowserSession() as session:
        print(f"浏览器通道：{session.used_channel}")

        for url in list_urls:
            limiter.wait()
            try:
                result: FetchResult = session.get_unblocked(url)
                if result.status >= 400:
                    raise RuntimeError(f"HTTP {result.status}")
                if is_waf_page(result.html):
                    raise RuntimeError("验证页（重试后仍未恢复）")
                page_rows = adapter.parse_list(result.html, result.url)
                if not page_rows:
                    raise RuntimeError("列表页无岗位行（疑似验证页）")
                stats.pages_ok += 1
                print(f"列表页 OK  {url}  ->  {len(page_rows)} 行")
            except Exception as exc:
                stats.pages_failed += 1
                stats.errors.append(f"list {url}: {exc}")
                print(f"列表页 FAIL {url}  ->  {exc}")
                continue

            for row in page_rows:
                if row.url in seen_urls:
                    continue
                seen_urls.add(row.url)
                rows.append(row)

    stats.rows_seen = len(rows)
    if stats.pages_ok == 0:
        print("所有列表页均失败，终止")
        return stats, 1

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    conn = None
    if not args.dry_run:
        conn = db_module.open_db()

    try:
        for session, batch in _session_batches(rows, args.session_size):
            for index, row in batch:
                limiter.wait()
                try:
                    result = session.get_unblocked(row.url)
                    if result.status >= 400:
                        raise RuntimeError(f"HTTP {result.status}")
                    if is_waf_page(result.html):
                        raise RuntimeError("验证页（重试后仍未恢复）")
                    detail = adapter.parse_detail(result.html, row.url)
                    if not detail.title and not detail.company:
                        raise RuntimeError("详情页无岗位主体（疑似验证页）")
                    # 详情正文必须抓到，否则 build_job 会用列表页标题兜底，
                    # 生成没有摘要/投递方式的残缺记录，污染库
                    if not detail.summary_raw and not detail.apply_info:
                        raise RuntimeError("详情页无正文（疑似验证页）")
                    job = build_job(row, detail, adapter.name)
                    if job is None:
                        stats.skipped += 1
                        if args.verbose:
                            print(f"[{index}/{len(rows)}] 跳过（缺标题或企业） {row.url}")
                        continue

                    # 城市兜底：站点详情页常把工作地点标成「其它」，
                    # 但从城市专属页采来的岗位可用该城市补全
                    if args.default_city:
                        cities = [c for c in (job.get("cities") or []) if c not in ("其它", "未知", "")]
                        if not cities:
                            job["cities"] = [args.default_city]
                            job["dedupe_key"] = build_dedupe_key(
                                job.get("company"), job.get("title"), job["cities"]
                            )

                    if not args.keep_other and not is_cs_job(job):
                        stats.skipped_non_cs += 1
                        if args.verbose:
                            print(
                                f"[{index}/{len(rows)}] 跳过（非计算机类） "
                                f"{job['company']} | {job['title']}"
                            )
                        continue

                    if args.dry_run:
                        if args.verbose:
                            print(
                                f"[{index}/{len(rows)}] {job['job_type']:<8} "
                                f"{'/'.join(job['directions']):<10} "
                                f"{job['company']} | {job['title']}"
                            )
                        else:
                            print(f"[{index}/{len(rows)}] {job['company']} | {job['title']}")
                        continue

                    outcome, _ = queries.save_job(conn, job)
                    if outcome == "added":
                        stats.added += 1
                    else:
                        stats.updated += 1
                    if args.verbose:
                        print(f"[{index}/{len(rows)}] {outcome} {job['company']} | {job['title']}")
                except Exception as exc:
                    stats.failed += 1
                    stats.errors.append(f"detail {row.url}: {exc}")
                    print(f"[{index}/{len(rows)}] FAIL {row.url} -> {exc}")
                    continue
    finally:
        if conn is not None:
            conn.close()

    return stats, 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 51job 走专用流程：公开接口 JSON，无需详情页
    if args.source == "51job":
        from .job51_collect import collect_51job

        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None
        try:
            stats = collect_51job(
                keywords=keywords,
                job_area=args.job_area,
                delay=args.delay,
                limit_pages_per_keyword=args.max_pages,
                dry_run=args.dry_run,
                db_path=args.db_path,
                verbose=args.verbose,
            )
        except BrowserUnavailable as exc:
            print(f"浏览器不可用：{exc}")
            return 1
        except KeyboardInterrupt:
            print("已中断")
            return 130
        except Exception:
            traceback.print_exc()
            return 1

        print("-" * 50)
        print(stats.summary())
        if stats.errors:
            print(f"错误明细（前 10 条，共 {len(stats.errors)}）：")
            for line in stats.errors[:10]:
                print("  - " + line)
        return 0

    try:
        stats, code = collect(args)
    except BrowserUnavailable as exc:
        print(f"浏览器不可用：{exc}")
        return 1
    except KeyboardInterrupt:
        print("已中断")
        return 130
    except Exception:
        traceback.print_exc()
        return 1

    print("-" * 50)
    print(stats.summary())
    if stats.errors:
        print(f"错误明细（前 10 条，共 {len(stats.errors)}）：")
        for line in stats.errors[:10]:
            print("  - " + line)
    return code


if __name__ == "__main__":
    sys.exit(main())
