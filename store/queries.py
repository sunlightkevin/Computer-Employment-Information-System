"""岗位写入（upsert）与基础查询。

写入一条岗位涉及 4 张表，必须在同一事务内完成：
jobs -> job_sources -> job_cities / job_directions
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

REQUIRED_FIELDS = ("dedupe_key", "title", "company", "job_type", "source_url")


class JobInvalid(ValueError):
    """岗位数据不满足最小写入条件。"""


def now_iso() -> str:
    """当前时间（本地时区，秒精度，ISO8601）。"""
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _validate(job: dict) -> None:
    for field in REQUIRED_FIELDS:
        value = job.get(field)
        if value is None or str(value).strip() == "":
            raise JobInvalid(f"缺少必填字段：{field}")
    if job["job_type"] not in ("intern", "campus", "fulltime"):
        raise JobInvalid(f"job_type 取值非法：{job['job_type']!r}")
    if not job.get("cities"):
        raise JobInvalid("缺少城市")
    if not job.get("directions"):
        raise JobInvalid("缺少方向")


def save_job(conn: sqlite3.Connection, job: dict, now: str | None = None) -> tuple[str, int]:
    """写入一条岗位。返回 ("added" | "updated", job_id)。

    整个过程在一个事务内：任一步失败则四张表均回滚。
    """
    _validate(job)
    now = now or now_iso()

    cities = list(dict.fromkeys(job["cities"]))
    directions = list(dict.fromkeys(job["directions"]))

    with conn:  # 事务开始
        row = conn.execute(
            "SELECT id FROM jobs WHERE dedupe_key = ?", (job["dedupe_key"],)
        ).fetchone()

        if row is None:
            cur = conn.execute(
                """
                INSERT INTO jobs (dedupe_key, title, company, job_type, summary,
                                  apply_info, published_at, first_seen_at, last_seen_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    job["dedupe_key"],
                    job["title"],
                    job["company"],
                    job["job_type"],
                    job.get("summary"),
                    job.get("apply_info"),
                    job.get("published_at"),
                    now,
                    now,
                ),
            )
            job_id = int(cur.lastrowid)
            result = "added"
        else:
            job_id = int(row["id"])
            conn.execute(
                """
                UPDATE jobs
                   SET title = ?, company = ?, job_type = ?, summary = ?,
                       apply_info = ?, published_at = ?, last_seen_at = ?
                 WHERE id = ?
                """,
                (
                    job["title"],
                    job["company"],
                    job["job_type"],
                    job.get("summary"),
                    job.get("apply_info"),
                    job.get("published_at"),
                    now,
                    job_id,
                ),
            )
            result = "updated"

        _upsert_source(conn, job_id, job, now)
        _replace_cities(conn, job_id, cities)
        _replace_directions(conn, job_id, directions)

    return result, job_id


def _upsert_source(conn: sqlite3.Connection, job_id: int, job: dict, now: str) -> None:
    existing = conn.execute(
        "SELECT id FROM job_sources WHERE source_site = ? AND source_url = ?",
        (job["source_site"], job["source_url"]),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO job_sources (job_id, source_site, source_url, external_id,
                                     url_ok, last_checked_at, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, 1, NULL, ?, ?)
            """,
            (job_id, job["source_site"], job["source_url"], job.get("external_id"), now, now),
        )
    else:
        conn.execute(
            """
            UPDATE job_sources
               SET job_id = ?, external_id = ?, last_seen_at = ?
             WHERE id = ?
            """,
            (job_id, job.get("external_id"), now, int(existing["id"])),
        )


def _replace_cities(conn: sqlite3.Connection, job_id: int, cities: list[str]) -> None:
    conn.execute("DELETE FROM job_cities WHERE job_id = ?", (job_id,))
    conn.executemany(
        "INSERT INTO job_cities (job_id, city) VALUES (?, ?)",
        [(job_id, city) for city in cities],
    )


def _replace_directions(conn: sqlite3.Connection, job_id: int, directions: list[str]) -> None:
    conn.execute("DELETE FROM job_directions WHERE job_id = ?", (job_id,))
    conn.executemany(
        "INSERT INTO job_directions (job_id, direction) VALUES (?, ?)",
        [(job_id, direction) for direction in directions],
    )


def count_jobs(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def count_sources(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0])
