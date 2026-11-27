"""51job 数据源测试：字典、JSON 解析、IT 过滤、类型判定、URL、CLI。"""
from __future__ import annotations

import json

import pytest

from collector.sources.job51 import (
    DEFAULT_KEYWORDS,
    FUNTYPE_DICT_URL,
    IT_MAJOR_CODE,
    JOB_AREA_HENAN,
    api_url,
    classify_job_type,
    is_it_job,
    is_it_title,
    load_it_codes,
    parse_api_json,
    search_page_url,
)


@pytest.fixture
def dict_json():
    return json.dumps(
        {
            "items": [
                {"code": "9F01", "value": "销售/客服", "items": [{"code": "3000", "value": "销售人员"}]},
                {
                    "code": IT_MAJOR_CODE,
                    "value": "互联网技术",
                    "items": [
                        {
                            "code": "0100",
                            "value": "后端开发",
                            "items": [{"code": "A0JQ", "value": "软件工程师"}],
                        },
                        {"code": "7300", "value": "人工智能"},
                    ],
                },
            ]
        },
        ensure_ascii=False,
    )


@pytest.fixture
def api_payload():
    return {
        "status": "1",
        "message": "成功",
        "resultbody": {
            "job": {
                "totalCount": 21,
                "items": [
                    {
                        "jobId": "172074298",
                        "jobName": "软件工程师",
                        "companyName": "中移铁通",
                        "jobDescribe": "岗位职责：写代码\n任职要求：本科",
                        "jobAreaLevelDetail": {
                            "cityString": "郑州",
                            "provinceString": "河南省",
                        },
                        "issueDateString": "2026-11-13 17:41:36",
                        "funcType1Code": "A0JQ",
                        "jobHref": "https://jobs.51job.com/zhengzhou/172074298.html",
                        "isIntern": False,
                        "termStr": "全职",
                        "jobTags": ["1-6年", "本科"],
                    },
                    {
                        "jobId": "999",
                        "jobName": "销售经理",
                        "companyName": "某公司",
                        "jobDescribe": "卖东西",
                        "jobAreaLevelDetail": {"cityString": "洛阳", "provinceString": "河南省"},
                        "issueDateString": "2026-11-01",
                        "funcType1Code": "3000",
                        "jobHref": "https://jobs.51job.com/luoyang/999.html",
                        "isIntern": False,
                        "termStr": "全职",
                        "jobTags": [],
                    },
                ],
            }
        },
    }


class TestLoadItCodes:
    def test_dynamic_load_all_descendants(self, dict_json):
        codes = load_it_codes(dict_json)
        assert {IT_MAJOR_CODE, "0100", "A0JQ", "7300"} <= codes
        assert "3000" not in codes
        assert "9F01" not in codes

    def test_dict_as_object(self, dict_json):
        codes = load_it_codes(json.loads(dict_json))
        assert "A0JQ" in codes

    def test_missing_items_raises(self):
        with pytest.raises(ValueError):
            load_it_codes({"foo": "bar"})


class TestParseApiJson:
    def test_fields_extracted(self, api_payload):
        rows, total = parse_api_json(api_payload)
        assert total == 21
        assert len(rows) == 2
        row = rows[0]
        assert row.job_id == "172074298"
        assert row.title == "软件工程师"
        assert row.company == "中移铁通"
        assert row.city == "郑州"
        assert row.province == "河南省"
        assert row.func_code == "A0JQ"
        assert row.published_at == "2026-11-13"
        assert "岗位职责" in row.summary
        assert row.tags == ["1-6年", "本科"]

    def test_string_payload(self, api_payload):
        rows, total = parse_api_json(json.dumps(api_payload, ensure_ascii=False))
        assert (len(rows), total) == (2, 21)

    def test_bad_status_raises(self):
        with pytest.raises(ValueError):
            parse_api_json({"status": "0", "message": "失败"})

    def test_missing_fields_skipped(self):
        payload = {
            "status": "1",
            "resultbody": {"job": {"items": [{"jobName": "", "companyName": "X"}], "totalCount": 1}},
        }
        rows, _ = parse_api_json(payload)
        assert rows == []

    def test_fallback_city_and_total(self):
        payload = {
            "status": "1",
            "resultbody": {
                "job": {
                    "items": [
                        {
                            "jobId": "1",
                            "jobName": "工程师",
                            "companyName": "X",
                            "jobAreaString": "洛阳",
                            "funcType1Code": "0100",
                            "jobHref": "https://x",
                        }
                    ],
                    "totalcount": "5",
                }
            },
        }
        rows, total = parse_api_json(payload)
        assert total == 5
        assert rows[0].city == "洛阳"
        assert rows[0].published_at is None


