"""站点探测脚本（阶段 0，非产品代码）。

用法：
    python -m tools.probe_list <url> [--out 文件名] [--wait 秒]

作用：用系统 Chrome 渲染页面，输出结构摘要，并把渲染后的 HTML 存到
tools/_dump/ 下，便于确定字段选择器。不写数据库，不进入产品流程。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DUMP_DIR = Path(__file__).resolve().parent / "_dump"


def pick_channel() -> str:
    """优先系统 Chrome，失败回落 Edge（本机未下载 Chromium）。"""
    return "chrome"


def probe(url: str, out: str | None, wait: float, headless: bool) -> int:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    name = out or re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "_", url)[:60]

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=pick_channel(), headless=headless)
        ctx = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(int(wait * 1000))

        status = resp.status if resp else -1
        final_url = page.url
        title = page.title()
        html = page.content()
        (DUMP_DIR / f"{name}.html").write_text(html, encoding="utf-8")

        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.innerText.trim(), e.getAttribute('href')])"
        )
        body_text = re.sub(r"\n{2,}", "\n", page.inner_text("body"))[:1500]

        print(f"STATUS      : {status}")
        print(f"FINAL_URL   : {final_url}")
        print(f"TITLE       : {title}")
        print(f"HTML_BYTES  : {len(html.encode('utf-8'))}")
        print(f"ANCHORS     : {len(links)}")
        print(f"DUMP        : {DUMP_DIR / (name + '.html')}")
        print("-" * 60)
        print("BODY_TEXT (前 1500 字):")
        print(body_text)
        print("-" * 60)
        print("ANCHORS (前 40 条):")
        for text, href in links[:40]:
            print(f"  {text[:40]!r:44} -> {href}")

        ctx.close()
        browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default=None)
    ap.add_argument("--wait", type=float, default=3.0)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    return probe(a.url, a.out, a.wait, headless=not a.headed)


if __name__ == "__main__":
    sys.exit(main())
