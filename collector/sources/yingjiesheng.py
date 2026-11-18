"""应届生求职网适配器。

站点结构实测见 docs/Spec/recon-yingjiesheng.md。
页面为服务端渲染，等 domcontentloaded 后即可抽取，无需额外等待。

改版时只需要改这个文件：选择器集中在此。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..base import DetailData, ListRow

BASE = "https://www.yingjiesheng.com"
CATEGORY_PATH = "/major/jisuanji"  # 专业分类（9 城市）
MAJOR_PATH = "/zhuanye/jisuanji"   # 专业招聘（68 城市，含郑州）
MAX_PAGES_PER_CITY = 3

# /zhuanye/jisuanji/{city}/ 入口的规模（每页 50 行）。
# 郑州在 /major/ 入口下没有独立 slug，只能从 /zhuanye/ 入口采集。
ZHUANYE_CITY_PAGES = {
    "zhengzhou": 3,
}

# 实测各城市末页（2026-11-17，见 docs/Spec/recon-yingjiesheng.md 的「规模实测」）。
# 全量采集按这个上限翻页；站点扩页后 --all-pages 会按解析到的末页继续。
CITY_TOTAL_PAGES = {
    "beijing": 18,
    "shanghai": 30,
    "guangzhou": 14,
    "shenzhen": 22,
    "wuhan": 11,
    "nanjing": 13,
    "tianjin": 4,
    "chengdu": 6,
    "other": 30,
}

CITY_SLUGS = (
    "beijing",
    "shanghai",
    "guangzhou",
    "shenzhen",
    "tianjin",
    "chengdu",
    "nanjing",
    "wuhan",
    "other",
)

DEFAULT_CITIES = ("beijing", "shanghai", "guangzhou", "shenzhen")
ALL_CITIES = tuple(CITY_SLUGS)

_LABEL_SUFFIX = re.compile(r"[:：]\s*$")


class YingjieshengAdapter:
    name = "yingjiesheng"
    display_name = "应届生求职网"

    # ---------------- 列表页 ----------------

    def list_url(self, city: str, page: int = 1) -> str:
        if city not in CITY_SLUGS:
            raise ValueError(f"未知城市标识：{city}")
        if page <= 1:
            return f"{BASE}{CATEGORY_PATH}/{city}/"
        return f"{BASE}{CATEGORY_PATH}/{city}/list_{page}.html"

    def list_pages(self, cities: list[str] | None = None, pages: int = 5) -> list[str]:
        """按城市顺序推进，每城市最多 MAX_PAGES_PER_CITY 页，凑满 pages 即停。"""
        cities = list(cities or DEFAULT_CITIES)
        if pages <= 0:
            return []
        urls: list[str] = []
        remaining = pages
        for city in cities:
            for page in range(1, MAX_PAGES_PER_CITY + 1):
                if remaining <= 0:
                    return urls
                urls.append(self.list_url(city, page))
                remaining -= 1
        return urls

    def list_all_pages(self, cities: list[str] | None = None) -> list[str]:
        """全量翻页：每个城市翻到实测末页。

        站点各城市页数不同（4～30 页），不能用统一上限。
        """
        cities = list(cities or ALL_CITIES)
        urls: list[str] = []
        for city in cities:
            if city not in CITY_SLUGS:
                raise ValueError(f"未知城市标识：{city}")
            for page in range(1, CITY_TOTAL_PAGES[city] + 1):
                urls.append(self.list_url(city, page))
        return urls

    def zhuanye_list_urls(self, city: str) -> list[str]:
        """专业招聘入口的列表页（每页 50 行）。

        该入口覆盖 68 个城市，含 /major/ 下没有独立 slug 的郑州。
        """
        if city not in ZHUANYE_CITY_PAGES:
            raise ValueError(f"专业招聘入口未登记城市：{city}")
        return [
            f"{BASE}{MAJOR_PATH}/{city}/" if page == 1
            else f"{BASE}{MAJOR_PATH}/{city}/list_{page}.html"
            for page in range(1, ZHUANYE_CITY_PAGES[city] + 1)
        ]

    def parse_list(self, html: str, base_url: str = BASE) -> list[ListRow]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[ListRow] = []
        for tr in soup.select("tr.jobli"):
            cells = tr.find_all("td")
            if not cells:
                continue
            link = cells[0].find("a")
            if link is None or not link.get("href"):
                continue
            url = link["href"].strip()
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            elif url.startswith("/"):
                url = BASE + url

            rows.append(
                ListRow(
                    url=url,
                    raw_title=link.get_text(" ", strip=True),
                    city_raw=cells[1].get_text(" ", strip=True) if len(cells) > 1 else "",
                    source_name=cells[2].get_text(" ", strip=True) if len(cells) > 2 else "",
                    date_raw=cells[3].get_text(" ", strip=True) if len(cells) > 3 else "",
                )
            )
        return rows

    # ---------------- 详情页 ----------------

    def parse_detail(self, html: str, url: str = "") -> DetailData:
        soup = BeautifulSoup(html, "html.parser")
        data = DetailData()

        h1 = soup.select_one(".comtit h1")
        if h1 is not None:
            data.company = h1.get_text(" ", strip=True)

        for label in soup.select(".comtit label.lab"):
            em = label.find("em")
            if em is None:
                continue
            key = _LABEL_SUFFIX.sub("", em.get_text(strip=True))
            value_el = label.select_one("span.i") or label.select_one("b.i")
            value = value_el.get_text(" ", strip=True) if value_el is not None else ""
            if key == "职位":
                data.title = value
            elif key == "工作地点":
                data.city_raw = value
            elif key == "职位类型":
                data.type_raw = value
            elif key == "发布时间":
                data.date_raw = value
            elif key == "来源":
                data.source_name = value

        block = soup.select_one(".combox .inf .el")
        if block is not None:
            raw = block.get_text("\n", strip=True)
            data.category, data.summary_raw = _split_category_and_requirement(raw)

        apply_link = soup.select_one("ul.linkbtn a.bgbtn[href]") or soup.select_one("ul.linkbtn a[href]")
        if apply_link is not None and apply_link.get("href"):
            href = apply_link["href"].strip()
            if href.startswith("//"):
                href = "https:" + href
            data.apply_info = f"在线申请：{href}"

        if not data.company:
            m = re.search(r"公司名称[:：]\s*([^\n]+)", soup.get_text("\n", strip=True))
            if m:
                data.company = m.group(1).strip()

        return data


def _split_category_and_requirement(raw: str) -> tuple[str, str]:
    """把「职能类别:xxx / 岗位要求：正文」拆成两段。"""
    text = raw.replace("\r\n", "\n")
    category = ""
    m = re.search(r"职能类别[:：]\s*([^\n]*)", text)
    if m:
        category = m.group(1).strip()

    m = re.search(r"岗位要求[:：]?\s*(.*)", text, re.S)
    if m:
        summary = m.group(1).strip()
    else:
        summary = re.sub(r"职能类别[:：][^\n]*", "", text).strip()

    return category, summary
