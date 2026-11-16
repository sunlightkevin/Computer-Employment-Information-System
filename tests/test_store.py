"""写库与事务测试（离线，使用内存库）。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from store import queries
from store.db import connect, init_db, open_db, user_version

NOW = "2026-11-16 10:00:00"


def make_job(**overrides) -> dict:
    job = {
        "dedupe_key": "腾讯|后端开发|北京",
        "title": "后端开发",
        "company": "腾讯",
        "job_type": "campus",
        "summary": "负责服务开发",
        "apply_info": "在线申请：https://example.com/apply",
        "published_at": "2026-11-15",
        "source_site": "yingjiesheng",
        "source_url": "https://www.yingjiesheng.com/job-001-000-001.html",
        "external_id": "001-000-001",
        "cities": ["北京"],
        "directions": ["后端"],
    }
    job.update(overrides)
    return job


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    init_db(connection)
    yield connection
    connection.close()


class TestSchema:
    def test_tables_created(self, conn):
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert {"jobs", "job_sources", "job_cities", "job_directions"} <= names

    def test_init_is_idempotent(self, conn):
        init_db(conn)
        init_db(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_indexes_created(self, conn):
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_jobs_type_published" in names
        assert "idx_cities_city" in names
        assert "idx_directions_direction" in names

    def test_foreign_keys_on(self, conn):
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


class TestSaveJob:
    def test_insert_then_update(self, conn):
        outcome, job_id = queries.save_job(conn, make_job(), now=NOW)
        assert outcome == "added"

        outcome2, job_id2 = queries.save_job(conn, make_job(summary="改过的摘要"), now="2026-11-17 10:00:00")
        assert outcome2 == "updated"
        assert job_id2 == job_id
        assert queries.count_jobs(conn) == 1
        assert queries.count_sources(conn) == 1
        assert conn.execute("SELECT summary FROM jobs").fetchone()[0] == "改过的摘要"

    def test_cities_and_directions_written(self, conn):
        queries.save_job(conn, make_job(cities=["北京", "上海"], directions=["后端", "数据"]), now=NOW)
        cities = {r[0] for r in conn.execute("SELECT city FROM job_cities")}
        directions = {r[0] for r in conn.execute("SELECT direction FROM job_directions")}
        assert cities == {"北京", "上海"}
        assert directions == {"后端", "数据"}

    def test_directions_replaced_not_accumulated(self, conn):
        queries.save_job(conn, make_job(directions=["后端", "数据"]), now=NOW)
        queries.save_job(conn, make_job(directions=["前端"]), now=NOW)
        directions = [r[0] for r in conn.execute("SELECT direction FROM job_directions")]
        assert directions == ["前端"]

    def test_duplicate_cities_ignored(self, conn):
        queries.save_job(conn, make_job(cities=["北京", "北京"]), now=NOW)
        assert conn.execute("SELECT COUNT(*) FROM job_cities").fetchone()[0] == 1

    def test_same_url_twice_keeps_one_source(self, conn):
        queries.save_job(conn, make_job(), now=NOW)
        queries.save_job(conn, make_job(), now=NOW)
        assert queries.count_sources(conn) == 1

    def test_first_seen_preserved_on_update(self, conn):
        queries.save_job(conn, make_job(), now=NOW)
        queries.save_job(conn, make_job(), now="2026-11-20 10:00:00")
        row = conn.execute("SELECT first_seen_at, last_seen_at FROM jobs").fetchone()
        assert row["first_seen_at"] == NOW
        assert row["last_seen_at"] == "2026-11-20 10:00:00"


class TestValidation:
    def test_missing_title(self, conn):
        with pytest.raises(queries.JobInvalid):
            queries.save_job(conn, make_job(title="  "), now=NOW)

    def test_missing_company(self, conn):
        with pytest.raises(queries.JobInvalid):
            queries.save_job(conn, make_job(company=""), now=NOW)

    def test_bad_job_type(self, conn):
        with pytest.raises(queries.JobInvalid):
            queries.save_job(conn, make_job(job_type="unknown"), now=NOW)

    def test_no_cities(self, conn):
        with pytest.raises(queries.JobInvalid):
            queries.save_job(conn, make_job(cities=[]), now=NOW)

    def test_no_directions(self, conn):
        with pytest.raises(queries.JobInvalid):
            queries.save_job(conn, make_job(directions=[]), now=NOW)


class TestTransaction:
    def test_rollback_leaves_no_rows(self, conn, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("模拟写入方向失败")

        monkeypatch.setattr(queries, "_replace_directions", boom)
        with pytest.raises(RuntimeError):
            queries.save_job(conn, make_job(), now=NOW)

        assert queries.count_jobs(conn) == 0
        assert queries.count_sources(conn) == 0
        assert conn.execute("SELECT COUNT(*) FROM job_cities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_directions").fetchone()[0] == 0

    def test_cascade_delete(self, conn):
        _, job_id = queries.save_job(conn, make_job(), now=NOW)
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_cities").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_directions").fetchone()[0] == 0

    def test_orphan_source_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO job_sources (job_id, source_site, source_url, first_seen_at, last_seen_at)"
                " VALUES (9999, 'x', 'y', ?, ?)",
                (NOW, NOW),
            )


class TestOpenDb:
    def test_file_db_created(self, tmp_path):
        path = tmp_path / "nested" / "jobs.db"
        conn = open_db(path)
        try:
            assert path.exists()
            assert user_version(conn) == 1
        finally:
            conn.close()
