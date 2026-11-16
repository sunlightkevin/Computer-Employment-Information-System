"""Web 层测试：列表、详情、可达性校验、空状态。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import PAGE_SIZE, create_app
from store import db as db_module
from store import queries

PREFIX = {"intern": "实习", "campus": "校招", "fulltime": "就业"}
SEED_COUNT = 60
BASE_DATE = date(2026, 1, 1)


def make_job(index: int, job_type: str = "intern", **overrides) -> dict:
    job = {
        "dedupe_key": f"公司{index}|{PREFIX[job_type]}{index:02d}|北京",
        "title": f"{PREFIX[job_type]}{index:02d}",
        "company": f"公司{index}",
        "job_type": job_type,
        "summary": f"摘要{index}",
        "apply_info": f"在线申请：https://example.com/apply/{index}",
        "published_at": (BASE_DATE + timedelta(days=index)).isoformat(),
        "source_site": "yingjiesheng",
        "source_url": f"https://www.yingjiesheng.com/job-{index:03d}.html",
        "external_id": f"{index:03d}",
        "cities": ["北京"],
        "directions": ["后端"],
    }
    job.update(overrides)
    return job


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = db_module.open_db(path)
    conn.close()
    return path


@pytest.fixture()
def client(db_path):
    application = create_app(str(db_path))
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client


@pytest.fixture()
def seeded(db_path):
    conn = db_module.open_db(db_path)
    for index in range(1, SEED_COUNT + 1):
        job_type = ("intern", "campus", "fulltime")[(index - 1) % 3]
        queries.save_job(conn, make_job(index, job_type))
    conn.close()
    return db_path


@pytest.fixture()
def seeded_client(seeded):
    application = create_app(str(seeded))
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client


class TestEmptyLibrary:
    def test_shows_collection_hint(self, client):
        response = client.get("/")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "本地库中还没有岗位" in body
        assert "python -m collector --source yingjiesheng" in body

    def test_does_not_render_blank_list(self, client):
        body = client.get("/").get_data(as_text=True)
        assert '<a class="card"' not in body


class TestList:
    def test_only_selected_category(self, seeded_client):
        body = seeded_client.get("/?type=campus").get_data(as_text=True)
        assert "校招59" in body
        assert "实习58" not in body
        assert "就业60" not in body

    def test_default_category_is_intern(self, seeded_client):
        body = seeded_client.get("/").get_data(as_text=True)
        assert "实习58" in body
        assert 'class="tab active"' in body

    def test_invalid_type_falls_back(self, seeded_client):
        response = seeded_client.get("/?type=unknown")
        assert response.status_code == 200
        assert "实习58" in response.get_data(as_text=True)

    def test_ordered_by_published_desc(self, seeded_client):
        body = seeded_client.get("/?type=intern").get_data(as_text=True)
        # intern 序号 1,4,...,58；发布日期随序号递增，倒序后 58 应排在 55 之前
        assert body.index("实习58") < body.index("实习55")

    def test_pagination_limits_page_size(self, seeded_client):
        body = seeded_client.get("/?type=fulltime").get_data(as_text=True)
        assert body.count('<a class="card"') == PAGE_SIZE

    def test_page_two_differs(self, seeded_client):
        page1 = seeded_client.get("/?type=intern&page=1").get_data(as_text=True)
        page2 = seeded_client.get("/?type=intern&page=2").get_data(as_text=True)
        assert page1 != page2
        assert "实习58" in page1
        assert "实习58" not in page2

    def test_page_beyond_range_clamped(self, seeded_client):
        response = seeded_client.get("/?type=intern&page=999")
        assert response.status_code == 200
        assert '<a class="card"' in response.get_data(as_text=True)

    def test_non_numeric_page(self, seeded_client):
        response = seeded_client.get("/?type=intern&page=abc")
        assert response.status_code == 200

    def test_empty_category_shows_state(self, db_path):
        conn = db_module.open_db(db_path)
        queries.save_job(conn, make_job(1, "intern"))
        conn.close()

        application = create_app(str(db_path))
        body = application.test_client().get("/?type=campus").get_data(as_text=True)
        assert "当前分类下没有岗位" in body

    def test_no_search_box_enabled(self, seeded_client):
        body = seeded_client.get("/").get_data(as_text=True)
        assert "P1" in body
        assert "disabled" in body


class TestDetail:
    def test_renders_fields(self, seeded, seeded_client):
        conn = db_module.open_db(seeded)
        job_id = conn.execute("SELECT id FROM jobs LIMIT 1").fetchone()[0]
        conn.close()

        body = seeded_client.get(f"/job/{job_id}").get_data(as_text=True)
        assert "岗位摘要" in body
        assert "投递方式" in body
        assert "查看原文" in body
        assert "来源信息" in body

    def test_missing_job_returns_404_with_message(self, seeded_client):
        response = seeded_client.get("/job/99999")
        assert response.status_code == 404
        assert "没有找到这条岗位" in response.get_data(as_text=True)

    def test_missing_apply_info_uses_fallback(self, db_path):
        conn = db_module.open_db(db_path)
        _, job_id = queries.save_job(conn, make_job(1, "intern", apply_info=None))
        conn.close()

        application = create_app(str(db_path))
        body = application.test_client().get(f"/job/{job_id}").get_data(as_text=True)
        assert "投递方式见原文" in body

    def test_missing_summary_uses_fallback(self, db_path):
        conn = db_module.open_db(db_path)
        _, job_id = queries.save_job(conn, make_job(1, "intern", summary=None))
        conn.close()

        application = create_app(str(db_path))
        body = application.test_client().get(f"/job/{job_id}").get_data(as_text=True)
        assert "未提供岗位描述" in body

    def test_unreachable_banner_visible_via_query(self, seeded, seeded_client):
        conn = db_module.open_db(seeded)
        job_id = conn.execute("SELECT id FROM jobs LIMIT 1").fetchone()[0]
        conn.close()

        body = seeded_client.get(f"/job/{job_id}?unreachable=1").get_data(as_text=True)
        assert "该岗位原文可能已不存在" in body


class TestLinkCheck:
    def test_reachable_keeps_url_ok(self, seeded, seeded_client, monkeypatch):
        monkeypatch.setattr("linkcheck.is_reachable", lambda url, timeout=3.0: True)

        conn = db_module.open_db(seeded)
        row = conn.execute("SELECT id, job_id FROM job_sources LIMIT 1").fetchone()
        source_id, job_id = row["id"], row["job_id"]
        conn.close()

        response = seeded_client.post(f"/job/{job_id}/check")
        assert response.status_code == 200
        assert response.get_json()["ok"] is True

        conn = db_module.open_db(seeded)
        assert conn.execute("SELECT url_ok FROM job_sources WHERE id=?", (source_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT last_checked_at FROM job_sources WHERE id=?", (source_id,)).fetchone()[0]
        conn.close()

    def test_unreachable_sets_url_ok_zero(self, seeded, seeded_client, monkeypatch):
        monkeypatch.setattr("linkcheck.is_reachable", lambda url, timeout=3.0: False)

        conn = db_module.open_db(seeded)
        row = conn.execute("SELECT id, job_id FROM job_sources LIMIT 1").fetchone()
        source_id, job_id = row["id"], row["job_id"]
        conn.close()

        response = seeded_client.post(f"/job/{job_id}/check")
        assert response.get_json()["ok"] is False

        conn = db_module.open_db(seeded)
        assert conn.execute("SELECT url_ok FROM job_sources WHERE id=?", (source_id,)).fetchone()[0] == 0
        conn.close()

    def test_check_missing_job(self, seeded_client):
        assert seeded_client.post("/job/424242/check").status_code == 404


class TestGoFallback:
    def test_reachable_redirects_to_source(self, seeded, seeded_client, monkeypatch):
        monkeypatch.setattr("linkcheck.is_reachable", lambda url, timeout=3.0: True)
        conn = db_module.open_db(seeded)
        job_id = conn.execute("SELECT job_id FROM job_sources LIMIT 1").fetchone()[0]
        source_url = conn.execute("SELECT source_url FROM job_sources LIMIT 1").fetchone()[0]
        conn.close()

        response = seeded_client.get(f"/job/{job_id}/go")
        assert response.status_code == 302
        assert response.headers["Location"] == source_url

    def test_unreachable_redirects_back_with_flag(self, seeded, seeded_client, monkeypatch):
        monkeypatch.setattr("linkcheck.is_reachable", lambda url, timeout=3.0: False)
        conn = db_module.open_db(seeded)
        job_id = conn.execute("SELECT job_id FROM job_sources LIMIT 1").fetchone()[0]
        conn.close()

        response = seeded_client.get(f"/job/{job_id}/go")
        assert response.status_code == 302
        assert "unreachable=1" in response.headers["Location"]


class TestNoAuth:
    def test_no_login_route(self, seeded_client):
        assert seeded_client.get("/login").status_code == 404

    def test_all_pages_reachable_without_session(self, seeded, seeded_client):
        conn = db_module.open_db(seeded)
        job_id = conn.execute("SELECT id FROM jobs LIMIT 1").fetchone()[0]
        conn.close()
        assert seeded_client.get("/").status_code == 200
        assert seeded_client.get(f"/job/{job_id}").status_code == 200
