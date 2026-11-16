"""可达性判定测试（S-02）。

覆盖三种真实形态：正常详情页、站点把不存在的岗位 302 回首页、WAF 挑战页。
全部用假探活结果驱动，不联网、不启动浏览器。
"""
from __future__ import annotations

import pytest

import linkcheck
from linkcheck import Probe, detail_title, is_reachable, judge

JOB_URL = "https://www.yingjiesheng.com/job-008-150-162.html"

DETAIL_HTML = """
<html><head><title>数采实习生招聘_节卡机器人股份有限公司_应届生求职网</title></head>
<body><div class="comtit"><label class="lab">名企</label><h1>[上海]节卡机器人股份有限公司</h1></div></body></html>
"""

# 首页与 WAF 挑战页都没有 .comtit h1，这正是判定的依据
HOME_HTML = """
<html><head><title>应届生求职APP –大学生求职app官方下载</title></head>
<body><h1>应届生求职网</h1></body></html>
"""

WAF_HTML = """
<textarea id="renderData">{"l1":"var arg1='5dd7177d98bd'","l2":"GET"}</textarea>
<!doctype html><html><head><meta name="aliyun_waf_aa" content="1"></head><body></body></html>
"""


def test_detail_page_reachable():
    assert judge(JOB_URL, Probe(200, JOB_URL, DETAIL_HTML)) is True


def test_redirect_to_home_is_unreachable():
    """站点对不存在的岗位 302 回首页，落地页仍是 HTTP 200。"""
    probe = Probe(200, "https://www.yingjiesheng.com/", HOME_HTML)
    assert judge(JOB_URL, probe) is False


def test_same_path_without_detail_body_is_unreachable():
    """路径没变但页面不是详情页（渲染失败或站点改版）。"""
    assert judge(JOB_URL, Probe(200, JOB_URL, HOME_HTML)) is False


def test_waf_challenge_page_is_unreachable():
    assert judge(JOB_URL, Probe(200, JOB_URL, WAF_HTML)) is False


@pytest.mark.parametrize("status", [0, 403, 404, 410, 500, 503])
def test_error_status_is_unreachable(status):
    assert judge(JOB_URL, Probe(status, JOB_URL, DETAIL_HTML)) is False


def test_missing_probe_is_unreachable():
    assert judge(JOB_URL, None) is False


def test_empty_detail_heading_is_unreachable():
    html = '<div class="comtit"><h1>   </h1></div>'
    assert judge(JOB_URL, Probe(200, JOB_URL, html)) is False


def test_tracking_query_does_not_break_judgement():
    """WAF 会在查询串里塞 alichlgref，不能因此判定不可达。"""
    probe = Probe(200, JOB_URL + "?alichlgref=https%3A%2F%2Fwww.yingjiesheng.com%2F", DETAIL_HTML)
    assert judge(JOB_URL, probe) is True


def test_trailing_slash_is_ignored():
    assert judge(JOB_URL, Probe(200, JOB_URL + "/", DETAIL_HTML)) is True


def test_detail_title_extracts_text():
    assert detail_title(DETAIL_HTML) == "[上海]节卡机器人股份有限公司"
    assert detail_title("") == ""
    assert detail_title("<html></html>") == ""


class TestIsReachable:
    def test_uses_injected_fetcher(self):
        seen = {}

        def fake(url, timeout):
            seen["url"] = url
            seen["timeout"] = timeout
            return Probe(200, url, DETAIL_HTML)

        assert is_reachable(JOB_URL, timeout=5.0, fetcher=fake) is True
        assert seen == {"url": JOB_URL, "timeout": 5.0}

    def test_fetcher_returning_none_is_unreachable(self):
        assert is_reachable(JOB_URL, fetcher=lambda url, timeout: None) is False

    def test_fetcher_exception_is_unreachable(self):
        def boom(url, timeout):
            raise RuntimeError("浏览器起不来")

        assert is_reachable(JOB_URL, fetcher=boom) is False

    @pytest.mark.parametrize("url", ["", None, "file:///etc/passwd", "javascript:void(0)", "/job-1.html"])
    def test_non_http_url_is_unreachable_without_probing(self, url):
        called = []
        assert is_reachable(url, fetcher=lambda u, t: called.append(u)) is False
        assert called == []

    def test_does_not_probe_when_url_is_empty(self):
        assert is_reachable("", fetcher=lambda u, t: pytest.fail("不该发起探活")) is False

    def test_default_timeout_is_browser_scale(self):
        """浏览器要启动 + 等 WAF 挑战，3 秒那种 HTTP 级超时不够。"""
        assert linkcheck.DEFAULT_TIMEOUT >= 10
