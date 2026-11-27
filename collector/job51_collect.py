"""51job 专用采集流程：搜索页内 fetch 公开接口 → JSON 解析 → IT 过滤 → 入库。

与应届生的「列表页 → 详情页」流程不同，51job 接口一次返回摘要与职能类别，
不需要访问撞滑块的详情页。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from store import db as db_module
from store import queries

from .base import BrowserUnavailable, CHANNEL_CANDIDATES, DEFAULT_TIMEOUT_MS, USER_AGENT, is_waf_page
from .normalize import build_dedupe_key
from .sources.job51 import (
    DEFAULT_KEYWORDS,
    FUNTYPE_DICT_URL,
    JOB_AREA_HENAN,
    Job51Row,
    api_url,
    classify_directions,
    classify_job_type,
    is_it_job,
    load_it_codes,
    parse_api_json,
    search_page_url,
)

# fetch 加超时保护：WAF 可能让请求一直 pending，不能让整个采集卡死。
FETCH_JS = """
async ({url, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { credentials: 'include', signal: controller.signal });
    const body = await r.text();
    return { status: r.status, body: body };
  } catch (e) {
    return { status: 0, body: '', error: String(e) };
  } finally {
    clearTimeout(timer);
  }
}
"""


@dataclass
class Stats51:
    keywords: int = 0
    pages_ok: int = 0
    pages_failed: int = 0
    rows_seen: int = 0
    skipped_non_it: int = 0
    skipped_invalid: int = 0
    added: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"关键词 {self.keywords}；列表页 成功 {self.pages_ok} / 失败 {self.pages_failed}；"
            f"候选 {self.rows_seen}；跳过(非IT) {self.skipped_non_it}，"
            f"跳过(字段不足) {self.skipped_invalid}；新增 {self.added}，更新 {self.updated}，失败 {self.failed}"
        )


def _load_it_codes(page) -> set[str]:
    resp = page.request.get(FUNTYPE_DICT_URL, timeout=DEFAULT_TIMEOUT_MS)
    if resp.status >= 400:
        raise RuntimeError(f"职能类别字典 HTTP {resp.status}")
    return load_it_codes(resp.text())


def _fetch_api(page, url: str, timeout_ms: int = 20000) -> tuple[list[Job51Row], int]:
    out = page.evaluate(FETCH_JS, {"url": url, "timeoutMs": timeout_ms})
    if out.get("status") != 200:
        raise RuntimeError(f"接口 HTTP {out.get('status')} {out.get('error', '')}")
    body = out.get("body") or ""
    if is_waf_page(body):
        raise RuntimeError("接口返回验证页")
    return parse_api_json(body)


def _build_job(row: Job51Row, it_codes: set[str]):
    if not is_it_job(row, it_codes):
        return None, "non_it"
    if not row.source_url:
        return None, "invalid"

    cities = [row.city] if row.city else ["其它"]
    directions = classify_directions(row)
    if not directions:
        return None, "invalid"

    return {
        "dedupe_key": build_dedupe_key(row.company, row.title, cities),
        "title": row.title,
        "company": row.company,
        "job_type": classify_job_type(row),
        "summary": row.summary,
        "apply_info": None,
        "published_at": row.published_at,
        "source_site": "51job",
        "source_url": row.source_url,
        "external_id": row.job_id or None,
        "cities": cities,
        "directions": directions,
    }, None


def collect_51job(
    keywords: list[str] | None = None,
    job_area: str = JOB_AREA_HENAN,
    delay: float = 3.0,
    limit_pages_per_keyword: int = 0,
    dry_run: bool = False,
    db_path: str | None = None,
    verbose: bool = False,
) -> Stats51:
    """按关键词逐个采集。

    limit_pages_per_keyword: 0 表示翻完 totalCount 所有页；>0 用于试跑。
    """
    stats = Stats51()
    keywords = list(keywords or DEFAULT_KEYWORDS)
    stats.keywords = len(keywords)

    conn = None if dry_run else (db_module.open_db(db_path) if db_path else db_module.open_db())

    with sync_playwright() as pw:
        browser = None
        for channel in CHANNEL_CANDIDATES:
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            raise BrowserUnavailable("无法启动浏览器")

        try:
            ctx = browser.new_context(
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
                user_agent=USER_AGENT,
            )
            page = ctx.new_page()
            it_codes = _load_it_codes(page)
            print(f"互联网技术大类 code 数：{len(it_codes)}")

            for kw_index, keyword in enumerate(keywords, 1):
                print(f"\n=== 关键词 [{kw_index}/{len(keywords)}]：{keyword} ===", flush=True)
                # 每个关键词重建页面，避免 WAF token 跨词累积导致挂起
                page.goto("about:blank")
                page.goto(search_page_url(keyword, job_area), wait_until="domcontentloaded",
                          timeout=DEFAULT_TIMEOUT_MS)
                if is_waf_page(page.content()):
                    stats.errors.append(f"search page WAF: {keyword}")
                    print("  搜索页验证，跳过该关键词", flush=True)
                    continue
                # 等岗位卡片出现，确保页面 JS 已初始化、WAF token 已就绪。
                # 只数 DOM 节点，不反复抓 page.content()（大 HTML 会拖垮渲染进程）。
                deadline = time.time() + 30
                while time.time() < deadline and page.locator(".job-item").count() == 0:
                    time.sleep(1)
                time.sleep(delay)

                ts = int(time.time())
                first_url = api_url(keyword, job_area, 1, ts)
                try:
                    rows, total = _fetch_api(page, first_url)
                except Exception as exc:
                    stats.pages_failed += 1
                    stats.errors.append(f"api {keyword} page1: {exc}")
                    print(f"  首页失败：{exc}")
                    continue
                stats.pages_ok += 1

                import math
                pages = max(1, math.ceil(total / 20))
                if limit_pages_per_keyword:
                    pages = min(pages, limit_pages_per_keyword)
                print(f"  共 {total} 条 / {pages} 页，本页 {len(rows)} 条")

                page_no = 1
                while True:
                    for row in rows:
                        stats.rows_seen += 1
                        job, skip = _build_job(row, it_codes)
                        if skip == "non_it":
                            stats.skipped_non_it += 1
                            if verbose:
                                print(f"    跳过(非IT) {row.func_code} {row.company} | {row.title}")
                            continue
                        if skip == "invalid":
                            stats.skipped_invalid += 1
                            continue
                        if dry_run:
                            if verbose:
                                print(f"    [dry] {job['job_type']:<8} {row.company} | {row.title}")
                            continue
                        try:
                            outcome, _ = queries.save_job(conn, job)
                            if outcome == "added":
                                stats.added += 1
                            else:
                                stats.updated += 1
                            if verbose:
                                print(f"    {outcome} {row.company} | {row.title}")
                        except Exception as exc:
                            stats.failed += 1
                            stats.errors.append(f"save {row.job_id}: {exc}")
                            print(f"    入库失败 {row.job_id}: {exc}")

                    if page_no >= pages:
                        break
                    page_no += 1
                    time.sleep(delay)
                    url = api_url(keyword, job_area, page_no, int(time.time()))
                    try:
                        rows, _ = _fetch_api(page, url)
                        stats.pages_ok += 1
                    except Exception as exc:
                        stats.pages_failed += 1
                        stats.errors.append(f"api {keyword} page{page_no}: {exc}")
                        print(f"  第 {page_no} 页失败：{exc}", flush=True)
                        break

                print(
                    f"  [{kw_index}/{len(keywords)}] {keyword} 完成："
                    f"累计新增 {stats.added}，更新 {stats.updated}，失败 {stats.failed}",
                    flush=True,
                )
        finally:
            if conn is not None:
                conn.close()
            # 浏览器可能已崩溃（Target crashed），close 会挂起；加超时保护。
            try:
                browser.close()
            except Exception:
                pass

    return stats
