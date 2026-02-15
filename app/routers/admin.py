import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.models.schemas import QueryLogEntry, QueryLogListResponse, QueryLogStatsResponse

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
