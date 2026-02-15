import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.middleware.rate_limit import limiter
from app.models.schemas import (
    ArticleCreate,
    ArticleFull,
    ArticleListResponse,
    ArticleResponse,
    BulkUpsertRequest,
    BulkUpsertResponse,
    DeleteResponse,
)
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["articles"])


def _get_embeddings(request: Request) -> EmbeddingService:
    return request.app.state.embeddings


# ── POST /articles/bulk  (defined before /{article_id}) ─────

@router.post("/articles/bulk", response_model=BulkUpsertResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/minute")
async def bulk_upsert_articles(request: Request, body: BulkUpsertRequest):
    emb = _get_embeddings(request)
    created = updated = failed = 0
    errors: list[dict] = []

    for i, article in enumerate(body.articles):
        try:
            article_dict = article.model_dump()
            row, was_created = await db.upsert_article(article_dict)
            await emb.upsert_article(article_dict)
            if was_created:
                created += 1
            else:
                updated += 1
                await db.cache_invalidate_by_article(article.id)
        except Exception as exc:
            failed += 1
            errors.append({"id": article.id if article.id else None, "error": str(exc)})
            logger.warning("Bulk upsert failed for index %d: %s", i, exc)

    return BulkUpsertResponse(
        processed=created + updated,
        created=created,
        updated=updated,
        failed=failed,
        errors=errors,
    )


# ── POST /articles ──────────────────────────────────────────

@router.post("/articles", response_model=ArticleResponse, status_code=201, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/minute")
async def create_article(request: Request, article: ArticleCreate):
    emb = _get_embeddings(request)

    if await db.article_exists(article.id):
        raise HTTPException(status_code=409, detail=f"Article {article.id} already exists. Use PUT to update.")

    article_dict = article.model_dump()
    row = await db.insert_article(article_dict)
    await emb.upsert_article(article_dict)

    return ArticleResponse(
        id=row["id"],
        title=row["title"],
        source_url=row["source_url"],
        created_at=row["created_at"],
        vector_indexed=True,
    )


# ── PUT /articles/{article_id} ──────────────────────────────

@router.put("/articles/{article_id}", response_model=ArticleResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/minute")
async def update_article(request: Request, article_id: str, article: ArticleCreate):
    emb = _get_embeddings(request)

    if not await db.article_exists(article_id):
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    article_dict = article.model_dump()
    row = await db.update_article(article_id, article_dict)
    await emb.upsert_article({**article_dict, "id": article_id})
    await db.cache_invalidate_by_article(article_id)

    return ArticleResponse(
        id=row["id"],
        title=row["title"],
        source_url=row["source_url"],
        updated_at=row["updated_at"],
        vector_indexed=True,
    )


# ── DELETE /articles/{article_id} ───────────────────────────

@router.delete("/articles/{article_id}", response_model=DeleteResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("100/minute")
async def delete_article(request: Request, article_id: str):
    if not await db.article_exists(article_id):
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    await db.delete_article(article_id)
    await db.cache_invalidate_by_article(article_id)

    return DeleteResponse(id=article_id, deleted_at=datetime.now(timezone.utc))


# ── GET /articles/{article_id} ──────────────────────────────

@router.get("/articles/{article_id}", response_model=ArticleFull)
@limiter.limit("100/minute")
async def get_article(request: Request, article_id: str):
    row = await db.get_article(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return ArticleFull(**row)


# ── GET /articles ────────────────────────────────────────────

@router.get("/articles", response_model=ArticleListResponse)
@limiter.limit("100/minute")
async def list_articles(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
):
    articles, total = await db.list_articles(page, per_page)
    total_pages = (total + per_page - 1) // per_page
    return ArticleListResponse(
        articles=articles,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )
