import csv
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.middleware.review_auth import hash_password
from app.models.schemas import (
    AdminEvaluationListResponse,
    QueryLogEntry,
    QueryLogListResponse,
    QueryLogStatsResponse,
)
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


# ── Reviews listing & export ──────────────────────────────


@router.get(
    "/reviews",
    response_model=AdminEvaluationListResponse,
    summary="List all evaluations",
    description="Paginated list of all reviewer evaluations with checklist details.",
)
async def list_reviews(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=200),
    review_status: str | None = Query(default=None),
    reviewer_id: int | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
):
    rows, total = await db.list_evaluations_for_admin(
        page=page, per_page=per_page,
        review_status=review_status, reviewer_id=reviewer_id, search=search,
    )
    total_pages = (total + per_page - 1) // per_page
    return AdminEvaluationListResponse(
        evaluations=rows, total=total, page=page,
        per_page=per_page, total_pages=total_pages,
    )


@router.get(
    "/reviews/export/csv",
    summary="Export evaluations as CSV",
)
async def export_evaluations_csv():
    rows = await db.get_all_evaluations_for_export()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "evaluation_id", "query_log_id", "query_text", "reviewer",
        "answers_question", "factually_accurate", "sources_relevant",
        "no_hallucinations", "appropriate_scope", "language_quality",
        "note", "review_status", "evaluation_date",
    ])
    for r in rows:
        cl = r.get("checklist", {})
        if isinstance(cl, str):
            cl = json.loads(cl)
        writer.writerow([
            r["id"], r["query_log_id"], r["query_text"], r["reviewer_username"],
            cl.get("answers_question", False),
            cl.get("factually_accurate", False),
            cl.get("sources_relevant", False),
            cl.get("no_hallucinations", False),
            cl.get("appropriate_scope", False),
            cl.get("language_quality", False),
            r.get("note", ""),
            r["review_status"],
            r["evaluation_date"].isoformat() if hasattr(r["evaluation_date"], "isoformat") else r["evaluation_date"],
        ])
    content = buf.getvalue()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="evaluations.csv"'},
    )


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)[:60]


@router.get(
    "/reviews/export/all",
    summary="Export all data as ZIP (evaluations, articles, query log, metadata)",
)
async def export_all_data_zip():
    evals, articles, query_logs = (
        await db.get_all_evaluations_for_export(),
        await db.get_all_reviewed_articles_latest(),
        await db.get_all_query_logs_for_export(),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── evaluations.csv ──
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow([
            "evaluation_id", "query_log_id", "query_text", "reviewer",
            "answers_question", "factually_accurate", "sources_relevant",
            "no_hallucinations", "appropriate_scope", "language_quality",
            "note", "review_status", "evaluation_date",
        ])
        for r in evals:
            cl = r.get("checklist", {})
            if isinstance(cl, str):
                cl = json.loads(cl)
            writer.writerow([
                r["id"], r["query_log_id"], r["query_text"], r["reviewer_username"],
                cl.get("answers_question", False),
                cl.get("factually_accurate", False),
                cl.get("sources_relevant", False),
                cl.get("no_hallucinations", False),
                cl.get("appropriate_scope", False),
                cl.get("language_quality", False),
                r.get("note", ""),
                r["review_status"],
                r["evaluation_date"].isoformat() if hasattr(r["evaluation_date"], "isoformat") else r["evaluation_date"],
            ])
        zf.writestr("evaluations.csv", csv_buf.getvalue())

        # ── reviewed_articles/ ──
        for art in articles:
            refs = art.get("references", [])
            if isinstance(refs, str):
                refs = json.loads(refs)
            created = art["created_at"]
            date_str = created.isoformat() if hasattr(created, "isoformat") else str(created)
            lines = [
                "---",
                f'title: "{art["title"]}"',
                f'query: "{art["query_text"]}"',
                f"date: {date_str}",
                f"version: {art['version']}",
                "---",
                "",
                f"# {art['title']}",
                "",
                art["edited_response"],
                "",
            ]
            if refs:
                lines.append("## References")
                lines.append("")
                for ref in refs:
                    title = ref.get("title", "Untitled")
                    url = ref.get("source_url", "")
                    lines.append(f"- [{title}]({url})")
                lines.append("")
            slug = _slugify(art["title"])
            filename = f"reviewed_articles/{art['query_log_id']}_{slug}.md"
            zf.writestr(filename, "\n".join(lines))

        # ── query_log.csv ──
        ql_buf = io.StringIO()
        ql_writer = csv.writer(ql_buf)
        ql_writer.writerow([
            "id", "query_text", "response_text", "model_used", "references_count",
            "scope_declined", "cached", "latency_ms", "review_status", "created_at",
        ])
        for ql in query_logs:
            refs = ql.get("references", [])
            if isinstance(refs, str):
                refs = json.loads(refs)
            refs_count = len(refs) if isinstance(refs, list) else 0
            ql_writer.writerow([
                ql["id"], ql["query_text"], ql.get("response_text", ""),
                ql.get("model_used", ""),
                refs_count,
                ql.get("scope_declined", False),
                ql.get("cached", False),
                ql.get("latency_ms", ""),
                ql.get("review_status", "pending"),
                ql["created_at"].isoformat() if hasattr(ql["created_at"], "isoformat") else ql["created_at"],
            ])
        zf.writestr("query_log.csv", ql_buf.getvalue())

        # ── metadata.json ──
        metadata = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "total_queries": len(query_logs),
            "total_evaluations": len(evals),
            "total_articles": len(articles),
        }
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="evropuvefur_all_data.zip"'},
    )


@router.get(
    "/reviews/export/articles",
    summary="Export reviewed articles as ZIP of markdown files",
)
async def export_articles_zip():
    articles = await db.get_all_reviewed_articles_latest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for art in articles:
            refs = art.get("references", [])
            if isinstance(refs, str):
                refs = json.loads(refs)
            created = art["created_at"]
            date_str = created.isoformat() if hasattr(created, "isoformat") else str(created)
            lines = [
                "---",
                f'title: "{art["title"]}"',
                f'query: "{art["query_text"]}"',
                f"date: {date_str}",
                f"version: {art['version']}",
                "---",
                "",
                f"# {art['title']}",
                "",
                art["edited_response"],
                "",
            ]
            if refs:
                lines.append("## References")
                lines.append("")
                for ref in refs:
                    title = ref.get("title", "Untitled")
                    url = ref.get("source_url", "")
                    lines.append(f"- [{title}]({url})")
                lines.append("")
            slug = _slugify(art["title"])
            filename = f"{art['query_log_id']}_{slug}.md"
            zf.writestr(filename, "\n".join(lines))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="reviewed_articles.zip"'},
    )
