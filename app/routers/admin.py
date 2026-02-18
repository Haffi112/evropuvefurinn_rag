import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.middleware.review_auth import hash_password
from app.models.schemas import QueryLogEntry, QueryLogListResponse, QueryLogStatsResponse
from app.models.review_schemas import (
    ReviewPasswordReset,
    ReviewStatusUpdate,
    ReviewUserCreate,
    ReviewUserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/query-log",
    response_model=QueryLogListResponse,
    summary="List query logs",
    description="Paginated, filterable log of all queries made to the API. "
    "Supports filtering by date range, model, cache status, scope-declined "
    "flag, and free-text search over query content.",
)
async def list_query_logs(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    cached: bool | None = Query(default=None),
    model_used: str | None = Query(default=None),
    scope_declined: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
):
    logs, total = await db.list_query_logs(
        page=page,
        per_page=per_page,
        date_from=date_from,
        date_to=date_to,
        cached=cached,
        model_used=model_used,
        scope_declined=scope_declined,
        search=search,
    )
    # Parse JSONB references from string if needed
    for log in logs:
        refs = log.get("references")
        if isinstance(refs, str):
            log["references"] = json.loads(refs)
    total_pages = (total + per_page - 1) // per_page
    return QueryLogListResponse(
        logs=logs, total=total, page=page, per_page=per_page, total_pages=total_pages,
    )


@router.get(
    "/query-log/stats",
    response_model=QueryLogStatsResponse,
    summary="Query log statistics",
    description="Aggregate statistics: total queries, today's count, cache hit count, "
    "scope-declined count, and average latency.",
)
async def query_log_stats():
    stats = await db.get_query_log_stats()
    return QueryLogStatsResponse(**stats)


# ── Reviewer management ─────────────────────────────────────


@router.post(
    "/reviewers",
    response_model=ReviewUserResponse,
    summary="Create a reviewer account",
)
async def create_reviewer(body: ReviewUserCreate):
    existing = await db.get_review_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")
    pw_hash = hash_password(body.password)
    row = await db.create_review_user(body.username, pw_hash)
    return ReviewUserResponse(**row)


@router.get(
    "/reviewers",
    response_model=list[ReviewUserResponse],
    summary="List all reviewers",
)
async def list_reviewers():
    rows = await db.list_review_users()
    return [ReviewUserResponse(**r) for r in rows]


@router.delete(
    "/reviewers/{reviewer_id}",
    summary="Deactivate a reviewer",
)
async def deactivate_reviewer(reviewer_id: int):
    await db.deactivate_review_user(reviewer_id)
    return {"detail": "Reviewer deactivated"}


@router.put(
    "/reviewers/{reviewer_id}/reset-password",
    summary="Reset a reviewer's password",
)
async def reset_reviewer_password(reviewer_id: int, body: ReviewPasswordReset):
    pw_hash = hash_password(body.password)
    await db.reset_review_user_password(reviewer_id, pw_hash)
    return {"detail": "Password reset"}


# ── Review status ──────────────────────────────────────────


@router.patch(
    "/query-log/{query_id}/review-status",
    summary="Set review status for a query",
    description="Set the review status of a query log entry. "
    "Allowed values: pending, excluded, reviewed, approved.",
)
async def set_review_status(query_id: int, body: ReviewStatusUpdate):
    log = await db.get_query_log_detail(query_id)
    if not log:
        raise HTTPException(status_code=404, detail="Query not found")
    await db.update_review_status(query_id, body.review_status)
    return {"detail": f"Review status set to '{body.review_status}'"}
