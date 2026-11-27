"""51job 岗位与现有应届生数据共存测试。"""
from __future__ import annotations

import pytest

from collector.job51_collect import _build_job
from collector.sources.job51 import Job51Row, load_it_codes, parse_api_json
from store import db as db_module
from store import queries

IT_CODES = {"9F02", "0100", "A0JQ", "7300"}


def make_row(**overrides) -> Job51Row:
    base = dict(
        job_id="12345",
        title="软件工程师",
        company="中移铁通",
        summary="岗位职责：写 Java 代码",
        city="郑州",
        province="河南省",
        published_at="2026-11-13",
        func_code="A0JQ",
        source_url="https://jobs.51job.com/zhengzhou/12345.html",
        is_intern=False,
        term="全职",
        tags=["1-6年", "本科"],
    )
    base.update(overrides)
    return Job51Row(**base)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


class TestBuildJob:
    def test_it_row_builds(self):
        job, skip = _build_job(make_row(), IT_CODES)
        assert skip is None
        assert job["source_site"] == "51job"
        assert job["cities"] == ["郑州"]
        assert job["job_type"] == "fulltime"
        assert job["dedupe_key"].endswith("|郑州")
        assert "后端" in job["directions"] or "其他" in job["directions"]

    def test_non_it_row_skipped(self):
        job, skip = _build_job(make_row(func_code="3000"), IT_CODES)
        assert job is None
        assert skip == "non_it"

    def test_missing_url_skipped(self):
        job, skip = _build_job(make_row(source_url=""), IT_CODES)
        assert job is None
        assert skip == "invalid"


class TestCoexist:
    def test_51job_and_yingjiesheng_coexist(self, db_path):
        conn = db_module.open_db(db_path)
        # 应届生岗位
        queries.save_job(conn, {
            "dedupe_key": "老公司|老岗位|北京",
            "title": "老岗位", "company": "老公司", "job_type": "intern",
            "summary": "旧数据", "source_site": "yingjiesheng",
            "source_url": "https://www.yingjiesheng.com/job-old.html",
            "external_id": "old", "cities": ["北京"], "directions": ["后端"],
        })
        # 51job 岗位
        job, _ = _build_job(make_row(), IT_CODES)
        outcome, _ = queries.save_job(conn, job)
        assert outcome == "added"
        assert queries.count_jobs(conn) == 2
        conn.close()

    def test_51job_upsert_updates_not_duplicates(self, db_path):
        conn = db_module.open_db(db_path)
        job, _ = _build_job(make_row(), IT_CODES)
        first, _ = queries.save_job(conn, job)
        second, _ = queries.save_job(conn, job)
        assert first == "added"
        assert second == "updated"
        assert queries.count_jobs(conn) == 1
        conn.close()
