"""原文链接可达性检查（对应 page-structure.md 的 S-02）。

判定方式由实测决定，两条都是踩出来的：

1. 来源站前面挂着阿里云 WAF 的 JS 挑战。直连 HTTP 永远只拿到挑战页
   （HTTP 200 + ``acw_sc__v2`` 脚本），状态码和正文都没有判定价值，
   正常岗位和不存在的岗位返回的挑战页甚至一模一样。
2. 站点对不存在的岗位不返回 404，而是 302 回首页，落地页照样是 HTTP 200，
   标题变成首页标题。所以「状态码 200」不能当成「页面还在」。

因此判定必须走真实浏览器，并且同时看两件事：
落地 URL 是否还停在原来的详情页路径上、页面里是否还能解析出详情主体。
任一不满足即判定不可达。

探活（跑浏览器、拿事实）和判定（纯函数）分开写：判定可被单测全覆盖，
不用联网、不用浏览器。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

DEFAULT_TIMEOUT = 20.0
# 详情页主体，与 collector/sources/yingjiesheng.py 用的是同一个选择器。
# 采集侧解析不出它，说明页面已经不是岗位详情页。
DETAIL_SELECTOR = ".comtit h1"
# WAF 挑战页会自己 reload 一次，等主体的稳定窗口。
STABLE_WINDOW = 8.0

# 浏览器启动和页面加载都是重活，同一时刻只跑一个检查，
# 免得连续点击时同时拉起多个浏览器实例。
_LOCK = threading.Lock()


@dataclass
class Probe:
    """一次探活拿到的原始事实。"""

    status: int
    final_url: str
    html: str


def _path(url: str) -> str:
    """只比对路径。WAF 可能往查询串里塞 alichlgref 之类的参数，查询串不可信。"""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    return path.rstrip("/") or "/"


def detail_title(html: str) -> str:
    """详情页主体文本，取不到说明不是详情页。"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(DETAIL_SELECTOR)
    return node.get_text(strip=True) if node else ""


def judge(requested: str, probe: Probe | None) -> bool:
    """根据探活结果判定原文是否可达。"""
    if probe is None:
        return False
    if probe.status <= 0 or probe.status >= 400:
        return False
    # 落地页换了路径：站点把不存在的岗位重定向走了
    if _path(probe.final_url) != _path(requested):
        return False
    # 路径没变，但页面里已经没有详情主体
    return bool(detail_title(probe.html))


def _probe(url: str, timeout: float) -> Probe | None:
    """用真实浏览器打开一次，返回落地 URL 与页面 HTML。打不开返回 None。"""
    from collector.base import BrowserSession

    try:
        with BrowserSession(timeout_ms=int(timeout * 1000)) as session:
            response = session.get(url)
            status = response.status
            # 已经被重定向走的，没什么可等；还停在原路径的，等 WAF 挑战结束。
            if _path(session.page.url) == _path(url):
                try:
                    session.page.wait_for_selector(
                        DETAIL_SELECTOR,
                        timeout=int(min(timeout, STABLE_WINDOW) * 1000),
                    )
                except Exception:
                    pass
            return Probe(status=status, final_url=session.page.url, html=session.page.content())
    except Exception:
        return None


def is_reachable(url: str, timeout: float = DEFAULT_TIMEOUT, fetcher=None) -> bool:
    """原文是否仍然可达。

    ``fetcher`` 只为测试留出注入点，正常调用不传。
    """
    if not url or not url.startswith(("http://", "https://")):
        return False

    fetch = fetcher or _probe
    try:
        with _LOCK:
            probe = fetch(url, timeout)
    except Exception:
        return False
    return judge(url, probe)
