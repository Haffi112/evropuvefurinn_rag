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
    author      TEXT,
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
    reviewer_id     BIGINT,
    mode            TEXT NOT NULL DEFAULT 'rag',  -- 'rag' | 'websearch'
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
    id               BIGSERIAL PRIMARY KEY,
    query_log_id     BIGINT NOT NULL REFERENCES query_log(id) UNIQUE,
    reviewer_id      INT NOT NULL REFERENCES review_users(id),
    checklist        JSONB NOT NULL,
    note             TEXT,
    duration_seconds INT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ
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

CREATE TABLE IF NOT EXISTS query_batches (
    id           BIGSERIAL PRIMARY KEY,
    filename     TEXT NOT NULL,
    total        INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'cancelled'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS flagged_references (
    id            BIGSERIAL PRIMARY KEY,
    -- Exactly one of article_id or url is set:
    --   article_id: RAG reference (foreign key to articles)
    --   url:        web-search reference (arbitrary URL)
    article_id    TEXT REFERENCES articles(id) ON DELETE CASCADE,
    url           TEXT,
    -- 'outdated' (RAG), 'irrelevant' (web), 'untrustworthy' (web)
    flag_type     TEXT NOT NULL DEFAULT 'outdated',
    reviewer_id   INT NOT NULL REFERENCES review_users(id),
    query_log_id  BIGINT REFERENCES query_log(id) ON DELETE SET NULL,
    reason        TEXT,
    resolved_at   TIMESTAMPTZ,
    resolved_by   INT REFERENCES review_users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT flagged_references_identifier_xor
        CHECK ((article_id IS NOT NULL) <> (url IS NOT NULL)),
    CONSTRAINT flagged_references_flag_type_check
        CHECK (flag_type IN ('outdated', 'irrelevant', 'untrustworthy'))
);
-- One OPEN flag per reviewer per identifier (article_id OR url).
-- Two partial indexes so each identifier type is enforced independently.
CREATE UNIQUE INDEX IF NOT EXISTS idx_flagged_references_unique_article_open
    ON flagged_references (article_id, reviewer_id)
    WHERE article_id IS NOT NULL AND resolved_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flagged_references_unique_url_open
    ON flagged_references (url, reviewer_id)
    WHERE url IS NOT NULL AND resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_flagged_references_article
    ON flagged_references (article_id) WHERE article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_flagged_references_url
    ON flagged_references (url) WHERE url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_flagged_references_created
    ON flagged_references (created_at DESC);

CREATE TABLE IF NOT EXISTS batch_items (
    id             BIGSERIAL PRIMARY KEY,
    batch_id       BIGINT NOT NULL REFERENCES query_batches(id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL,
    question_text  TEXT NOT NULL,
    mode           TEXT NOT NULL,  -- 'rag' | 'websearch'
    status         TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'processing' | 'done' | 'failed' | 'cancelled'
    query_log_id   BIGINT REFERENCES query_log(id),
    error          TEXT,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id ON batch_items (batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_pending ON batch_items (status) WHERE status = 'pending';
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

ALTER TABLE articles ALTER COLUMN author DROP NOT NULL;

ALTER TABLE query_log ADD COLUMN IF NOT EXISTS reviewer_id BIGINT REFERENCES review_users(id);

ALTER TABLE query_log ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'rag';
UPDATE query_log
SET mode = 'websearch'
WHERE mode = 'rag' AND model_used LIKE '%:online';

ALTER TABLE review_evaluations ADD COLUMN IF NOT EXISTS duration_seconds INT;

-- Backfill new checklist key so existing evaluations show false (not missing)
-- for the 'publishable_minor_edits' criterion introduced after launch.
UPDATE review_evaluations
SET checklist = checklist || '{"publishable_minor_edits": false}'::jsonb
WHERE NOT (checklist ? 'publishable_minor_edits');

-- Web-reference flagging: extend flagged_references to also flag URLs
-- (not just article IDs) and to carry a flag_type (outdated/irrelevant/untrustworthy).
ALTER TABLE flagged_references ALTER COLUMN article_id DROP NOT NULL;
ALTER TABLE flagged_references ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE flagged_references ADD COLUMN IF NOT EXISTS flag_type TEXT NOT NULL DEFAULT 'outdated';

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'flagged_references_identifier_xor'
    ) THEN
        ALTER TABLE flagged_references ADD CONSTRAINT flagged_references_identifier_xor
            CHECK ((article_id IS NOT NULL) <> (url IS NOT NULL));
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'flagged_references_flag_type_check'
    ) THEN
        ALTER TABLE flagged_references ADD CONSTRAINT flagged_references_flag_type_check
            CHECK (flag_type IN ('outdated', 'irrelevant', 'untrustworthy'));
    END IF;
END $$;

-- Replace the old single unique index with two type-specific ones
DROP INDEX IF EXISTS idx_flagged_references_unique_open;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flagged_references_unique_article_open
    ON flagged_references (article_id, reviewer_id)
    WHERE article_id IS NOT NULL AND resolved_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_flagged_references_unique_url_open
    ON flagged_references (url, reviewer_id)
    WHERE url IS NOT NULL AND resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_flagged_references_url
    ON flagged_references (url) WHERE url IS NOT NULL;
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
