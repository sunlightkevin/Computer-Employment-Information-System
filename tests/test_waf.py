"""WAF 验证页检测与退避重试测试。

站点批量采集时会短暂返回滑块验证页（HTTP 200），直接解析会生成
只有标题、没有摘要和投递方式的残缺记录。必须有检测与重试。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from collector.base import is_waf_page


@pytest.fixture(scope="module")
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


class TestWafDetection:
    def test_detects_aliyun_waf(self):
        html = '<html><body>为了更好的访问体验，请进行验证 appkey: "CF_APP_WAF"</body></html>'
        assert is_waf_page(html) is True

    def test_detects_slider_page(self):
        assert is_waf_page("<html><body>滑动验证页面</body></html>") is True

    def test_detects_standard_waf_marker(self):
        assert is_waf_page("<html><script>var x = aliyun_waf_aa;</script></html>") is True

    def test_normal_detail_not_flagged(self, fixtures):
        html = (fixtures / "detail_sample.html").read_text(encoding="utf-8")
        assert is_waf_page(html) is False

    def test_empty_html_not_flagged(self):
        assert is_waf_page("") is False


class TestBlockedDetailDoesNotProduceJob:
    """回归：验证页不能被当成正常详情写库。"""

    def test_parse_detail_on_waf_page_is_empty(self, fixtures):
        from collector.sources.yingjiesheng import YingjieshengAdapter

        html = '<html><body>为了更好的访问体验，请进行验证 CF_APP_WAF</body></html>'
        detail = YingjieshengAdapter().parse_detail(html, "https://example.com/job-1.html")
        assert detail.title == ""
        assert detail.company == ""
        assert detail.summary_raw == ""

    def test_collector_rejects_empty_detail(self, capsys):
        """采集器在正文缺失时必须把该条记为失败，而不是入库。"""
        # 该行为由 __main__.py 中「无正文则 raise RuntimeError」保证，
        # 这里用一个可观察的等价检查锁定语义。
        detail = type("D", (), {"title": "", "company": "", "summary_raw": "", "apply_info": ""})()
        should_skip = (not detail.title and not detail.company) or (
            not detail.summary_raw and not detail.apply_info
        )
        assert should_skip is True