class TestTitleFilter:
    @pytest.mark.parametrize("title", [
        "高级软件工程师", "Java开发工程师", "测试工程师", "运维工程师",
        "大模型推理引擎研发工程师", "goland工程师", "数字智能化GIS系统专家",
    ])
    def test_it_titles_pass(self, title):
        assert is_it_title(title) is True

    @pytest.mark.parametrize("title", [
        "急招两名游戏陪练 会打游戏就行", "综合办公室文员(专利撰写方向)",
        "风电光伏运维工程师五险一金", "热电厂运维工程师", "仪器测试技术员",
        "数据标注", "资管经理-河南长沙", "项目经理",
    ])
    def test_non_it_titles_rejected(self, title):
        assert is_it_title(title) is False

    def test_blacklist_overrides_whitelist(self):
        # 「运维工程师」在白名单，但「热电厂」在黑名单，黑名单优先
        assert is_it_title("热电厂运维工程师") is False

    def test_case_insensitive(self):
        assert is_it_title("JAVA后端开发工程师") is True


class TestItFilter:
    def test_it_code_and_title_passes(self, api_payload, dict_json):
        rows, _ = parse_api_json(api_payload)
        codes = load_it_codes(dict_json)
        assert is_it_job(rows[0], codes) is True

    def test_non_it_code_rejected(self, api_payload, dict_json):
        rows, _ = parse_api_json(api_payload)
        codes = load_it_codes(dict_json)
        assert is_it_job(rows[1], codes) is False

    def test_it_code_but_non_it_title_rejected(self, api_payload, dict_json):
        """双重过滤：类别是 IT 但标题是游戏陪练，仍不入库。"""
        rows, _ = parse_api_json(api_payload)
        codes = load_it_codes(dict_json)
        rows[0].title = "急招两名游戏陪练 会打游戏就行"
        assert is_it_job(rows[0], codes) is False


class TestJobType:
    def test_intern_flag(self, api_payload):
        rows, _ = parse_api_json(api_payload)
        row = rows[0]
        row.is_intern = True
        assert classify_job_type(row) == "intern"

    def test_campus_keyword(self, api_payload):
        rows, _ = parse_api_json(api_payload)
        rows[0].title = "2027校园招聘软件工程师"
        assert classify_job_type(rows[0]) == "campus"

    def test_default_fulltime(self, api_payload):
        rows, _ = parse_api_json(api_payload)
        assert classify_job_type(rows[0]) == "fulltime"


class TestUrls:
    def test_search_page(self):
        url = search_page_url("人工智能")
        assert url.startswith("https://we.51job.com/pc/search")
        assert "keyword=%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD" in url
        assert "jobArea=170000" in url

    def test_api_url(self):
        url = api_url("人工智能", JOB_AREA_HENAN, 2, 1795695146)
        assert "/api/job/search-pc" in url
        assert "api_key=51job" in url
        assert "jobArea=170000" in url
        assert "pageNum=2" in url
        assert "pageSize=20" in url

    def test_funtype_dict_url(self):
        assert FUNTYPE_DICT_URL.endswith("dd_funtype_standard.json")


class TestDefaults:
    def test_keywords_count(self):
        assert len(DEFAULT_KEYWORDS) == 11
        assert "软件测试工程师" in DEFAULT_KEYWORDS
        assert "人工智能" in DEFAULT_KEYWORDS
        assert "嵌入式工程师" in DEFAULT_KEYWORDS


class TestCli:
    def test_source_51job(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(["--source", "51job"])
        assert args.source == "51job"
        assert args.job_area == "170000"
        assert args.max_pages == 0
        assert args.keywords is None

    def test_custom_keywords_and_pages(self):
        from collector.__main__ import build_parser

        args = build_parser().parse_args(
            ["--source", "51job", "--keywords", "人工智能,后端开发", "--max-pages", "1"]
        )
        assert args.keywords == "人工智能,后端开发"
        assert args.max_pages == 1
