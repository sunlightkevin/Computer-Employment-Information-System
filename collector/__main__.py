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

from .base import BrowserSession, BrowserUnavailable, FetchResult, RateLimiter
from .pipeline import build_job, is_cs_job
from .sources import get_adapter
from .sources.yingjiesheng import DEFAULT_CITIES


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
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条详情，0 为不限")
    parser.add_argument("--delay", type=float, default=3.0, help="请求间隔秒，默认 3")
    parser.add_argument("--dry-run", action="store_true", help="只抓取与归一化，不写库")
    parser.add_argument(
        "--keep-other",
        action="store_true",
        help="保留无法归入任何计算机方向的岗位（默认丢弃，保证采集范围为计算机/IT 类）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每条处理结果")
    return parser


def collect(args: argparse.Namespace) -> tuple[Stats, int]:
    stats = Stats()
    adapter = get_adapter(args.source)
    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
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
                result: FetchResult = session.get(url)
                if result.status >= 400:
                    raise RuntimeError(f"HTTP {result.status}")
                page_rows = adapter.parse_list(result.html, result.url)
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
        with BrowserSession() as session:
            for index, row in enumerate(rows, start=1):
                limiter.wait()
                try:
                    result = session.get(row.url)
                    if result.status >= 400:
                        raise RuntimeError(f"HTTP {result.status}")
                    detail = adapter.parse_detail(result.html, row.url)
                    job = build_job(row, detail, adapter.name)
                    if job is None:
                        stats.skipped += 1
                        if args.verbose:
                            print(f"[{index}/{len(rows)}] 跳过（缺标题或企业） {row.url}")
                        continue

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
