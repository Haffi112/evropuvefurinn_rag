import json
import re
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
    # Empty-answer rows are kept for telemetry but routed straight to
    # `excluded` so they never reach the reviewer queue. Non-empty rows are
    # 'pending'. The batch worker treats empty answers as retriable failures,
    # so every retry attempt logs its own row — without this, each retry would
    # leave an orphaned blank entry in the reviewer queue.
    #
    # NB: this is computed in Python on purpose. Doing it in SQL as
    # `CASE WHEN $2 IS NULL OR trim($2) = '' ...` made Postgres unable to infer
    # the type of parameter $2 (AmbiguousParameterError), which silently broke
    # *every* query-log insert — `_log_query` swallows the error, so it went
    # unnoticed for weeks.
    review_status = (
        "excluded"
        if response_text is None or response_text.strip() == ""
        else "pending"
    )
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO query_log
                (query_text, response_text, model_used, "references",
                 scope_declined, cached, latency_ms, ip_address,
                 reviewer_id, mode, review_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
            review_status,
        )


_QUERY_LOG_COLUMNS = (
    'id, query_text, response_text, model_used, mode, "references", '
    "scope_declined, cached, latency_ms, ip_address, created_at, review_status"
)


def _query_log_filters(
    date_from: datetime | None,
    date_to: datetime | None,
    cached: bool | None,
    model_used: str | None,
    scope_declined: bool | None,
    search: str | None,
) -> tuple[str, list]:
    """Build the shared WHERE clause + params for query-log list and export so
    the exported set always matches what the operator is viewing."""
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
        # Substring match so UI labels like "pro"/"flash" match full OpenRouter
        # ids (e.g. "google/gemini-3.5-flash") without pinning exact versions.
        conditions.append(f"model_used ILIKE ${idx}")
        params.append(f"%{model_used}%")
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
    return where, params


def _query_log_direction(order: str | None) -> str:
    """Whitelist the sort direction — never interpolate user input into SQL."""
    return "ASC" if (order or "").lower() == "asc" else "DESC"


async def list_query_logs(
    page: int,
    per_page: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cached: bool | None = None,
    model_used: str | None = None,
    scope_declined: bool | None = None,
    search: str | None = None,
    order: str = "desc",
) -> tuple[list[dict], int]:
    pool = get_pool()
    offset = (page - 1) * per_page

    where, params = _query_log_filters(
        date_from, date_to, cached, model_used, scope_declined, search
    )
    direction = _query_log_direction(order)
    idx = len(params) + 1

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM query_log {where}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT {_QUERY_LOG_COLUMNS}
            FROM query_log {where}
            ORDER BY created_at {direction}
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params, per_page, offset,
        )
        return [dict(r) for r in rows], total


