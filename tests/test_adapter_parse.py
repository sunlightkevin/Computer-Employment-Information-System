"""适配器解析与记录组装测试（离线，使用真实页面 fixture）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from collector.base import DetailData, ListRow
from collector.pipeline import build_job, is_cs_job
from collector.sources.yingjiesheng import YingjieshengAdapter

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 16)


@pytest.fixture(scope="module")
def adapter() -> YingjieshengAdapter:
    return YingjieshengAdapter()


@pytest.fixture(scope="module")
def list_html() -> str:
    return (FIXTURES / "list_sample.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail_sample.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def minimal_html() -> str:
    return (FIXTURES / "detail_minimal.html").read_text(encoding="utf-8")


class TestListPages:
    def test_urls_within_one_city(self, adapter):
        urls = adapter.list_pages(["beijing"], pages=3)
        assert urls == [
            "https://www.yingjiesheng.com/major/jisuanji/beijing/",
            "https://www.yingjiesheng.com/major/jisuanji/beijing/list_2.html",
            "https://www.yingjiesheng.com/major/jisuanji/beijing/list_3.html",
        ]

    def test_urls_spill_to_next_city(self, adapter):
        urls = adapter.list_pages(["beijing", "shanghai"], pages=5)
        assert len(urls) == 5
        assert any(u.endswith("beijing/list_3.html") for u in urls)
        assert urls[-2].endswith("/shanghai/")
        assert urls[-1].endswith("/shanghai/list_2.html")

    def test_cap_per_city(self, adapter):
        urls = adapter.list_pages(["beijing"], pages=10)
        assert len(urls) == 3

    def test_zero_pages(self, adapter):
        assert adapter.list_pages(["beijing"], pages=0) == []

    def test_unknown_city(self, adapter):
        with pytest.raises(ValueError):
            adapter.list_url("atlantis", 1)


class TestParseList:
    def test_row_count(self, adapter, list_html):
        assert len(adapter.parse_list(list_html)) == 3

    def test_url_upgraded_to_https(self, adapter, list_html):
        rows = adapter.parse_list(list_html)
        assert all(row.url.startswith("https://") for row in rows)

    def test_fields(self, adapter, list_html):
        first = adapter.parse_list(list_html)[0]
        assert first.url == "https://www.yingjiesheng.com/job-008-150-254.html"
        assert first.raw_title.startswith("[北京]中证金融研究院")
        assert first.city_raw == "北京"
        assert first.source_name == "前程无忧(51JOB)"
        assert first.date_raw == "2026-11-16"

    def test_multi_city_raw(self, adapter, list_html):
        second = adapter.parse_list(list_html)[1]
        assert second.city_raw.startswith("北京,长沙")

    def test_empty_html(self, adapter):
        assert adapter.parse_list("<html><body></body></html>") == []

    def test_row_without_link_skipped(self, adapter):
        html = '<table><tr class="jobli"><td>没有链接</td><td>北京</td></tr></table>'
        assert adapter.parse_list(html) == []


class TestParseDetail:
    def test_fields(self, adapter, detail_html):
        data = adapter.parse_detail(detail_html)
        assert data.title == "岗位03-人工智能（AI）类研究岗（校招）"
        assert data.company == "[北京]中证金融研究院"
        assert data.city_raw == "北京"
        assert data.type_raw == "全职"
        assert data.category == "推荐算法工程师"
        assert data.date_raw == "2026-11-16"
        assert data.source_name == "前程无忧(51JOB)"

    def test_summary_extracted(self, adapter, detail_html):
        data = adapter.parse_detail(detail_html)
        assert "岗位要求" not in data.summary_raw[:10]
        assert "计算机" in data.summary_raw

    def test_apply_link(self, adapter, detail_html):
        data = adapter.parse_detail(detail_html)
        assert data.apply_info.startswith("在线申请：")
        assert "51job.com" in data.apply_info

    def test_minimal_html_does_not_raise(self, adapter, minimal_html):
        data = adapter.parse_detail(minimal_html)
        assert data.company == "某某科技有限公司"
        assert data.title == ""
        assert data.summary_raw == ""

    def test_garbage_html(self, adapter):
        data = adapter.parse_detail("<html><body>无关内容</body></html>")
        assert isinstance(data, DetailData)
        assert data.title == ""


class TestBuildJob:
    def test_full_record(self, adapter, list_html, detail_html):
        row = adapter.parse_list(list_html)[0]
        detail = adapter.parse_detail(detail_html)
        job = build_job(row, detail, "yingjiesheng", today=TODAY)

        assert job is not None
        assert job["company"] == "中证金融研究院"
        assert job["title"] == "岗位03-人工智能（AI）类研究岗（校招）"
        assert job["job_type"] == "campus"
        assert job["directions"] == ["算法"]
        assert job["cities"] == ["北京"]
        assert job["published_at"] == "2026-11-16"
        assert job["external_id"] == "008-150-254"
        assert job["dedupe_key"] == "中证金融研究院|岗位03-人工智能(ai)类研究岗(校招)|北京"

    def test_summary_truncated(self, adapter, list_html):
        row = adapter.parse_list(list_html)[0]
        detail = adapter.parse_detail(
            '<div class="combox"><div class="inf"><div class="el">岗位要求：'
            + ("很长的要求" * 100)
            + "</div></div></div>"
        )
        job = build_job(row, detail, "yingjiesheng", today=TODAY)
        assert job is not None
        assert len(job["summary"]) <= 301
        assert job["summary"].endswith("…")

    def test_missing_title_falls_back_to_row_title(self, adapter, list_html, minimal_html):
        row = adapter.parse_list(list_html)[0]
        detail = adapter.parse_detail(minimal_html)
        job = build_job(row, detail, "yingjiesheng", today=TODAY)
        assert job is not None
        assert job["title"].startswith("岗位03")

    def test_no_title_anywhere_returns_none(self, adapter):
        row = ListRow(url="https://example.com/job-1-1-1.html", raw_title="")
        detail = DetailData(title="", company="某某公司")
        assert build_job(row, detail, "yingjiesheng", today=TODAY) is None

    def test_city_falls_back_to_unknown(self, adapter, list_html):
        row = adapter.parse_list(list_html)[0]
        row.city_raw = ""
        detail = adapter.parse_detail('<div class="comtit"><h1>[北京]某公司</h1>'
                                      '<p class="sub"><label class="lab"><em>职位：</em>'
                                      '<b class="i">后端开发</b></label></p></div>')
        job = build_job(row, detail, "yingjiesheng", today=TODAY)
        assert job is not None
        assert job["cities"] == ["未知"]

    def test_directions_never_empty(self, adapter, list_html):
        row = adapter.parse_list(list_html)[2]  # 销售管培生
        detail = DetailData(title="销售管培生", company="[北京]某公司", city_raw="北京")
        job = build_job(row, detail, "yingjiesheng", today=TODAY)
        assert job is not None
        assert job["directions"] == ["其他"]


class TestCsScope:
    def test_other_only_is_not_cs(self):
        assert is_cs_job({"directions": ["其他"]}) is False

    def test_real_direction_is_cs(self):
        assert is_cs_job({"directions": ["后端"]}) is True
        assert is_cs_job({"directions": ["算法", "数据"]}) is True
        assert is_cs_job({"directions": ["其他", "测试"]}) is True

    def test_empty_directions_is_not_cs(self):
        assert is_cs_job({"directions": []}) is False
