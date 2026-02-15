"""
Backfill embeddings for all articles missing them.

Usage:
    python scripts/backfill_embeddings.py                  # reads from .env
    python scripts/backfill_embeddings.py --database-url DATABASE_URL --api-key KEY
"""

import argparse
import asyncio
import logging
import os
import sys
import time

import asyncpg
import httpx
import numpy as np
from pgvector.asyncpg import register_vector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEEPINFRA_EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings"
BATCH_SIZE = 25

SETUP_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'articles' AND column_name = 'embedding'
    ) THEN
        ALTER TABLE articles ADD COLUMN embedding vector(1024);
    END IF;
END $$;
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_articles_embedding_hnsw
    ON articles USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


def build_embed_text(title: str, question: str, answer: str) -> str:
    words = answer.split()
    truncated = " ".join(words[:1000])
    return f"{title}\n{question}\n{truncated}"


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


async def main(database_url: str, api_key: str, model: str) -> None:
    conn = await asyncpg.connect(database_url)
    await register_vector(conn)

    # Ensure schema is ready
    await conn.execute(SETUP_SQL)

    # Fetch articles needing embeddings
    rows = await conn.fetch(
        "SELECT id, title, question, answer FROM articles WHERE embedding IS NULL ORDER BY id"
    )
    total = len(rows)
    logger.info("Found %d articles needing embeddings", total)

    if total == 0:
        logger.info("Nothing to do — all articles already have embeddings")
        await conn.close()
        return

    client = httpx.AsyncClient(
        timeout=60.0,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    embedded = 0
    start = time.time()

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        texts = [build_embed_text(r["title"], r["question"], r["answer"]) for r in batch]

        embeddings = await embed_batch(client, texts, model)

        for row, emb in zip(batch, embeddings):
            vec = np.array(emb, dtype=np.float32)
            await conn.execute(
                "UPDATE articles SET embedding = $1 WHERE id = $2",
                vec, row["id"],
            )

        embedded += len(batch)
        elapsed = time.time() - start
        rate = embedded / elapsed if elapsed > 0 else 0
        logger.info("Progress: %d/%d (%.1f articles/sec)", embedded, total, rate)

    await client.aclose()

    # Create HNSW index
    logger.info("Creating HNSW index (if not exists)...")
    await conn.execute(INDEX_SQL)

    # Final stats
    count = await conn.fetchval("SELECT count(*) FROM articles WHERE embedding IS NOT NULL")
    logger.info("Done! %d/%d articles now have embeddings", count, total)

    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill article embeddings via DeepInfra")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection string")
    parser.add_argument("--api-key", default=None, help="DeepInfra API key")
    parser.add_argument("--model", default="intfloat/multilingual-e5-large", help="Embedding model")
    args = parser.parse_args()

    db_url = args.database_url or os.getenv("DATABASE_URL")
    key = args.api_key or os.getenv("DEEPINFRA_API_KEY")

    if not db_url:
        # Try loading from .env
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            db_url = os.getenv("DATABASE_URL")
            key = key or os.getenv("DEEPINFRA_API_KEY")

    if not db_url:
        logger.error("DATABASE_URL is required (--database-url or env)")
        sys.exit(1)
    if not key:
        logger.error("DEEPINFRA_API_KEY is required (--api-key or env)")
        sys.exit(1)

    asyncio.run(main(db_url, key, args.model))
