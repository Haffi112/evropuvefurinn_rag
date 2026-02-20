import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import queries as db
from app.db.database import get_pool
from app.middleware.auth import verify_api_key
from app.models.schemas import HealthResponse, StatsResponse
from app.services import settings_service
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["operational"])


def _get_embeddings(request: Request) -> EmbeddingService:
    return request.app.state.embeddings


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the status of each backing service (Postgres, embeddings, "
    "Gemini). Returns 200 if all healthy, 503 if any service is degraded.",
)
async def health_check(request: Request):
    settings = get_settings()
    checks = {}
    all_healthy = True

    # Postgres
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["postgres"] = "connected"
    except Exception:
        checks["postgres"] = "disconnected"
        all_healthy = False

    # Embeddings (DeepInfra)
    try:
        emb: EmbeddingService = _get_embeddings(request)
        if await emb.health_check():
            checks["embeddings"] = "connected"
        else:
            checks["embeddings"] = "timeout"
            all_healthy = False
    except Exception:
        checks["embeddings"] = "unavailable"
        all_healthy = False

    # Gemini (basic check — just verify key is set)
    checks["gemini"] = "available" if settings.gemini_api_key else "not_configured"
    if not settings.gemini_api_key:
        all_healthy = False

    status_code = 200 if all_healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if all_healthy else "degraded",
            "version": settings.app_version,
            "checks": checks,
        },
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    dependencies=[Depends(verify_api_key)],
    summary="Usage statistics",
    description="Returns article counts, today's query volume, cache hit rate, "
    "Gemini quota usage (Pro/Flash), and vector index statistics.",
)
async def stats(request: Request):
    settings = get_settings()
    emb: EmbeddingService = _get_embeddings(request)

    article_count = await db.get_article_count()
    last_synced = await db.get_last_synced()
    today_queries = await db.get_today_query_count()
    cache_rate = await db.get_cache_hit_rate()

    pro_used = await db.quota_get("pro")
    flash_used = await db.quota_get("flash")

    vector_stats = await emb.get_index_stats()

    # Next midnight UTC
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if tomorrow <= now:
        from datetime import timedelta
        tomorrow += timedelta(days=1)

    return StatsResponse(
        articles={
            "total": article_count,
            "last_synced": last_synced.isoformat() if last_synced else None,
        },
        queries={
            "today": today_queries,
            "cache_hit_rate": cache_rate,
        },
        quota={
            "gemini_3_pro": {
                "used": pro_used,
                "limit": settings_service.get_int("model.pro_daily_limit"),
                "resets_at": tomorrow.isoformat(),
            },
            "gemini_3_flash": {
                "used": flash_used,
                "limit": None,
            },
        },
        vectors=vector_stats,
    )
