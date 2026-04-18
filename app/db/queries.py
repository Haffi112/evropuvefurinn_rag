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
    reviewer_id: int | None = None,
    mode: str = "rag",
) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO query_log
                (query_text, response_text, model_used, "references",
                 scope_declined, cached, latency_ms, ip_address, reviewer_id, mode)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
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
            reviewer_id,
            mode,
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
                   ql.cached, ql.created_at, ql.mode,
                   ru.username AS reviewer_username,
                   su.username AS submitted_by
            FROM query_log ql
            LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
            LEFT JOIN review_users ru ON ru.id = re.reviewer_id
            LEFT JOIN review_users su ON su.id = ql.reviewer_id
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
                   review_status, mode
            FROM query_log WHERE id = $1
            """,
            query_log_id,
        )
        return dict(row) if row else None


async def get_next_unreviewed_query(
    exclude_id: int | None, batch_user_id: int | None
) -> int | None:
    """Pick a random query_log row that has no review_evaluations row and is
    not review_status='excluded'. Prefers rows attributed to the batch user
    (ql.reviewer_id = batch_user_id) when such candidates exist; falls back to
    any unreviewed row otherwise. Returns the id, or None if nothing available."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ql.id
            FROM query_log ql
            LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
            WHERE re.id IS NULL
              AND ql.review_status != 'excluded'
              AND ($1::bigint IS NULL OR ql.id != $1)
            ORDER BY (ql.reviewer_id = $2) DESC, random()
            LIMIT 1
            """,
            exclude_id, batch_user_id,
        )
        return row["id"] if row else None


async def delete_batch(batch_id: int) -> bool:
    """Delete the batch and its items (ON DELETE CASCADE handles items).
    Leaves query_log / review_evaluations / reviewed_articles intact — the
    answered queries and any review work are preserved."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM query_batches WHERE id = $1 RETURNING id", batch_id,
        )
        return row is not None


# ── Stats (reviewer + admin) ────────────────────────────────

async def get_reviewer_stats(reviewer_id: int) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE created_at >= now() - interval '7 days') AS this_week,
                COUNT(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_seconds)
                    FILTER (WHERE duration_seconds IS NOT NULL) AS median_duration,
                AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL) AS avg_duration,
                SUM(duration_seconds) AS total_duration
            FROM review_evaluations
            WHERE reviewer_id = $1
            """,
            reviewer_id,
        )

        queue_remaining = await conn.fetchval(
            """
            SELECT COUNT(*) FROM query_log ql
            LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
            WHERE re.id IS NULL AND ql.review_status != 'excluded'
            """
        )

        daily_rows = await conn.fetch(
            """
            SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS count
            FROM review_evaluations
            WHERE reviewer_id = $1 AND created_at >= now() - interval '30 days'
            GROUP BY 1 ORDER BY 1
            """,
            reviewer_id,
        )

        mode_rows = await conn.fetch(
            """
            SELECT ql.mode AS mode, COUNT(*) AS count
            FROM review_evaluations re
            JOIN query_log ql ON ql.id = re.query_log_id
            WHERE re.reviewer_id = $1
            GROUP BY ql.mode
            """,
            reviewer_id,
        )

        # Checklist pass rates: pull all checklists for this reviewer and aggregate
        checklists = await conn.fetch(
            "SELECT checklist FROM review_evaluations WHERE reviewer_id = $1",
            reviewer_id,
        )
        pass_rates: dict[str, dict] = {}
        for row in checklists:
            cl = row["checklist"]
            if isinstance(cl, str):
                cl = json.loads(cl)
            for k, v in cl.items():
                entry = pass_rates.setdefault(k, {"true": 0, "total": 0})
                entry["total"] += 1
                if v is True:
                    entry["true"] += 1

        recent_rows = await conn.fetch(
            """
            SELECT re.query_log_id, ql.query_text, ql.mode, ql.review_status,
                   re.duration_seconds, re.created_at
            FROM review_evaluations re
            JOIN query_log ql ON ql.id = re.query_log_id
            WHERE re.reviewer_id = $1
            ORDER BY re.created_at DESC
            LIMIT 5
            """,
            reviewer_id,
        )

    return {
        "total": totals["total"],
        "this_week": totals["this_week"],
        "today": totals["today"],
        "median_duration_seconds": float(totals["median_duration"]) if totals["median_duration"] is not None else None,
        "avg_duration_seconds": float(totals["avg_duration"]) if totals["avg_duration"] is not None else None,
        "total_duration_seconds": int(totals["total_duration"]) if totals["total_duration"] is not None else 0,
        "queue_remaining": queue_remaining,
        "daily_activity": [{"day": r["day"].isoformat(), "count": r["count"]} for r in daily_rows],
        "mode_split": [{"mode": r["mode"], "count": r["count"]} for r in mode_rows],
        "checklist_pass_rates": [
            {"key": k, "true_count": v["true"], "total": v["total"],
             "rate": round(100.0 * v["true"] / v["total"], 1) if v["total"] else 0.0}
            for k, v in pass_rates.items()
        ],
        "recent": [dict(r) for r in recent_rows],
    }


async def get_admin_review_stats() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        status_rows = await conn.fetch(
            """
            SELECT review_status, COUNT(*) AS count
            FROM query_log
            GROUP BY review_status
            """
        )

        weekly_rows = await conn.fetch(
            """
            SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS count
            FROM review_evaluations
            WHERE created_at >= now() - interval '30 days'
            GROUP BY 1 ORDER BY 1
            """
        )

        reviewer_rows = await conn.fetch(
            """
            SELECT u.id, u.username,
                   COUNT(re.id) AS reviews,
                   AVG(re.duration_seconds) FILTER (WHERE re.duration_seconds IS NOT NULL) AS avg_duration,
                   SUM(re.duration_seconds) AS total_duration,
                   MAX(re.created_at) AS last_activity
            FROM review_users u
            LEFT JOIN review_evaluations re ON re.reviewer_id = u.id
            WHERE u.username != 'batch'  -- exclude the system attribution user
            GROUP BY u.id, u.username
            ORDER BY COUNT(re.id) DESC, u.username
            """
        )

        oldest_pending = await conn.fetch(
            """
            SELECT ql.id, ql.query_text, ql.mode, ql.created_at,
                   su.username AS submitted_by
            FROM query_log ql
            LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
            LEFT JOIN review_users su ON su.id = ql.reviewer_id
            WHERE re.id IS NULL AND ql.review_status != 'excluded'
            ORDER BY ql.created_at ASC
            LIMIT 10
            """
        )

        totals = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_evaluations,
                   AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL) AS avg_duration,
                   SUM(duration_seconds) AS total_duration
            FROM review_evaluations
            """
        )

    return {
        "status_counts": [{"status": r["review_status"], "count": r["count"]} for r in status_rows],
        "weekly_activity": [{"day": r["day"].isoformat(), "count": r["count"]} for r in weekly_rows],
        "reviewers": [
            {
                "id": r["id"],
                "username": r["username"],
                "reviews": r["reviews"],
                "avg_duration_seconds": float(r["avg_duration"]) if r["avg_duration"] is not None else None,
                "total_duration_seconds": int(r["total_duration"]) if r["total_duration"] is not None else 0,
                "last_activity": r["last_activity"].isoformat() if r["last_activity"] else None,
            }
            for r in reviewer_rows
        ],
        "oldest_pending": [
            {
                "id": r["id"],
                "query_text": r["query_text"],
                "mode": r["mode"],
                "created_at": r["created_at"].isoformat(),
                "submitted_by": r["submitted_by"],
            }
            for r in oldest_pending
        ],
        "total_evaluations": totals["total_evaluations"],
        "avg_duration_seconds": float(totals["avg_duration"]) if totals["avg_duration"] is not None else None,
        "total_duration_seconds": int(totals["total_duration"]) if totals["total_duration"] is not None else 0,
    }


async def upsert_evaluation(
    query_log_id: int, reviewer_id: int, checklist: dict, note: str | None,
    duration_seconds: int | None = None,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO review_evaluations (query_log_id, reviewer_id, checklist, note, duration_seconds)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (query_log_id) DO UPDATE SET
                reviewer_id = EXCLUDED.reviewer_id,
                checklist = EXCLUDED.checklist,
                note = EXCLUDED.note,
                duration_seconds = COALESCE(EXCLUDED.duration_seconds, review_evaluations.duration_seconds),
                updated_at = now()
            RETURNING *
            """,
            query_log_id, reviewer_id, json.dumps(checklist), note, duration_seconds,
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


# ── Admin Review Listing ──────────────────────────────────

async def list_evaluations_for_admin(
    page: int,
    per_page: int,
    review_status: str | None = None,
    reviewer_id: int | None = None,
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
    if reviewer_id is not None:
        conditions.append(f"re.reviewer_id = ${idx}")
        params.append(reviewer_id)
        idx += 1
    if search:
        conditions.append(f"ql.query_text ILIKE ${idx}")
        params.append(f"%{search}%")
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    base_sql = """
        FROM review_evaluations re
        JOIN query_log ql ON ql.id = re.query_log_id
        JOIN review_users ru ON ru.id = re.reviewer_id
        {where}
    """.format(where=where)

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) {base_sql}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT re.id, re.query_log_id, ql.query_text,
                   ru.username AS reviewer_username,
                   re.checklist, re.note,
                   ql.review_status,
                   EXISTS(SELECT 1 FROM reviewed_articles ra WHERE ra.query_log_id = ql.id) AS has_article,
                   re.created_at AS evaluation_date,
                   re.updated_at AS evaluation_updated,
                   ql.created_at AS query_date
            {base_sql}
            ORDER BY re.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params, per_page, offset,
        )
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("checklist"), str):
                d["checklist"] = json.loads(d["checklist"])
            result.append(d)
        return result, total


async def get_all_evaluations_for_export() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT re.id, re.query_log_id, ql.query_text,
                   ru.username AS reviewer_username,
                   re.checklist, re.note,
                   ql.review_status,
                   re.created_at AS evaluation_date
            FROM review_evaluations re
            JOIN query_log ql ON ql.id = re.query_log_id
            JOIN review_users ru ON ru.id = re.reviewer_id
            ORDER BY re.created_at DESC
            """
        )
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("checklist"), str):
                d["checklist"] = json.loads(d["checklist"])
            result.append(d)
        return result


async def get_all_query_logs_for_export() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, query_text, response_text, model_used, "references",
                   scope_declined, cached, latency_ms, ip_address, review_status, created_at
            FROM query_log
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in rows]


async def get_all_reviewed_articles_latest() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (ra.query_log_id)
                   ra.id, ra.query_log_id, ra.version, ra.title,
                   ra.edited_response, ra.created_at,
                   ql.query_text, ql."references"
            FROM reviewed_articles ra
            JOIN query_log ql ON ql.id = ra.query_log_id
            ORDER BY ra.query_log_id, ra.version DESC
            """
        )
        result = []
        for r in rows:
            d = dict(r)
            refs = d.get("references")
            if isinstance(refs, str):
                d["references"] = json.loads(refs)
            result.append(d)
        return result


# ── Batch queue ──────────────────────────────────────────────

async def get_batch_user_id() -> int | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM review_users WHERE username = 'batch'")


async def ensure_batch_user() -> int:
    """Seed a 'batch' review_user (idempotent) and return its id. The account
    has a random locked password hash so it cannot log in."""
    import secrets
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO review_users (username, password_hash, is_active)
            VALUES ('batch', $1, true)
            ON CONFLICT (username) DO NOTHING
            """,
            "locked:" + secrets.token_hex(16),
        )
        return await conn.fetchval("SELECT id FROM review_users WHERE username = 'batch'")


async def create_batch(filename: str, total: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO query_batches (filename, total)
            VALUES ($1, $2)
            RETURNING id
            """,
            filename, total,
        )


async def insert_batch_items(batch_id: int, items: list[dict]) -> None:
    """items = [{question_id, question_text, mode}, ...]"""
    if not items:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO batch_items (batch_id, question_id, question_text, mode)
            VALUES ($1, $2, $3, $4)
            """,
            [(batch_id, i["question_id"], i["question_text"], i["mode"]) for i in items],
        )


async def claim_next_batch_item() -> dict | None:
    """Atomically move the next pending item to 'processing'. Single-worker
    usage; no SKIP LOCKED needed."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE batch_items
            SET status = 'processing', updated_at = now()
            WHERE id = (
                SELECT id FROM batch_items
                WHERE status = 'pending'
                ORDER BY id
                LIMIT 1
            )
            RETURNING *
            """
        )
        return dict(row) if row else None


async def reset_stale_processing() -> int:
    """On startup, reset any items stuck in 'processing' back to 'pending'.
    Safe under single-worker assumption. Returns count reset."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH reset AS (
                UPDATE batch_items SET status = 'pending', updated_at = now()
                WHERE status = 'processing'
                RETURNING id
            )
            SELECT COUNT(*) AS n FROM reset
            """
        )
        return row["n"] if row else 0


async def complete_batch_item(item_id: int, query_log_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE batch_items
            SET status = 'done', query_log_id = $2, updated_at = now(), error = NULL
            WHERE id = $1
            """,
            item_id, query_log_id,
        )


async def fail_batch_item(item_id: int, error: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE batch_items
            SET status = 'failed', error = $2, updated_at = now()
            WHERE id = $1
            """,
            item_id, error[:2000],
        )


async def requeue_batch_item(item_id: int, retry_count: int, error: str | None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE batch_items
            SET status = 'pending', retry_count = $2, error = $3, updated_at = now()
            WHERE id = $1
            """,
            item_id, retry_count, (error or "")[:2000],
        )


async def try_complete_batch(batch_id: int) -> bool:
    """If no pending/processing items remain in the batch, mark it completed.
    Returns True if the batch was just completed."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE query_batches
            SET status = 'completed', completed_at = now()
            WHERE id = $1 AND status = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM batch_items
                  WHERE batch_id = $1 AND status IN ('pending', 'processing')
              )
            RETURNING id
            """,
            batch_id,
        )
        return row is not None


async def list_batches() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.filename, b.total, b.status,
                   b.created_at, b.completed_at,
                   COUNT(*) FILTER (WHERE i.status = 'done') AS done,
                   COUNT(*) FILTER (WHERE i.status = 'failed') AS failed,
                   COUNT(*) FILTER (WHERE i.status = 'pending') AS pending,
                   COUNT(*) FILTER (WHERE i.status = 'processing') AS processing,
                   COUNT(*) FILTER (WHERE i.status = 'cancelled') AS cancelled
            FROM query_batches b
            LEFT JOIN batch_items i ON i.batch_id = b.id
            GROUP BY b.id
            ORDER BY b.id DESC
            """
        )
        return [dict(r) for r in rows]


async def get_batch_detail(batch_id: int) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        batch = await conn.fetchrow(
            "SELECT * FROM query_batches WHERE id = $1", batch_id,
        )
        if not batch:
            return None
        items = await conn.fetch(
            "SELECT * FROM batch_items WHERE batch_id = $1 ORDER BY id",
            batch_id,
        )
        return {"batch": dict(batch), "items": [dict(r) for r in items]}


async def retry_failed_batch_items(batch_id: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE batch_items
                SET status = 'pending', retry_count = 0, error = NULL, updated_at = now()
                WHERE batch_id = $1 AND status = 'failed'
                RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            batch_id,
        )
        # Also re-open the batch if it had been marked completed
        await conn.execute(
            """
            UPDATE query_batches SET status = 'running', completed_at = NULL
            WHERE id = $1 AND status = 'completed'
              AND EXISTS (
                  SELECT 1 FROM batch_items
                  WHERE batch_id = $1 AND status IN ('pending', 'processing')
              )
            """,
            batch_id,
        )
        return row["n"] if row else 0


async def retry_batch_item(item_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE batch_items
            SET status = 'pending', retry_count = 0, error = NULL, updated_at = now()
            WHERE id = $1 AND status = 'failed'
            RETURNING batch_id
            """,
            item_id,
        )
        if not row:
            return False
        # Re-open the batch if needed
        await conn.execute(
            """
            UPDATE query_batches SET status = 'running', completed_at = NULL
            WHERE id = $1 AND status = 'completed'
            """,
            row["batch_id"],
        )
        return True


async def cancel_batch(batch_id: int) -> int:
    """Cancel pending items and mark batch cancelled. Items in 'processing'
    are left alone (will finish); on their completion, try_complete_batch
    updates the batch status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE batch_items
                SET status = 'cancelled', updated_at = now()
                WHERE batch_id = $1 AND status = 'pending'
                RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            batch_id,
        )
        await conn.execute(
            """
            UPDATE query_batches SET status = 'cancelled', completed_at = now()
            WHERE id = $1 AND status = 'running'
            """,
            batch_id,
        )
        return row["n"] if row else 0
