"""
Backfill chunk-level embeddings (article_chunks) for hybrid retrieval.

Chunks every article's answer into overlapping word windows (see
app/services/embedding_service.py for the geometry and rationale), embeds each
chunk with the title+question prefix, and stores one row per chunk. Skips
articles that already have chunks, so the script is resumable; pass --force to
re-chunk everything (e.g. after changing the chunk size).

Usage:
    python scripts/backfill_chunks.py                  # reads from .env
    python scripts/backfill_chunks.py --force
    python scripts/backfill_chunks.py --database-url URL --api-key KEY
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import asyncpg
import httpx
import numpy as np
from pgvector.asyncpg import register_vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.embedding_service import EmbeddingService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEEPINFRA_EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings"
# Max texts per embedding API call (an article's chunks are never split
# across calls, so this is a soft cap checked between articles).
EMBED_BATCH_TEXTS = 64

SETUP_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS article_chunks (
    id          BIGSERIAL PRIMARY KEY,
    article_id  TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_article_chunks_embedding_hnsw
    ON article_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_article_chunks_article
    ON article_chunks (article_id);
"""


async def embed_batch(client: httpx.AsyncClient, texts: list[str], model: str) -> list[list[float]]:
    prefixed = ["passage: " + t for t in texts]
    resp = await client.post(
        DEEPINFRA_EMBED_URL,
        json={"model": model, "input": prefixed, "encoding_format": "float"},
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda x: x["index"])
    return [d["embedding"] for d in data]


async def write_chunks(conn: asyncpg.Connection, article_id: str,
                       chunk_texts: list[str], embeddings: list[list[float]]) -> None:
    async with conn.transaction():
        await conn.execute("DELETE FROM article_chunks WHERE article_id = $1", article_id)
        for i, (chunk, emb) in enumerate(zip(chunk_texts, embeddings)):
            await conn.execute(
                """
                INSERT INTO article_chunks (article_id, chunk_index, content, embedding)
                VALUES ($1, $2, $3, $4)
                """,
                article_id, i, chunk, np.array(emb, dtype=np.float32),
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-chunk articles that already have chunk rows")
    args = parser.parse_args()

    # Fall back to .env via the app's Settings (same precedence as the API)
    if not (args.database_url and args.api_key and args.model):
        from app.config import get_settings
        settings = get_settings()
        args.database_url = args.database_url or settings.database_url
        args.api_key = args.api_key or settings.deepinfra_api_key
        args.model = args.model or settings.deepinfra_model
    if not args.api_key:
        sys.exit("No DeepInfra API key (set DEEPINFRA_API_KEY or pass --api-key)")

    conn = await asyncpg.connect(args.database_url)
    await conn.execute(SETUP_SQL)
    await register_vector(conn)

    if args.force:
        rows = await conn.fetch(
            "SELECT id, title, question, answer FROM articles ORDER BY id"
        )
    else:
        rows = await conn.fetch(
            """
            SELECT a.id, a.title, a.question, a.answer
            FROM articles a
            WHERE NOT EXISTS (
                SELECT 1 FROM article_chunks c WHERE c.article_id = a.id
            )
            ORDER BY a.id
            """
        )
    logger.info("Articles to chunk: %d (force=%s)", len(rows), args.force)
    if not rows:
        await conn.close()
        return

    client = httpx.AsyncClient(
        timeout=60.0,
        headers={"Authorization": f"Bearer {args.api_key}",
                 "Content-Type": "application/json"},
    )

    done = total_chunks = 0
    start = time.monotonic()
    pending: list[tuple[str, list[str]]] = []  # (article_id, chunk_texts)
    pending_texts = 0

    async def flush() -> None:
        nonlocal done, total_chunks, pending, pending_texts
        if not pending:
            return
        texts = [t for _, chunks in pending for t in chunks]
        embeddings = await embed_batch(client, texts, args.model)
        offset = 0
        for article_id, chunks in pending:
            await write_chunks(conn, article_id, chunks, embeddings[offset : offset + len(chunks)])
            offset += len(chunks)
            done += 1
            total_chunks += len(chunks)
        pending, pending_texts = [], 0
        elapsed = time.monotonic() - start
        logger.info("Progress: %d/%d articles, %d chunks, %.0fs elapsed",
                    done, len(rows), total_chunks, elapsed)

    for row in rows:
        chunk_texts = EmbeddingService._build_chunk_texts(
            row["title"], row["question"], row["answer"]
        )
        if not chunk_texts:
            logger.warning("Article %s has an empty answer — skipped", row["id"])
            done += 1
            continue
        if pending and pending_texts + len(chunk_texts) > EMBED_BATCH_TEXTS:
            await flush()
        pending.append((row["id"], chunk_texts))
        pending_texts += len(chunk_texts)
    await flush()

    await client.aclose()
    await conn.close()
    logger.info("Backfill complete: %d articles, %d chunks", done, total_chunks)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(main())
