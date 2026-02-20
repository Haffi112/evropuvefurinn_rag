import logging

import asyncpg
from pgvector.asyncpg import register_vector

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

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
    embedding   vector(1024),
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

CREATE INDEX IF NOT EXISTS idx_articles_embedding_hnsw
    ON articles USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE TABLE IF NOT EXISTS query_log (
    id              BIGSERIAL PRIMARY KEY,
    query_text      TEXT NOT NULL,
    response_text   TEXT,
    model_used      TEXT,
    "references"    JSONB NOT NULL DEFAULT '[]',
    scope_declined  BOOLEAN NOT NULL DEFAULT FALSE,
    cached          BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms      INTEGER,
    ip_address      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_query_log_created_at ON query_log (created_at DESC);

CREATE TABLE IF NOT EXISTS review_users (
    id          SERIAL PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_evaluations (
    id            BIGSERIAL PRIMARY KEY,
    query_log_id  BIGINT NOT NULL REFERENCES query_log(id) UNIQUE,
    reviewer_id   INT NOT NULL REFERENCES review_users(id),
    checklist     JSONB NOT NULL,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reviewed_articles (
    id              BIGSERIAL PRIMARY KEY,
    query_log_id    BIGINT NOT NULL REFERENCES query_log(id),
    reviewer_id     INT NOT NULL REFERENCES review_users(id),
    version         INT NOT NULL DEFAULT 1,
    title           TEXT NOT NULL,
    edited_response TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

MIGRATION_SQL = """
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'articles' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE articles ADD COLUMN embedding vector(1024);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_articles_embedding_hnsw
    ON articles USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

ALTER TABLE query_log ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'pending';
"""


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def init_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    # Ensure pgvector extension exists before pool init registers the type
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()

    _pool = await asyncpg.create_pool(
        database_url, min_size=2, max_size=10, init=_init_connection,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(MIGRATION_SQL)
    logger.info("Database pool initialized, schema ensured (pgvector enabled)")
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
