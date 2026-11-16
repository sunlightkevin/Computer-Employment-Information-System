"""采集基础设施：浏览器会话、限频、站点适配器接口。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, Page, sync_playwright

DEFAULT_TIMEOUT_MS = 45_000
CHANNEL_CANDIDATES = ("chrome", "msedge")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class BrowserUnavailable(RuntimeError):
    """本机没有可用的浏览器通道。"""


@dataclass
class FetchResult:
    status: int
    url: str
    html: str


@dataclass
class ListRow:
    """列表页的一行候选，字段尚未归一化。"""

    url: str
    raw_title: str
    city_raw: str = ""
    source_name: str = ""
    date_raw: str = ""


@dataclass
class DetailData:
    """详情页抽取结果，字段尚未归一化。"""

    title: str = ""
    company: str = ""
    city_raw: str = ""
    type_raw: str = ""
    category: str = ""
    summary_raw: str = ""
    apply_info: str = ""
    date_raw: str = ""
    source_name: str = ""


class RateLimiter:
    """串行请求的最小间隔控制。目的是避免被封 IP，不是合规要求。"""

    def __init__(self, delay_seconds: float) -> None:
        self.delay = max(0.0, float(delay_seconds))
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is None or self.delay <= 0:
            self._last = time.monotonic()
            return
        elapsed = time.monotonic() - self._last
        remaining = self.delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()


class BrowserSession:
    """一个浏览器实例，复用一个 context 与 page。

    本机无法下载 Chromium，因此使用已安装的 Chrome，失败时回落 Edge。
    """

    def __init__(self, headless: bool = True, channel: str | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        self.headless = headless
        self.channel = channel
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.used_channel: str | None = None

    def __enter__(self) -> "BrowserSession":
        self._pw = sync_playwright().start()
        errors: list[str] = []
        for channel in ([self.channel] if self.channel else list(CHANNEL_CANDIDATES)):
            try:
                self._browser = self._pw.chromium.launch(channel=channel, headless=self.headless)
                self.used_channel = channel
                break
            except PlaywrightError as exc:  # 通道不存在或启动失败
                errors.append(f"{channel}: {str(exc).splitlines()[0][:120]}")
        if self._browser is None:
            self._pw.stop()
            self._pw = None
            raise BrowserUnavailable("无法启动浏览器：" + "; ".join(errors))

        self._context = self._browser.new_context(
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, *exc_info) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("浏览器会话未启动")
        return self._page

    def get(self, url: str) -> FetchResult:
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        status = response.status if response is not None else 0
        return FetchResult(status=status, url=self.page.url, html=self.page.content())


class SourceAdapter(Protocol):
    """站点适配器接口。站点改版时只改实现类。"""

    name: str

    def list_pages(self, cities: list[str], pages: int) -> list[str]:
        """返回要抓取的列表页 URL 序列。"""
        ...

    def parse_list(self, html: str, base_url: str) -> list[ListRow]:
        ...

    def parse_detail(self, html: str, url: str) -> DetailData:
        ...
