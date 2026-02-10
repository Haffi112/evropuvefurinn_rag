import json
from datetime import datetime, timedelta, timezone

import asyncpg

from app.db.database import get_pool


# ── Articles ─────────────────────────────────────────────────

async def insert_article(article: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO articles (id, title, question, answer, source_url, date, author, categories, tags)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            article["id"], article["title"], article["question"], article["answer"],
            article["source_url"], article["date"], article["author"],
            article["categories"], article["tags"],
        )
        return dict(row)


async def update_article(article_id: str, article: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE articles
            SET title=$2, question=$3, answer=$4, source_url=$5, date=$6,
                author=$7, categories=$8, tags=$9, updated_at=now()
            WHERE id=$1
            RETURNING *
            """,
            article_id, article["title"], article["question"], article["answer"],
            article["source_url"], article["date"], article["author"],
            article["categories"], article["tags"],
        )
        return dict(row) if row else None


async def delete_article(article_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM articles WHERE id=$1", article_id)
        return result == "DELETE 1"


async def get_article(article_id: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM articles WHERE id=$1", article_id)
        return dict(row) if row else None


async def list_articles(page: int, per_page: int) -> tuple[list[dict], int]:
    pool = get_pool()
    offset = (page - 1) * per_page
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM articles")
        rows = await conn.fetch(
            """
            SELECT id, title, source_url, date, updated_at
            FROM articles ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            per_page, offset,
        )
        return [dict(r) for r in rows], total


async def article_exists(article_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM articles WHERE id=$1)", article_id)


async def get_articles_by_ids(article_ids: list[str]) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM articles WHERE id = ANY($1)", article_ids)
        return [dict(r) for r in rows]


async def upsert_article(article: dict) -> tuple[dict, bool]:
    """Returns (row, was_created). True if INSERT, False if UPDATE."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO articles (id, title, question, answer, source_url, date, author, categories, tags)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO UPDATE SET
                title=EXCLUDED.title, question=EXCLUDED.question, answer=EXCLUDED.answer,
                source_url=EXCLUDED.source_url, date=EXCLUDED.date, author=EXCLUDED.author,
                categories=EXCLUDED.categories, tags=EXCLUDED.tags, updated_at=now()
            RETURNING *, (xmax = 0) AS was_created
            """,
            article["id"], article["title"], article["question"], article["answer"],
            article["source_url"], article["date"], article["author"],
            article["categories"], article["tags"],
        )
        row_dict = dict(row)
        was_created = row_dict.pop("was_created")
        return row_dict, was_created


# ── Query Cache ──────────────────────────────────────────────

async def cache_get(query_hash: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT response_json FROM query_cache WHERE query_hash=$1 AND expires_at > now()",
            query_hash,
        )
        return json.loads(row["response_json"]) if row else None


async def cache_store(query_hash: str, query_text: str, response: dict,
                      article_ids: list[str], ttl_hours: int) -> None:
    pool = get_pool()
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO query_cache (query_hash, query_text, response_json, article_ids_used, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (query_hash) DO UPDATE SET
                response_json=EXCLUDED.response_json,
                article_ids_used=EXCLUDED.article_ids_used,
                expires_at=EXCLUDED.expires_at,
                created_at=now()
            """,
            query_hash, query_text, json.dumps(response), article_ids, expires,
        )


async def cache_invalidate_by_article(article_id: str) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM query_cache WHERE $1 = ANY(article_ids_used)",
            article_id,
        )
        # result is e.g. "DELETE 3"
        return int(result.split()[-1])


# ── Daily Quota ──────────────────────────────────────────────

async def quota_get(model_id: str) -> int:
    pool = get_pool()
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count FROM daily_quota WHERE model_id=$1 AND date=$2",
            model_id, today,
        )
        return count or 0


async def quota_increment(model_id: str) -> int:
    pool = get_pool()
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO daily_quota (model_id, date, count)
            VALUES ($1, $2, 1)
            ON CONFLICT (model_id, date) DO UPDATE SET count = daily_quota.count + 1
            RETURNING count
            """,
            model_id, today,
        )
        return row["count"]


# ── Stats helpers ────────────────────────────────────────────

async def get_article_count() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM articles")


async def get_last_synced() -> datetime | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT GREATEST(MAX(created_at), MAX(updated_at)) FROM articles"
        )


async def get_today_query_count() -> int:
    pool = get_pool()
    today = datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        pro = await conn.fetchval(
            "SELECT COALESCE(count, 0) FROM daily_quota WHERE model_id='pro' AND date=$1", today
        ) or 0
        flash = await conn.fetchval(
            "SELECT COALESCE(count, 0) FROM daily_quota WHERE model_id='flash' AND date=$1", today
        ) or 0
        return pro + flash


async def get_cache_hit_rate() -> float:
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM query_cache")
        return 0.0 if total == 0 else round(total / max(total, 1), 2)
