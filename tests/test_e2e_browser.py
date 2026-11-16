"""浏览器端到端测试：勾选即筛选、从详情返回时条件与滚动位置都还在。

这两条（T3.5 的交互形态、T3.7 的滚动还原）依赖真实浏览器行为，
模板层的字符串断言证明不了，所以起一个本地服务加真浏览器跑一遍。
"""
from __future__ import annotations

import threading
import urllib.parse

import pytest
from werkzeug.serving import make_server

from app import create_app
from collector.base import BrowserSession, BrowserUnavailable
from store import db as db_module
from store import queries

BEIJING = urllib.parse.quote("北京")
SHANGHAI = urllib.parse.quote("上海")
BACKEND = urllib.parse.quote("后端")


def seed(conn) -> None:
    for index in range(1, 25):
        city = "北京" if index % 2 else "上海"
        queries.save_job(
            conn,
            {
                "dedupe_key": f"公司{index}|岗位{index:02d}|{city}",
                "title": f"岗位{index:02d}",
                "company": f"公司{index}",
                "job_type": "fulltime",
                "summary": "摘要",
                "apply_info": "在线申请",
                "published_at": f"2026-01-{index:02d}",
                "source_site": "yingjiesheng",
                "source_url": f"https://www.yingjiesheng.com/job-{index:03d}.html",
                "external_id": f"{index:03d}",
                "cities": [city],
                "directions": ["后端"],
            },
        )


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    conn = db_module.open_db(path)
    seed(conn)
    conn.close()

    application = create_app(str(path))
    server = make_server("127.0.0.1", 0, application, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def session():
    browser = BrowserSession()
    try:
        browser.__enter__()
    except BrowserUnavailable as exc:
        pytest.skip(f"本机没有可用浏览器：{exc}")
    try:
        browser.page.set_viewport_size({"width": 1280, "height": 480})
        yield browser
    finally:
        browser.__exit__(None, None, None)


class TestCheckboxFiltering:
    def test_checking_a_city_filters_without_submit(self, session, live_server):
        page = session.page
        page.goto(f"{live_server}/?type=fulltime", wait_until="domcontentloaded")

        page.click('label.opt:has(input[value="上海"])')
        page.wait_for_url(f"**city={SHANGHAI}**", timeout=5000)

        assert "city=" + SHANGHAI in page.url
        assert page.locator("a.card").count() == 10

    def test_unchecking_returns_to_full_list(self, session, live_server):
        page = session.page
        page.goto(f"{live_server}/?type=fulltime&city={SHANGHAI}", wait_until="domcontentloaded")
        assert page.locator('label.opt:has(input[value="上海"]):has(input:checked)').count() == 1

        page.click('label.opt:has(input[value="上海"])')
        # 不能写成 "**/"，带查询串的 /?type=fulltime 匹配不上
        page.wait_for_url(lambda url: "city=" not in url, timeout=5000)
        assert "type=fulltime" in page.url


class TestSearch:
    def test_type_and_enter_filters(self, session, live_server):
        page = session.page
        page.goto(f"{live_server}/?type=fulltime", wait_until="domcontentloaded")

        page.fill('input[name="q"]', "岗位01")
        page.press('input[name="q"]', "Enter")
        page.wait_for_url(lambda url: "q=" in url, timeout=5000)

        assert page.locator("a.card").count() == 1
        assert page.input_value('input[name="q"]') == "岗位01"

    def test_search_keeps_selected_city(self, session, live_server):
        page = session.page
        page.goto(f"{live_server}/?type=fulltime&city={BEIJING}", wait_until="domcontentloaded")

        page.fill('input[name="q"]', "岗位")
        page.press('input[name="q"]', "Enter")
        page.wait_for_url(lambda url: "q=" in url, timeout=5000)

        assert f"city={BEIJING}" in page.url
        assert page.locator("a.card").count() > 0

    def test_clear_keyword_keeps_city(self, session, live_server):
        page = session.page
        page.goto(f"{live_server}/?type=fulltime&city={BEIJING}&q=岗位01", wait_until="domcontentloaded")

        page.click(".search-clear")
        page.wait_for_url(lambda url: "q=" not in url, timeout=5000)

        assert f"city={BEIJING}" in page.url
        assert page.input_value('input[name="q"]') == ""


class TestBackFromDetail:
    def test_back_keeps_filters_and_scroll(self, session, live_server):
        page = session.page
        url = f"{live_server}/?type=fulltime&city={BEIJING}&direction={BACKEND}"
        page.goto(url, wait_until="domcontentloaded")

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(200)
        scrolled = page.evaluate("window.scrollY")
        assert scrolled > 300, "列表页没有滚动起来，这个用例就没意义"

        # 点最后一张卡：它已在视野内，Playwright 不会为了点它而改滚动位置
        page.locator("a.card").last.click()
        page.wait_for_url("**/job/**", timeout=5000)

        page.click("#backToList")
        page.wait_for_url(lambda target: "/job/" not in target, timeout=5000)

        assert f"city={BEIJING}" in page.url
        assert f"direction={BACKEND}" in page.url
        assert page.evaluate("window.scrollY") > 300

    def test_direct_detail_back_link_still_carries_filters(self, session, live_server):
        """直接打开详情页没有上一条历史，靠 href 里带的参数回去。"""
        page = session.page
        page.goto(f"{live_server}/?type=fulltime&city={BEIJING}", wait_until="domcontentloaded")
        first = page.locator("a.card").first.get_attribute("href")
        assert f"city={BEIJING}" in first

        page.goto(f"{live_server}{first}", wait_until="domcontentloaded")
        href = page.locator("#backToList").get_attribute("href")
        assert f"city={BEIJING}" in href
