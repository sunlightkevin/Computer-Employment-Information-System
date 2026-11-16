"""页面读取查询。

与 store/queries.py 分工：那个模块负责写入（upsert），本模块负责读取。
列表查询一律用 EXISTS 而不是 JOIN 多值表，避免重复行导致分页错位。
"""
from __future__ import annotations

import sqlite3

DEFAULT_PAGE_SIZE = 10
JOB_TYPES = ("intern", "campus", "fulltime")


def _group_multi(conn: sqlite3.Connection, rows: list[sqlite3.Row], table: str, column: str) -> dict[int, list[str]]:
    """批量取多值字段，避免逐条查询。"""
    if not rows:
        return {}
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(ids))
    grouped: dict[int, list[str]] = {job_id: [] for job_id in ids}
    for record in conn.execute(
        f"SELECT job_id, {column} AS value FROM {table} WHERE job_id IN ({placeholders}) ORDER BY {column}",
        ids,
    ):
        grouped[record["job_id"]].append(record["value"])
    return grouped


def _attach_multi(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    cities = _group_multi(conn, rows, "job_cities", "city")
    directions = _group_multi(conn, rows, "job_directions", "direction")
    items = []
    for row in rows:
        item = dict(row)
        item["cities"] = cities.get(row["id"], [])
        item["directions"] = directions.get(row["id"], [])
        items.append(item)
    return items


def _where(job_type: str | None, cities: list[str], directions: list[str], keyword: str | None):
    clauses = ["j.status = 'active'"]
    params: list = []

    if job_type:
        clauses.append("j.job_type = ?")
        params.append(job_type)

    if cities:
        placeholders = ",".join("?" * len(cities))
        clauses.append(
            f"EXISTS (SELECT 1 FROM job_cities c WHERE c.job_id = j.id AND c.city IN ({placeholders}))"
        )
        params.extend(cities)

    if directions:
        placeholders = ",".join("?" * len(directions))
        clauses.append(
            f"EXISTS (SELECT 1 FROM job_directions d WHERE d.job_id = j.id AND d.direction IN ({placeholders}))"
        )
        params.extend(directions)

    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            "(j.title LIKE ? OR j.company LIKE ? "
            "OR EXISTS (SELECT 1 FROM job_directions d2 WHERE d2.job_id = j.id AND d2.direction LIKE ?))"
        )
        params.extend([like, like, like])

    return " AND ".join(clauses), params


def count_jobs(
    conn: sqlite3.Connection,
    job_type: str | None = None,
    cities: list[str] | None = None,
    directions: list[str] | None = None,
    keyword: str | None = None,
) -> int:
    where, params = _where(job_type, cities or [], directions or [], keyword)
    return int(conn.execute(f"SELECT COUNT(*) FROM jobs j WHERE {where}", params).fetchone()[0])


def list_jobs(
    conn: sqlite3.Connection,
    job_type: str | None = None,
    cities: list[str] | None = None,
    directions: list[str] | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict]:
    """按条件取一页岗位：发布时间倒序，缺失的排最后。"""
    page = max(1, int(page))
    offset = (page - 1) * page_size
    where, params = _where(job_type, cities or [], directions or [], keyword)

    sql = (
        "SELECT j.id, j.title, j.company, j.job_type, j.summary, j.apply_info, j.published_at "
        f"FROM jobs j WHERE {where} "
        "ORDER BY j.published_at IS NULL, j.published_at DESC, j.id DESC "
        "LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, params + [page_size, offset]).fetchall()
    return _attach_multi(conn, rows)


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None

    job = dict(row)
    job["cities"] = [
        r["city"] for r in conn.execute("SELECT city FROM job_cities WHERE job_id = ? ORDER BY city", (job_id,))
    ]
    job["directions"] = [
        r["direction"]
        for r in conn.execute(
            "SELECT direction FROM job_directions WHERE job_id = ? ORDER BY direction", (job_id,)
        )
    ]
    job["sources"] = [
        dict(r)
        for r in conn.execute(
            "SELECT id, source_site, source_url, external_id, url_ok, last_checked_at "
            "FROM job_sources WHERE job_id = ? ORDER BY last_seen_at DESC, id DESC",
            (job_id,),
        )
    ]
    return job


def primary_source(job: dict | None) -> dict | None:
    if not job or not job.get("sources"):
        return None
    return job["sources"][0]


def category_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {name: 0 for name in JOB_TYPES}
    for row in conn.execute(
        "SELECT job_type, COUNT(*) AS n FROM jobs WHERE status = 'active' GROUP BY job_type"
    ):
        counts[row["job_type"]] = int(row["n"])
    return counts


def total_jobs(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'active'").fetchone()[0])


def distinct_values(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    """筛选区可选项：取库中实际存在的值，按数量倒序。"""
    rows = conn.execute(
        f"SELECT {column} AS v, COUNT(*) AS n FROM {table} GROUP BY {column} ORDER BY n DESC, v"
    )
    return [r["v"] for r in rows]


def value_counts(conn: sqlite3.Connection, table: str, column: str) -> list[dict]:
    """带数量的可选值，按数量倒序。"""
    rows = conn.execute(
        f"SELECT {column} AS value, COUNT(*) AS count FROM {table} GROUP BY {column} ORDER BY count DESC, value"
    )
    return [{"value": r["value"], "count": int(r["count"])} for r in rows]


def mark_source_reachability(conn: sqlite3.Connection, source_id: int, reachable: bool, checked_at: str) -> None:
    with conn:
        conn.execute(
            "UPDATE job_sources SET url_ok = ?, last_checked_at = ? WHERE id = ?",
            (1 if reachable else 0, checked_at, source_id),
        )
