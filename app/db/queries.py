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


# ── Query Log ───────────────────────────────────────────────

async def insert_query_log(
    query_text: str,
    response_text: str | None,
    model_used: str | None,
    references: list[dict] | None,
    scope_declined: bool,
    cached: bool,
    latency_ms: int | None,
    ip_address: str | None,
) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO query_log
                (query_text, response_text, model_used, "references",
                 scope_declined, cached, latency_ms, ip_address)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            query_text,
            response_text,
            model_used,
            json.dumps(references or []),
            scope_declined,
            cached,
            latency_ms,
            ip_address,
        )


async def list_query_logs(
    page: int,
    per_page: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cached: bool | None = None,
    model_used: str | None = None,
    scope_declined: bool | None = None,
    search: str | None = None,
) -> tuple[list[dict], int]:
    pool = get_pool()
    offset = (page - 1) * per_page

    conditions: list[str] = []
    params: list = []
    idx = 1

    if date_from is not None:
        conditions.append(f"created_at >= ${idx}")
        params.append(date_from)
        idx += 1
    if date_to is not None:
        conditions.append(f"created_at <= ${idx}")
        params.append(date_to)
        idx += 1
    if cached is not None:
        conditions.append(f"cached = ${idx}")
        params.append(cached)
        idx += 1
    if model_used is not None:
        conditions.append(f"model_used = ${idx}")
        params.append(model_used)
        idx += 1
    if scope_declined is not None:
        conditions.append(f"scope_declined = ${idx}")
        params.append(scope_declined)
        idx += 1
    if search:
        conditions.append(f"query_text ILIKE ${idx}")
        params.append(f"%{search}%")
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM query_log {where}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT id, query_text, response_text, model_used, "references",
                   scope_declined, cached, latency_ms, ip_address, created_at,
                   review_status
            FROM query_log {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params, per_page, offset,
        )
        return [dict(r) for r in rows], total


# ── Review Users ────────────────────────────────────────────

async def create_review_user(username: str, password_hash: str) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO review_users (username, password_hash)
            VALUES ($1, $2)
            RETURNING id, username, is_active, created_at
            """,
            username, password_hash,
        )
        return dict(row)


async def list_review_users() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, is_active, created_at FROM review_users ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]


async def deactivate_review_user(user_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE review_users SET is_active = false WHERE id = $1", user_id
        )


async def reset_review_user_password(user_id: int, password_hash: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE review_users SET password_hash = $1 WHERE id = $2",
            password_hash, user_id,
        )


async def get_review_user_by_username(username: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, is_active, created_at FROM review_users WHERE username = $1",
            username,
        )
        return dict(row) if row else None


# ── Review Operations ──────────────────────────────────────

async def list_query_logs_for_review(
    page: int,
    per_page: int,
    review_status: str | None = None,
    search: str | None = None,
) -> tuple[list[dict], int]:
    pool = get_pool()
    offset = (page - 1) * per_page

    conditions: list[str] = []
    params: list = []
    idx = 1

    if review_status is not None:
        conditions.append(f"ql.review_status = ${idx}")
        params.append(review_status)
        idx += 1
    else:
        # Hide excluded queries by default
        conditions.append(f"ql.review_status != ${idx}")
        params.append("excluded")
        idx += 1
    if search:
        conditions.append(f"ql.query_text ILIKE ${idx}")
        params.append(f"%{search}%")
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM query_log ql {where}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT ql.id, ql.query_text, ql.model_used, ql.review_status,
                   ql.cached, ql.created_at,
                   ru.username AS reviewer_username
            FROM query_log ql
            LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
            LEFT JOIN review_users ru ON ru.id = re.reviewer_id
            {where}
            ORDER BY ql.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params, per_page, offset,
        )
        return [dict(r) for r in rows], total


async def get_query_log_detail(query_log_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, query_text, response_text, model_used, "references",
                   scope_declined, cached, latency_ms, ip_address, created_at,
                   review_status
            FROM query_log WHERE id = $1
            """,
            query_log_id,
        )
        return dict(row) if row else None


async def upsert_evaluation(
    query_log_id: int, reviewer_id: int, checklist: dict, note: str | None
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO review_evaluations (query_log_id, reviewer_id, checklist, note)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (query_log_id) DO UPDATE SET
                reviewer_id = EXCLUDED.reviewer_id,
                checklist = EXCLUDED.checklist,
                note = EXCLUDED.note,
                updated_at = now()
            RETURNING *
            """,
            query_log_id, reviewer_id, json.dumps(checklist), note,
        )
        return dict(row)


async def get_evaluation(query_log_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM review_evaluations WHERE query_log_id = $1",
            query_log_id,
        )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("checklist"), str):
            d["checklist"] = json.loads(d["checklist"])
        return d


async def update_review_status(query_log_id: int, status: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE query_log SET review_status = $1 WHERE id = $2",
            status, query_log_id,
        )


async def insert_reviewed_article(
    query_log_id: int, reviewer_id: int, title: str, edited_response: str
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        max_version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM reviewed_articles WHERE query_log_id = $1",
            query_log_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO reviewed_articles (query_log_id, reviewer_id, version, title, edited_response)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            query_log_id, reviewer_id, max_version + 1, title, edited_response,
        )
        return dict(row)


async def get_latest_reviewed_article(query_log_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM reviewed_articles
            WHERE query_log_id = $1
            ORDER BY version DESC LIMIT 1
            """,
            query_log_id,
        )
        return dict(row) if row else None


async def get_query_log_stats() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                count(*)                                          AS total_queries,
                count(*) FILTER (WHERE created_at >= CURRENT_DATE) AS today_queries,
                count(*) FILTER (WHERE cached = TRUE)             AS cached_queries,
                count(*) FILTER (WHERE scope_declined = TRUE)     AS declined_queries,
                COALESCE(avg(latency_ms), 0)::INTEGER             AS avg_latency_ms
            FROM query_log
            """
        )
        return dict(row)
