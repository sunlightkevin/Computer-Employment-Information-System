"""归一化规则测试（离线）。"""
from __future__ import annotations

from datetime import date

import pytest

from collector.normalize import (
    build_dedupe_key,
    classify_directions,
    classify_job_type,
    clean_summary,
    extract_external_id,
    norm_text,
    parse_cities,
    parse_date,
    split_title_and_company,
    strip_city_prefix,
)

TODAY = date(2026, 11, 16)


class TestNormText:
    def test_fullwidth_and_case(self):
        assert norm_text("  ＪＡＶＡ   开发  ") == "java 开发"

    def test_none_and_blank(self):
        assert norm_text(None) == ""
        assert norm_text("   ") == ""

    def test_ideographic_space(self):
        assert norm_text("北京\u3000上海") == "北京 上海"


class TestParseCities:
    def test_comma_with_ellipsis(self):
        assert parse_cities("北京,长沙,湖南,...") == ["北京", "长沙", "湖南"]

    def test_multiple_separators(self):
        assert parse_cities("北京、上海/深圳，杭州") == ["北京", "上海", "深圳", "杭州"]

    def test_city_suffix_stripped(self):
        assert parse_cities("上海市") == ["上海"]
        assert parse_cities("北京市,深圳市") == ["北京", "深圳"]

    def test_remote_aliases(self):
        assert parse_cities("线上") == ["远程"]
        assert parse_cities("远程") == ["远程"]
        assert parse_cities("remote") == ["远程"]

    def test_dedupe_and_order(self):
        assert parse_cities("北京,上海,北京") == ["北京", "上海"]

    def test_empty(self):
        assert parse_cities(None) == []
        assert parse_cities("...") == []


class TestParseDate:
    def test_full_date(self):
        assert parse_date("2026-11-16", today=TODAY) == "2026-11-16"
        assert parse_date("2026/3/5", today=TODAY) == "2026-03-05"
        assert parse_date("2026年3月5日", today=TODAY) == "2026-03-05"

    def test_month_day_same_year(self):
        assert parse_date("11-16", today=TODAY) == "2026-11-16"

    def test_month_day_future_rolls_back_one_year(self):
        assert parse_date("12-01", today=TODAY) == "2025-12-01"

    def test_invalid(self):
        assert parse_date("近期", today=TODAY) is None
        assert parse_date("2026-13-01", today=TODAY) is None
        assert parse_date(None, today=TODAY) is None


class TestClassifyJobType:
    @pytest.mark.parametrize(
        "type_raw,title,expected",
        [
            ("实习", "后端开发", "intern"),
            ("全职", "测试实习生", "intern"),
            ("全职", "2027 校园招聘", "campus"),
            ("全职", "岗位03-人工智能（AI）类研究岗（校招）", "campus"),
            ("全职", "Java 开发工程师", "fulltime"),
            ("全职", "社会招聘 后端开发", "fulltime"),
            ("", "应届生 后端开发", "campus"),
        ],
    )
    def test_rules(self, type_raw, title, expected):
        assert classify_job_type(type_raw, title) == expected


class TestClassifyDirections:
    def test_single(self):
        assert classify_directions("推荐算法工程师", "岗位03") == ["算法"]

    def test_from_title(self):
        assert classify_directions("", "前端开发工程师") == ["前端"]

    def test_fallback_other(self):
        assert classify_directions("销售管培生", "销售管培生") == ["其他"]

    def test_multi_value_kept(self):
        result = classify_directions("大数据开发", "大数据开发工程师")
        assert "数据" in result
        assert len(result) >= 1

    def test_at_most_three(self):
        result = classify_directions("前后端算法数据测试运维", "全栈")
        assert len(result) <= 3

    def test_never_empty(self):
        assert classify_directions(None, None) == ["其他"]

    def test_ascii_boundary(self):
        # javascript 不应命中后端关键词 java
        assert "后端" not in classify_directions("javascript 工程师", "")

    def test_specific_keyword_beats_generic(self):
        # 「开发工程师」是通用词，不能把前端判成后端
        assert classify_directions("", "前端开发工程师") == ["前端"]
        assert classify_directions("", "测试开发工程师") == ["测试"]
        assert classify_directions("", "后端开发工程师") == ["后端"]

    def test_generic_keyword_used_when_alone(self):
        assert classify_directions("", "系统方向量化交易开发工程师") == ["后端"]


class TestCleanSummary:
    def test_under_limit_unchanged(self):
        text = "a" * 299
        assert clean_summary(text) == text

    def test_exactly_limit_unchanged(self):
        text = "b" * 300
        assert clean_summary(text) == text

    def test_over_limit_truncated(self):
        text = "c" * 301
        result = clean_summary(text)
        assert result.endswith("…")
        assert len(result) == 301
        assert result[:-1] == "c" * 300

    def test_blank_to_none(self):
        assert clean_summary("   ") is None
        assert clean_summary(None) is None

    def test_whitespace_compressed(self):
        assert clean_summary("前端\n\n\n开发") == "前端 开发"


class TestDedupeKey:
    def test_composition(self):
        assert build_dedupe_key("中证金融研究院", "研究岗", ["北京"]) == "中证金融研究院|研究岗|北京"

    def test_normalized_equivalence(self):
        a = build_dedupe_key("腾讯 科技", "后端 开发", ["北京"])
        b = build_dedupe_key("腾讯　科技", "后端  开发", ["北京"])
        assert a == b

    def test_city_sorted_first(self):
        assert build_dedupe_key("A", "B", ["上海", "北京"]) == "a|b|上海"

    def test_no_city(self):
        assert build_dedupe_key("A", "B", []) == "a|b|未知"


class TestHelpers:
    def test_extract_external_id(self):
        assert extract_external_id("https://www.yingjiesheng.com/job-008-150-254.html") == "008-150-254"
        assert extract_external_id("https://example.com/x.html") is None

    def test_strip_city_prefix(self):
        assert strip_city_prefix("[北京]中证金融研究院") == "中证金融研究院"
        assert strip_city_prefix("无前缀公司") == "无前缀公司"

    def test_split_title_and_company(self):
        title, company = split_title_and_company("[北京]中证金融研究院 岗位03-人工智能类")
        assert company == "中证金融研究院"
        assert title == "岗位03-人工智能类"

    def test_split_without_company(self):
        title, company = split_title_and_company("[北京]只有标题")
        assert title == "只有标题"
        assert company == ""