async def export_query_logs(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cached: bool | None = None,
    model_used: str | None = None,
    scope_declined: bool | None = None,
    search: str | None = None,
    order: str = "desc",
) -> list[dict]:
    """All query-log rows matching the same filters as `list_query_logs`, with
    no pagination — backs the filtered CSV export."""
    pool = get_pool()
    where, params = _query_log_filters(
        date_from, date_to, cached, model_used, scope_declined, search
    )
    direction = _query_log_direction(order)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_QUERY_LOG_COLUMNS}
            FROM query_log {where}
            ORDER BY created_at {direction}
            """,
            *params,
        )
        return [dict(r) for r in rows]


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
    viewer_id: int | None = None,
    mine_filter: str | None = None,
) -> tuple[list[dict], int]:
    """List queries for the reviewer queue.

    Aggregates multi-annotator evaluation data per query:
      - evaluation_count: how many distinct reviewers have evaluated
      - reviewer_usernames: list of those usernames
      - reviewer_username:  comma-joined string (backward compat)
      - i_evaluated:        whether the calling reviewer (viewer_id) has evaluated

    mine_filter (requires viewer_id):
      - None       → no per-viewer filter
      - 'pending'  → only queries this viewer has NOT evaluated
      - 'done'     → only queries this viewer HAS evaluated
    """
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

    # Per-viewer filter — only applied when viewer_id and mine_filter are set.
    if viewer_id is not None and mine_filter == "pending":
        conditions.append(
            f"NOT EXISTS (SELECT 1 FROM review_evaluations re_mf "
            f"WHERE re_mf.query_log_id = ql.id AND re_mf.reviewer_id = ${idx})"
        )
        params.append(viewer_id)
        idx += 1
    elif viewer_id is not None and mine_filter == "done":
        conditions.append(
            f"EXISTS (SELECT 1 FROM review_evaluations re_mf "
            f"WHERE re_mf.query_log_id = ql.id AND re_mf.reviewer_id = ${idx})"
        )
        params.append(viewer_id)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # $idx → viewer_id (for SELECT-side i_evaluated), $idx+1 → per_page,
    # $idx+2 → offset. viewer_id is passed twice in the worst case (once in
    # WHERE via mine_filter, once in SELECT for i_evaluated) — fine, asyncpg
    # handles repeated positional values.
    viewer_param_idx = idx
    limit_idx = idx + 1
    offset_idx = idx + 2

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM query_log ql {where}", *params
        )
        rows = await conn.fetch(
            f"""
            SELECT ql.id, ql.query_text, ql.model_used, ql.review_status,
                   ql.cached, ql.created_at, ql.mode,
                   su.username AS submitted_by,
                   COALESCE(agg.evaluation_count, 0) AS evaluation_count,
                   COALESCE(agg.reviewer_usernames, '{{}}'::text[]) AS reviewer_usernames,
                   EXISTS (
                       SELECT 1 FROM review_evaluations re2
                       WHERE re2.query_log_id = ql.id
                         AND ${viewer_param_idx}::int IS NOT NULL
                         AND re2.reviewer_id = ${viewer_param_idx}::int
                   ) AS i_evaluated
            FROM query_log ql
            LEFT JOIN review_users su ON su.id = ql.reviewer_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS evaluation_count,
                       array_agg(ru.username ORDER BY re.created_at) AS reviewer_usernames
                FROM review_evaluations re
                JOIN review_users ru ON ru.id = re.reviewer_id
                WHERE re.query_log_id = ql.id
            ) agg ON TRUE
            {where}
            ORDER BY ql.created_at DESC
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *params, viewer_id, per_page, offset,
        )
        rows = [
            {
                **dict(r),
                # Backward-compatible string view of the aggregated list
                "reviewer_username": ", ".join(r["reviewer_usernames"])
                    if r["reviewer_usernames"] else None,
            }
            for r in rows
        ]
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
    exclude_id: int | None,
    batch_user_id: int | None,
    reviewer_id: int | None = None,
) -> int | None:
    """Pick the next query for this reviewer, random within priority tiers.

    With multi-annotator support, "unreviewed" is per-reviewer: a query
    already evaluated by reviewer A is still available to reviewer B.
    Each reviewer therefore walks the corpus in their own random order.

    Priority tiers (highest first):
      1. Queries with zero evaluations from anyone — drives coverage first
         so we never double-up before every article has been seen.
      2. Queries attributed to the batch user — keeps the seeded review
         corpus ahead of ad-hoc playground queries.
      3. Random tiebreaker within each tier.

    If reviewer_id is None (legacy callers), falls back to the original
    "no evaluations at all" filter for backward compatibility.

    NOTE: We coerce preference expressions via `IS TRUE` because comparing
    against a NULL reviewer_id yields NULL, and Postgres's default
    `ORDER BY ... DESC` puts NULLs FIRST — which would bias the picker toward
    playground queries (NULL reviewer_id) over actual batch items. Using
    `IS TRUE` forces a plain TRUE/FALSE sort key."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ql.id
            FROM query_log ql
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS n_evals
                FROM review_evaluations re
                WHERE re.query_log_id = ql.id
            ) agg ON TRUE
            WHERE ql.review_status != 'excluded'
              AND ($1::bigint IS NULL OR ql.id != $1)
              AND (
                  -- Legacy: caller didn't pass reviewer_id → original semantics
                  ($3::int IS NULL AND agg.n_evals = 0)
                  OR
                  -- Per-reviewer: this reviewer has no evaluation row yet
                  ($3::int IS NOT NULL AND NOT EXISTS (
                      SELECT 1 FROM review_evaluations re
                      WHERE re.query_log_id = ql.id AND re.reviewer_id = $3
                  ))
              )
            ORDER BY (agg.n_evals = 0) DESC,
                     (ql.reviewer_id = $2) IS TRUE DESC,
                     random()
            LIMIT 1
            """,
            exclude_id, batch_user_id, reviewer_id,
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


# ── Flagged references ──────────────────────────────────────

_REFS_HEADING_RE = re.compile(
    r"^##\s+(?:Heimildir|References)\s*$", re.MULTILINE
)
_URL_RE = re.compile(r"https?://[^\s)\]\>]+")


def _extract_web_urls(answer_text: str | None) -> list[str]:
    """Extract URLs from the References/Heimildir section of a web-search
    answer. Returns unique URLs in first-appearance order (after the heading)."""
    if not answer_text:
        return []
    m = _REFS_HEADING_RE.search(answer_text)
    if not m:
        return []
    section = answer_text[m.end():]
    urls: list[str] = []
    seen: set[str] = set()
    for line in section.splitlines():
        if not re.match(r"^\s*-\s*\[\d+\]", line):
            continue
        for url_m in _URL_RE.finditer(line):
            url = url_m.group(0).rstrip(".,;)'\"")
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _domain_of(url: str) -> str | None:
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


async def flag_reference(
    reviewer_id: int,
    article_id: str | None = None,
    url: str | None = None,
    flag_type: str = "outdated",
    query_log_id: int | None = None,
    reason: str | None = None,
) -> dict:
    """Create (or re-open) an OPEN flag. Exactly one of article_id/url must be set.
    Upserts so the same reviewer flagging the same identifier twice updates
    the existing open flag instead of raising a conflict."""
    if (article_id is None) == (url is None):
        raise ValueError("exactly one of article_id or url must be set")

    pool = get_pool()
    async with pool.acquire() as conn:
        if article_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO flagged_references
                    (article_id, reviewer_id, flag_type, query_log_id, reason)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (article_id, reviewer_id)
                    WHERE article_id IS NOT NULL AND resolved_at IS NULL
                DO UPDATE SET
                    flag_type = EXCLUDED.flag_type,
                    query_log_id = COALESCE(EXCLUDED.query_log_id, flagged_references.query_log_id),
                    reason = COALESCE(EXCLUDED.reason, flagged_references.reason),
                    created_at = now()
                RETURNING *
                """,
                article_id, reviewer_id, flag_type, query_log_id, reason,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO flagged_references
                    (url, reviewer_id, flag_type, query_log_id, reason)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (url, reviewer_id)
                    WHERE url IS NOT NULL AND resolved_at IS NULL
                DO UPDATE SET
                    flag_type = EXCLUDED.flag_type,
                    query_log_id = COALESCE(EXCLUDED.query_log_id, flagged_references.query_log_id),
                    reason = COALESCE(EXCLUDED.reason, flagged_references.reason),
                    created_at = now()
                RETURNING *
                """,
                url, reviewer_id, flag_type, query_log_id, reason,
            )
        return dict(row)


async def unflag_reference(flag_id: int, reviewer_id: int) -> bool:
    """Delete a flag row. The reviewer can only remove their own flag."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM flagged_references WHERE id = $1 AND reviewer_id = $2 RETURNING id",
            flag_id, reviewer_id,
        )
        return row is not None


async def get_flags_for_query(query_log_id: int) -> list[dict]:
    """Return open flags associated with a query's references. Matches by:
    - `article_id` — from the query_log.references JSONB (RAG citations)
    - `url` — parsed from the query_log.response_text References section
      (web-search citations)

    Works transparently for both modes: RAG queries return article-based
    flags, web-search queries return URL-based flags, and mixed histories
    can return both if present.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        ql = await conn.fetchrow(
            'SELECT "references", response_text, mode FROM query_log WHERE id = $1',
            query_log_id,
        )
        if not ql:
            return []

        refs = ql["references"]
        if isinstance(refs, str):
            refs = json.loads(refs)
        article_ids = [r.get("id") for r in (refs or []) if r.get("id")]

        urls = _extract_web_urls(ql["response_text"])

        if not article_ids and not urls:
            return []

        rows = await conn.fetch(
            """
            SELECT f.id, f.article_id, f.url, f.flag_type, f.reviewer_id,
                   f.reason, f.created_at,
                   u.username AS reviewer_username
            FROM flagged_references f
            JOIN review_users u ON u.id = f.reviewer_id
            WHERE f.resolved_at IS NULL
              AND (
                  (f.article_id IS NOT NULL AND f.article_id = ANY($1::text[]))
                  OR
                  (f.url IS NOT NULL AND f.url = ANY($2::text[]))
              )
            """,
            article_ids, urls,
        )
        return [dict(r) for r in rows]


async def list_flagged_references(resolved: bool | None = False) -> list[dict]:
    """Admin view: aggregate flags grouped by target. Each row represents
    either an article (RAG) or a URL (web) plus open/total counts, distinct
    reviewers, most-recent flag, flag_type breakdown, and all flag details.

    `resolved`:
      - False (default): only groups with open flags
      - True: only groups with resolved flags
      - None: all
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        if resolved is False:
            having = "HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NULL) > 0"
        elif resolved is True:
            having = "HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NOT NULL) > 0"
        else:
            having = ""

        rows = await conn.fetch(
            f"""
            SELECT
                CASE WHEN f.article_id IS NOT NULL
                     THEN 'article' ELSE 'url' END           AS kind,
                f.article_id                                  AS article_id,
                f.url                                         AS url,
                a.title                                       AS article_title,
                a.source_url                                  AS article_source_url,
                a.date                                        AS article_date,
                COUNT(*)                                      AS total_flags,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL) AS open_flags,
                COUNT(*) FILTER (WHERE f.resolved_at IS NOT NULL) AS resolved_flags,
                COUNT(DISTINCT f.reviewer_id) FILTER (WHERE f.resolved_at IS NULL) AS distinct_reviewers,
                MAX(f.created_at)                             AS most_recent,
                -- flag type breakdown (open flags only)
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.flag_type = 'outdated')      AS open_outdated,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.flag_type = 'irrelevant')    AS open_irrelevant,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.flag_type = 'untrustworthy') AS open_untrustworthy,
                json_agg(
                    json_build_object(
                        'id', f.id,
                        'flag_type', f.flag_type,
                        'reviewer_id', f.reviewer_id,
                        'reviewer_username', u.username,
                        'reason', f.reason,
                        'query_log_id', f.query_log_id,
                        'resolved_at', f.resolved_at,
                        'resolved_by_username', ru.username,
                        'created_at', f.created_at
                    )
                    ORDER BY f.created_at DESC
                ) AS flags
            FROM flagged_references f
            JOIN review_users u ON u.id = f.reviewer_id
            LEFT JOIN review_users ru ON ru.id = f.resolved_by
            LEFT JOIN articles a ON a.id = f.article_id
            GROUP BY
                CASE WHEN f.article_id IS NOT NULL THEN 'article' ELSE 'url' END,
                f.article_id, f.url, a.title, a.source_url, a.date
            {having}
            ORDER BY COUNT(*) FILTER (WHERE f.resolved_at IS NULL) DESC,
                     COUNT(*) DESC,
                     MAX(f.created_at) DESC
            """
        )
        result = []
        for r in rows:
            d = dict(r)
            flags = d.get("flags")
            if isinstance(flags, str):
                d["flags"] = json.loads(flags)
            result.append(d)
        return result


async def list_flagged_domains(resolved: bool | None = False) -> list[dict]:
    """Admin aggregate: group URL-based flags by domain. Articles are
    excluded (they have their own canonical group in the main list; the
    domain view is specifically about web sources)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.url, f.resolved_at, f.flag_type
            FROM flagged_references f
            WHERE f.url IS NOT NULL
            """
        )

    by_domain: dict[str, dict] = {}
    for r in rows:
        dom = _domain_of(r["url"])
        if not dom:
            continue
        d = by_domain.setdefault(dom, {
            "domain": dom,
            "total_flags": 0, "open_flags": 0, "resolved_flags": 0,
            "open_irrelevant": 0, "open_untrustworthy": 0, "open_outdated": 0,
            "urls": set(),
        })
        d["total_flags"] += 1
        d["urls"].add(r["url"])
        if r["resolved_at"] is None:
            d["open_flags"] += 1
            if r["flag_type"] == "irrelevant":
                d["open_irrelevant"] += 1
            elif r["flag_type"] == "untrustworthy":
                d["open_untrustworthy"] += 1
            elif r["flag_type"] == "outdated":
                d["open_outdated"] += 1
        else:
            d["resolved_flags"] += 1

    result: list[dict] = []
    for d in by_domain.values():
        if resolved is False and d["open_flags"] == 0:
            continue
        if resolved is True and d["resolved_flags"] == 0:
            continue
        d["distinct_urls"] = len(d["urls"])
        d.pop("urls")
        result.append(d)
    result.sort(key=lambda x: (-x["open_flags"], -x["total_flags"], x["domain"]))
    return result


async def resolve_flag(flag_id: int, admin_user_id: int | None = None) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE flagged_references
            SET resolved_at = now(), resolved_by = $2
            WHERE id = $1 AND resolved_at IS NULL
            RETURNING id
            """,
            flag_id, admin_user_id,
        )
        return row is not None


async def resolve_all_flags_for_article(article_id: str, admin_user_id: int | None = None) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE flagged_references
                SET resolved_at = now(), resolved_by = $2
                WHERE article_id = $1 AND resolved_at IS NULL
                RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            article_id, admin_user_id,
        )
        return row["n"] if row else 0


async def resolve_all_flags_for_url(url: str, admin_user_id: int | None = None) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH updated AS (
                UPDATE flagged_references
                SET resolved_at = now(), resolved_by = $2
                WHERE url = $1 AND resolved_at IS NULL
                RETURNING id
            )
            SELECT COUNT(*) AS n FROM updated
            """,
            url, admin_user_id,
        )
        return row["n"] if row else 0


async def get_flag_stats() -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE resolved_at IS NULL) AS open_flags,
                COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved_flags,
                COUNT(DISTINCT article_id) FILTER (WHERE resolved_at IS NULL AND article_id IS NOT NULL) AS articles_with_open_flags,
                COUNT(DISTINCT url) FILTER (WHERE resolved_at IS NULL AND url IS NOT NULL) AS urls_with_open_flags,
                COUNT(*) FILTER (WHERE resolved_at IS NULL AND flag_type = 'outdated') AS open_outdated,
                COUNT(*) FILTER (WHERE resolved_at IS NULL AND flag_type = 'irrelevant') AS open_irrelevant,
                COUNT(*) FILTER (WHERE resolved_at IS NULL AND flag_type = 'untrustworthy') AS open_untrustworthy
            FROM flagged_references
            """
        )
        return dict(row) if row else {
            "open_flags": 0, "resolved_flags": 0,
            "articles_with_open_flags": 0, "urls_with_open_flags": 0,
            "open_outdated": 0, "open_irrelevant": 0, "open_untrustworthy": 0,
        }


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

        # Per-reviewer queue: queries this reviewer has NOT evaluated and that
        # aren't excluded. (The previous global "zero evals anywhere" semantic
        # was wrong under multi-annotator — each reviewer now has their own
        # remaining queue.)
        scope = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total_in_scope,
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1 FROM review_evaluations re
                        WHERE re.query_log_id = ql.id AND re.reviewer_id = $1
                    )
                ) AS queue_remaining
            FROM query_log ql
            WHERE ql.review_status != 'excluded'
            """,
            reviewer_id,
        )
        total_in_scope = scope["total_in_scope"]
        queue_remaining = scope["queue_remaining"]

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
        # Per-reviewer queue progress
        "queue_remaining": queue_remaining,
        "total_in_scope": total_in_scope,
        "reviewed_by_me": totals["total"],
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

        # Multi-annotator coverage: how many distinct queries have been
        # evaluated by N reviewers? Captures coverage progress (queries
        # with ≥1 annotator) and inter-annotator depth (queries with ≥2).
        coverage = await conn.fetchrow(
            """
            WITH per_query AS (
                SELECT ql.id,
                       COUNT(re.id) AS n_evals
                FROM query_log ql
                LEFT JOIN review_evaluations re ON re.query_log_id = ql.id
                WHERE ql.review_status != 'excluded'
                GROUP BY ql.id
            )
            SELECT COUNT(*) FILTER (WHERE n_evals = 0) AS zero_evals,
                   COUNT(*) FILTER (WHERE n_evals = 1) AS one_eval,
                   COUNT(*) FILTER (WHERE n_evals >= 2) AS multi_eval,
                   COUNT(*) AS total_queries,
                   COALESCE(AVG(n_evals) FILTER (WHERE n_evals > 0), 0)::float
                       AS avg_annotators_per_reviewed_query
            FROM per_query
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
        "coverage": {
            "zero_evals": coverage["zero_evals"],
            "one_eval": coverage["one_eval"],
            "multi_eval": coverage["multi_eval"],
            "total_queries": coverage["total_queries"],
            "avg_annotators_per_reviewed_query": float(coverage["avg_annotators_per_reviewed_query"]),
        },
    }


async def upsert_evaluation(
    query_log_id: int, reviewer_id: int, checklist: dict, note: str | None,
    duration_seconds: int | None = None,
) -> dict:
    """Insert or update a reviewer's evaluation of a query.

    Conflict key is (query_log_id, reviewer_id): one evaluation per reviewer
    per query, but multiple reviewers may each have their own row.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO review_evaluations (query_log_id, reviewer_id, checklist, note, duration_seconds)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (query_log_id, reviewer_id) DO UPDATE SET
                checklist = EXCLUDED.checklist,
                note = EXCLUDED.note,
                duration_seconds = COALESCE(EXCLUDED.duration_seconds, review_evaluations.duration_seconds),
                updated_at = now()
            RETURNING *
            """,
            query_log_id, reviewer_id, json.dumps(checklist), note, duration_seconds,
        )
        return dict(row)


async def get_evaluation_for_reviewer(
    query_log_id: int, reviewer_id: int,
) -> dict | None:
    """Return a single reviewer's evaluation of a query, or None."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM review_evaluations
            WHERE query_log_id = $1 AND reviewer_id = $2
            """,
            query_log_id, reviewer_id,
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


async def get_all_evaluations_for_query(query_log_id: int) -> list[dict]:
    """Return every reviewer's evaluation of a query, with reviewer username."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT re.*, ru.username AS reviewer_username
            FROM review_evaluations re
            JOIN review_users ru ON ru.id = re.reviewer_id
            WHERE re.query_log_id = $1
            ORDER BY re.created_at ASC
            """,
            query_log_id,
        )
        out: list[dict] = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("checklist"), str):
                d["checklist"] = json.loads(d["checklist"])
            out.append(d)
        return out


