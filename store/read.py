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


def _like_pattern(keyword: str) -> str:
    """把关键词转成 LIKE 模式。

    必须转义 % 和 _，否则搜 `100%` 会变成「匹配任何内容」。
    """
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
        like = _like_pattern(keyword)
        clauses.append(
            "(j.title LIKE ? ESCAPE '\\' OR j.company LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM job_directions d2 "
            "WHERE d2.job_id = j.id AND d2.direction LIKE ? ESCAPE '\\'))"
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


FACET_COLUMNS = {"city": ("job_cities", "city"), "direction": ("job_directions", "direction")}


def facet_counts(
    conn: sqlite3.Connection,
    dimension: str,
    job_type: str | None = None,
    cities: list[str] | None = None,
    directions: list[str] | None = None,
    keyword: str | None = None,
    limit: int | None = None,
    pinned: list[str] | None = None,
) -> list[dict]:
    """筛选区的可选值与数量。

    数量按「其他维度的当前条件」统计：选了城市之后，方向上的数字反映的是
    这些城市里的分布，而不是全库分布。否则会出现「筛完只剩 0 条，选项上
    却还写着 45」的自相矛盾。

    本维度自己的条件不参与统计，否则勾上之后其余选项会被清零。
    """
    if dimension not in FACET_COLUMNS:
        raise ValueError(f"未知维度：{dimension}")
    table, column = FACET_COLUMNS[dimension]
    if dimension == "city":
        where, params = _where(job_type, [], directions or [], keyword)
    else:
        where, params = _where(job_type, cities or [], [], keyword)

    sql = (
        f"SELECT f.{column} AS value, COUNT(*) AS count "
        f"FROM {table} f JOIN jobs j ON j.id = f.job_id "
        f"WHERE {where} GROUP BY f.{column} ORDER BY count DESC, value"
    )
    rows = [{"value": r["value"], "count": int(r["count"])} for r in conn.execute(sql, params)]

    if not pinned:
        return rows[:limit] if limit else rows

    # 固定值排在最前（按给定顺序），即使当前分类下数量为 0 也要出现，
    # 否则郑州这类小量城市会在别的分类里从筛选项中消失。
    by_value = {r["value"]: r for r in rows}
    result = [by_value.pop(value, {"value": value, "count": 0}) for value in pinned if value]
    # 其余按数量倒序补齐；置顶值已从 by_value 移除，不会重复
    rest = sorted(by_value.values(), key=lambda r: (-r["count"], r["value"]))
    result.extend(rest)
    return result[:limit] if limit else result


def mark_source_reachability(conn: sqlite3.Connection, source_id: int, reachable: bool, checked_at: str) -> None:
    with conn:
        conn.execute(
            "UPDATE job_sources SET url_ok = ?, last_checked_at = ? WHERE id = ?",
            (1 if reachable else 0, checked_at, source_id),
        )
