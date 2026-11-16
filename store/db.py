"""数据库连接与建表。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = SRC_DIR / "schema.sql"
DEFAULT_DB_PATH = SRC_DIR / "data" / "jobs.db"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开连接：开启外键约束，返回 Row 可用的连接。"""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表与索引。可重复执行，不破坏已有数据。"""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def open_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """连接并确保表已建立。"""
    conn = connect(db_path)
    init_db(conn)
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])