def derive_review_status(
    current_status: str, checklists: list[dict] | None,
) -> str:
    """Pure rule deriving review_status from current status and evaluations.

    Exposed as a module-level function so it can be unit-tested without a DB.

    Rule (multi-annotator):
      - current_status == 'excluded' → stay 'excluded' (terminal)
      - No checklists                → 'pending'
      - At least one checklist, but
        not all are all-true          → 'reviewed'
      - All checklists all-true      → 'approved'
    """
    if current_status == "excluded":
        return "excluded"
    if not checklists:
        return "pending"

    def all_true(cl: dict) -> bool:
        return bool(cl) and all(v is True for v in cl.values())

    return "approved" if all(all_true(c) for c in checklists) else "reviewed"


async def recompute_review_status(query_log_id: int) -> str:
    """Re-derive a query's review_status from all stored evaluations.

    Returns the new status. Leaves rows with status='excluded' untouched.
    See derive_review_status() for the decision rule.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchval(
                "SELECT review_status FROM query_log WHERE id = $1",
                query_log_id,
            )
            if current == "excluded":
                return "excluded"

            rows = await conn.fetch(
                "SELECT checklist FROM review_evaluations WHERE query_log_id = $1",
                query_log_id,
            )
            parsed = [
                json.loads(r["checklist"]) if isinstance(r["checklist"], str) else r["checklist"]
                for r in rows
            ]
            new_status = derive_review_status(current, parsed)

            if new_status != current:
                await conn.execute(
                    "UPDATE query_log SET review_status = $1 WHERE id = $2",
                    new_status, query_log_id,
                )
            return new_status


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
                   ql.review_status, ql.mode,
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
            SELECT id, query_text, response_text, model_used, mode, "references",
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


async def get_all_flagged_references_for_export() -> list[dict]:
    """Flat export of every source flag (open AND resolved), one row per flag.

    Each flag targets either an article (RAG/local source) or a web URL. We
    LEFT JOIN articles so article-based flags carry their title/source_url, and
    expose the flagging reviewer's comment (`reason`), the flag_type
    ('outdated' / 'irrelevant' / 'untrustworthy'), the originating query, and
    full resolution metadata so the export is a complete audit trail."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                f.id                                          AS flag_id,
                CASE WHEN f.article_id IS NOT NULL
                     THEN 'article' ELSE 'url' END            AS source_kind,
                f.flag_type                                   AS flag_type,
                f.reason                                      AS comment,
                f.article_id                                  AS article_id,
                a.title                                       AS article_title,
                a.source_url                                  AS article_source_url,
                f.url                                         AS web_url,
                f.query_log_id                                AS query_log_id,
                ql.query_text                                 AS query_text,
                u.username                                    AS flagged_by,
                f.created_at                                  AS flagged_at,
                CASE WHEN f.resolved_at IS NULL
                     THEN 'open' ELSE 'resolved' END          AS status,
                f.resolved_at                                 AS resolved_at,
                ru.username                                   AS resolved_by
            FROM flagged_references f
            JOIN review_users u ON u.id = f.reviewer_id
            LEFT JOIN review_users ru ON ru.id = f.resolved_by
            LEFT JOIN articles a ON a.id = f.article_id
            LEFT JOIN query_log ql ON ql.id = f.query_log_id
            ORDER BY f.created_at DESC
            """
        )
        return [dict(r) for r in rows]


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
            """
            SELECT bi.*,
                   -- True iff this item points at a query_log row whose answer
                   -- is effectively blank. The worker used to accept empty
                   -- model responses as successful; this flag lets the admin
                   -- UI surface them.
                   (bi.query_log_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM query_log ql
                        WHERE ql.id = bi.query_log_id
                          AND (ql.response_text IS NULL OR trim(ql.response_text) = '')
                    )) AS response_empty
            FROM batch_items bi
            WHERE bi.batch_id = $1
            ORDER BY bi.id
            """,
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


async def regenerate_batch_items_by_mode(batch_id: int, mode: str) -> dict:
    """Re-queue every item of a given mode in a batch and excise the existing
    answers from the reviewer queue.

    Skips items currently in 'processing' (worker-owned). Existing `query_log`
    rows are marked `review_status = 'excluded'` rather than deleted, so any
    reviewer evaluations / article drafts already attached remain intact but
    the old answers disappear from the active queue.

    Returns `{items_reset, logs_excluded, items_processing_skipped}`.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH target AS (
                    SELECT id, query_log_id, status
                    FROM batch_items
                    WHERE batch_id = $1 AND mode = $2
                    FOR UPDATE
                ),
                to_reset AS (
                    SELECT id, query_log_id FROM target WHERE status <> 'processing'
                ),
                to_skip AS (
                    SELECT id FROM target WHERE status = 'processing'
                ),
                excluded_logs AS (
                    UPDATE query_log
                    SET review_status = 'excluded'
                    WHERE id IN (
                        SELECT query_log_id FROM to_reset WHERE query_log_id IS NOT NULL
                    )
                    RETURNING id
                ),
                reset_items AS (
                    UPDATE batch_items
                    SET status = 'pending',
                        retry_count = 0,
                        error = NULL,
                        query_log_id = NULL,
                        updated_at = now()
                    WHERE id IN (SELECT id FROM to_reset)
                    RETURNING id
                )
                SELECT
                    (SELECT COUNT(*) FROM reset_items)      AS items_reset,
                    (SELECT COUNT(*) FROM excluded_logs)    AS logs_excluded,
                    (SELECT COUNT(*) FROM to_skip)          AS items_processing_skipped
                """,
                batch_id, mode,
            )
            await conn.execute(
                """
                UPDATE query_batches
                SET status = 'running', completed_at = NULL
                WHERE id = $1 AND status = 'completed'
                """,
                batch_id,
            )
    return dict(row) if row else {
        "items_reset": 0, "logs_excluded": 0, "items_processing_skipped": 0,
    }


async def resalvage_empty_batch_items(
    batch_id: int, mode: str | None = None,
) -> dict:
    """Re-queue batch items whose linked `query_log` row has an empty answer.

    Historically the worker marked items `done` as long as `query_log_id` was
    non-null, even when the LLM returned an empty string. This helper
    identifies those silent failures and puts them back through the normal
    retry path, and excludes the empty rows from the reviewer queue.

    `mode` optionally restricts the scope to a single mode ('rag' | 'websearch').
    Items currently in 'processing' are skipped.

    Returns `{items_reset, logs_excluded, items_processing_skipped}`.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                WITH target AS (
                    SELECT bi.id, bi.query_log_id, bi.status
                    FROM batch_items bi
                    JOIN query_log ql ON ql.id = bi.query_log_id
                    WHERE bi.batch_id = $1
                      AND ($2::text IS NULL OR bi.mode = $2)
                      AND (ql.response_text IS NULL OR trim(ql.response_text) = '')
                    FOR UPDATE OF bi
                ),
                to_reset AS (
                    SELECT id, query_log_id FROM target WHERE status <> 'processing'
                ),
                to_skip AS (
                    SELECT id FROM target WHERE status = 'processing'
                ),
                excluded_logs AS (
                    UPDATE query_log
                    SET review_status = 'excluded'
                    WHERE id IN (
                        SELECT query_log_id FROM to_reset WHERE query_log_id IS NOT NULL
                    )
                    RETURNING id
                ),
                reset_items AS (
                    UPDATE batch_items
                    SET status = 'pending',
                        retry_count = 0,
                        error = NULL,
                        query_log_id = NULL,
                        updated_at = now()
                    WHERE id IN (SELECT id FROM to_reset)
                    RETURNING id
                )
                SELECT
                    (SELECT COUNT(*) FROM reset_items)      AS items_reset,
                    (SELECT COUNT(*) FROM excluded_logs)    AS logs_excluded,
                    (SELECT COUNT(*) FROM to_skip)          AS items_processing_skipped
                """,
                batch_id, mode,
            )
            await conn.execute(
                """
                UPDATE query_batches
                SET status = 'running', completed_at = NULL
                WHERE id = $1 AND status = 'completed'
                """,
                batch_id,
            )
    return dict(row) if row else {
        "items_reset": 0, "logs_excluded": 0, "items_processing_skipped": 0,
    }
