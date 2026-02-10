import logging

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    date        TEXT NOT NULL,
    author      TEXT NOT NULL,
    categories  TEXT[] NOT NULL DEFAULT '{}',
    tags        TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS query_cache (
    query_hash      TEXT PRIMARY KEY,
    query_text      TEXT NOT NULL,
    response_json   JSONB NOT NULL,
    article_ids_used TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_query_cache_article_ids
    ON query_cache USING GIN (article_ids_used);

CREATE INDEX IF NOT EXISTS idx_query_cache_expires
    ON query_cache (expires_at);

CREATE TABLE IF NOT EXISTS daily_quota (
    model_id    TEXT NOT NULL,
    date        DATE NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (model_id, date)
);
"""


async def init_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("Database pool initialized, schema ensured")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialized")
    return _pool
