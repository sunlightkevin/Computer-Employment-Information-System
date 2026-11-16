-- 岗位聚合工具数据库结构
-- 版本：1
-- 执行方式：由 store/db.py 在连接后调用 init_db() 执行，可重复执行。

PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key    TEXT    NOT NULL UNIQUE,
    title         TEXT    NOT NULL,
    company       TEXT    NOT NULL,
    job_type      TEXT    NOT NULL CHECK (job_type IN ('intern', 'campus', 'fulltime')),
    summary       TEXT,
    apply_info    TEXT,
    published_at  TEXT,
    first_seen_at TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'hidden'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_type_published ON jobs (job_type, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS job_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    source_site     TEXT    NOT NULL,
    source_url      TEXT    NOT NULL,
    external_id     TEXT,
    url_ok          INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    first_seen_at   TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL,
    UNIQUE (source_site, source_url)
);

CREATE INDEX IF NOT EXISTS idx_sources_job ON job_sources (job_id);

CREATE TABLE IF NOT EXISTS job_cities (
    job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    city   TEXT    NOT NULL,
    PRIMARY KEY (job_id, city)
);

CREATE INDEX IF NOT EXISTS idx_cities_city ON job_cities (city);

CREATE TABLE IF NOT EXISTS job_directions (
    job_id    INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    direction TEXT    NOT NULL,
    PRIMARY KEY (job_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_directions_direction ON job_directions (direction);
