import csv
import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.middleware.review_auth import hash_password
from app.models.schemas import (
    AdminEvaluationListResponse,
    QueryLogEntry,
    QueryLogListResponse,
    QueryLogStatsResponse,
    RetrievalAnnotation,
    RetrievalAnnotationList,
    ReviewPlaygroundRequest,
)
from app.models.review_schemas import (
    ReviewPasswordReset,
    ReviewStatusUpdate,
    ReviewUserCreate,
    ReviewUserResponse,
)
from app.services.rag_service import RAGService
from app.services.visindavefur_export import to_vv_html

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
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
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
        order=order,
    )
    # Parse JSONB columns from string if needed
    for log in logs:
        refs = log.get("references")
        if isinstance(refs, str):
            log["references"] = json.loads(refs)
        cands = log.get("retrieval_candidates")
        if isinstance(cands, str):
            log["retrieval_candidates"] = json.loads(cands)
    total_pages = (total + per_page - 1) // per_page
    return QueryLogListResponse(
        logs=logs, total=total, page=page, per_page=per_page, total_pages=total_pages,
    )


# ── Retrieval annotations ───────────────────────────────────

_ALLOWED_ANNOTATION_LABELS = {"should_cite", "correct", "irrelevant"}


class RetrievalAnnotationUpsert(BaseModel):
    article_id: str
    label: str  # 'should_cite' | 'correct' | 'irrelevant'


@router.get(
    "/query-log/{query_log_id}/annotations",
    response_model=RetrievalAnnotationList,
    summary="List retrieval annotations for a query",
    description="The admin's ground-truth labels on this query's retrieval "
    "candidates: 'should_cite' (a below-cutoff candidate the answer should "
    "have cited), 'correct' (an in-prompt reference judged correctly cited), "
    "or 'irrelevant' (an in-prompt reference judged not relevant). These rows "
    "form the seed of the retrieval evaluation set.",
)
async def list_retrieval_annotations(query_log_id: int):
    rows = await db.get_retrieval_annotations(query_log_id)
    return RetrievalAnnotationList(
        query_log_id=query_log_id,
        annotations=[
            RetrievalAnnotation(article_id=r["article_id"], label=r["label"])
            for r in rows
        ],
    )


@router.put(
    "/query-log/{query_log_id}/annotations",
    response_model=RetrievalAnnotation,
    summary="Set (upsert) a retrieval annotation",
)
async def upsert_retrieval_annotation(query_log_id: int, body: RetrievalAnnotationUpsert):
    if body.label not in _ALLOWED_ANNOTATION_LABELS:
        raise HTTPException(
            status_code=422,
            detail=f"label must be one of {sorted(_ALLOWED_ANNOTATION_LABELS)}",
        )
    if not await db.get_query_log_detail(query_log_id):
        raise HTTPException(status_code=404, detail="Query log entry not found")
    try:
        row = await db.set_retrieval_annotation(query_log_id, body.article_id, body.label)
    except Exception as exc:
        # Most likely a foreign-key violation (unknown article_id)
        raise HTTPException(status_code=422, detail=f"Could not save annotation: {exc}")
    return RetrievalAnnotation(article_id=row["article_id"], label=row["label"])


@router.delete(
    "/query-log/{query_log_id}/annotations/{article_id}",
    summary="Remove a retrieval annotation",
)
async def delete_retrieval_annotation(query_log_id: int, article_id: str):
    ok = await db.delete_retrieval_annotation(query_log_id, article_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Annotation not found")
    return {"deleted": True}


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


@router.get(
    "/query-log/export",
    summary="Export the filtered query log as CSV",
    description="Streams every query-log row matching the given filters (the same "
    "date range, model, cache, scope-declined, and search filters as the list "
    "endpoint) as a CSV file — not just the current page.",
)
async def export_query_log(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    cached: bool | None = Query(default=None),
    model_used: str | None = Query(default=None),
    scope_declined: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
):
    rows = await db.export_query_logs(
        date_from=date_from,
        date_to=date_to,
        cached=cached,
        model_used=model_used,
        scope_declined=scope_declined,
        search=search,
        order=order,
    )

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM so Excel renders Icelandic characters
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "query_text", "response_text", "model_used", "mode",
        "references_count", "scope_declined", "cached", "latency_ms",
        "review_status", "ip_address",
    ])
    for ql in rows:
        refs = ql.get("references", [])
        if isinstance(refs, str):
            refs = json.loads(refs)
        refs_count = len(refs) if isinstance(refs, list) else 0
        created = ql["created_at"]
        writer.writerow([
            ql["id"],
            created.isoformat() if hasattr(created, "isoformat") else created,
            ql["query_text"],
            ql.get("response_text", "") or "",
            ql.get("model_used", "") or "",
            ql.get("mode", ""),
            refs_count,
            ql.get("scope_declined", False),
            ql.get("cached", False),
            ql.get("latency_ms", "") if ql.get("latency_ms") is not None else "",
            ql.get("review_status", "pending"),
            ql.get("ip_address", "") or "",
        ])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="query_log_{stamp}.csv"'
        },
    )


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


_CHECKLIST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("answers_question", "Answers question"),
    ("factually_accurate", "Factually accurate"),
    ("sources_relevant", "Sources relevant"),
    ("no_hallucinations", "No hallucinations"),
    ("appropriate_scope", "Appropriate scope"),
    ("language_quality", "Language quality"),
    ("publishable_minor_edits", "Publishable w/ minor edits"),
)


def _write_evaluations_csv(rows: list[dict]) -> str:
    """Render evaluations as CSV. One row per evaluation: under multi-annotator
    that means a query reviewed by N people will produce N rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "evaluation_id", "query_log_id", "query_text", "reviewer", "mode",
        *(key for key, _ in _CHECKLIST_COLUMNS),
        "failed_checks",
        "note", "review_status", "evaluation_date",
    ])
    for r in rows:
        cl = r.get("checklist", {})
        if isinstance(cl, str):
            cl = json.loads(cl)
        failed = [
            label for key, label in _CHECKLIST_COLUMNS if cl.get(key) is not True
        ]
        writer.writerow([
            r["id"], r["query_log_id"], r["query_text"], r["reviewer_username"],
            r.get("mode", ""),
            *(cl.get(key, False) for key, _ in _CHECKLIST_COLUMNS),
            "; ".join(failed),
            r.get("note", ""),
            r["review_status"],
            r["evaluation_date"].isoformat() if hasattr(r["evaluation_date"], "isoformat") else r["evaluation_date"],
        ])
    return buf.getvalue()


def _write_flags_csv(rows: list[dict]) -> str:
    """Render source flags as CSV. One row per flag (open and resolved), each
    carrying its flag_type, the reviewer's free-text comment, the flagged
    source (article title + URL, or the raw web URL), the originating query,
    and resolution metadata."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "flag_id", "source_kind", "flag_type", "comment",
        "source_title", "source_url",
        "query_log_id", "query_text",
        "flagged_by", "flagged_at",
        "status", "resolved_by", "resolved_at",
    ])
    for r in rows:
        if r.get("source_kind") == "article":
            source_title = r.get("article_title", "")
            source_url = r.get("article_source_url", "")
        else:
            source_title = ""
            source_url = r.get("web_url", "")
        flagged_at = r.get("flagged_at")
        resolved_at = r.get("resolved_at")
        writer.writerow([
            r.get("flag_id", ""),
            r.get("source_kind", ""),
            r.get("flag_type", ""),
            r.get("comment", "") or "",
            source_title or "",
            source_url or "",
            r.get("query_log_id", "") or "",
            r.get("query_text", "") or "",
            r.get("flagged_by", ""),
            flagged_at.isoformat() if hasattr(flagged_at, "isoformat") else (flagged_at or ""),
            r.get("status", ""),
            r.get("resolved_by", "") or "",
            resolved_at.isoformat() if hasattr(resolved_at, "isoformat") else (resolved_at or ""),
        ])
    return buf.getvalue()


@router.get(
    "/reviews/export/csv",
    summary="Export evaluations as CSV",
)
async def export_evaluations_csv():
    rows = await db.get_all_evaluations_for_export()
    content = _write_evaluations_csv(rows)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="evaluations.csv"'},
    )


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)[:60]


def _slugify_ascii(text: str) -> str:
    """ASCII-only slug for HTTP Content-Disposition headers, which must be
    latin-1-safe. Folds Icelandic letters to their nearest ASCII equivalent."""
    import unicodedata

    # `ð`/`þ` have no ASCII decomposition, so map them BEFORE NFKD strips
    # them silently.
    pre = (
        text.replace("ð", "d").replace("Ð", "D")
        .replace("þ", "th").replace("Þ", "Th")
    )
    folded = unicodedata.normalize("NFKD", pre)
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return _slugify(folded)


@router.get(
    "/reviews/export/all",
    summary="Export all data as ZIP (evaluations, flagged references, articles, query log, metadata)",
)
async def export_all_data_zip():
    evals, articles, query_logs, flags = (
        await db.get_all_evaluations_for_export(),
        await db.get_all_reviewed_articles_latest(),
        await db.get_all_query_logs_for_export(),
        await db.get_all_flagged_references_for_export(),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── evaluations.csv ──
        zf.writestr("evaluations.csv", _write_evaluations_csv(evals))

        # ── flagged_references.csv ──
        # Reviewer comments on sources: which reference was flagged, the
        # comment, and why (outdated / irrelevant / untrustworthy).
        zf.writestr("flagged_references.csv", _write_flags_csv(flags))

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
            "id", "query_text", "response_text", "model_used", "mode", "references_count",
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
                ql.get("mode", ""),
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
            "total_flags": len(flags),
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


def _coerce_references(refs) -> list[dict]:
    """`query_log.references` may come back as a JSON string or as a list,
    depending on whether asyncpg's JSONB codec decoded it. Normalize."""
    if refs is None:
        return []
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except json.JSONDecodeError:
            return []
    return refs if isinstance(refs, list) else []


@router.get(
    "/reviews/{query_log_id}/export/visindavefur",
    summary="Export a single reviewed article in Vísindavefur publish format",
    description="Returns the article body as text/plain in the HTML+template "
    "flavor used by Vísindavefur (<strong>, <b>, {{footnote|...}}, "
    "{{footnote_list|}}, and a Heimildir block). Prefers the editor's "
    "reviewed_articles.edited_response when one exists; otherwise falls back "
    "to the raw LLM response.",
)
async def export_query_visindavefur(query_log_id: int):
    ql = await db.get_query_log_detail(query_log_id)
    if not ql:
        raise HTTPException(status_code=404, detail="Query log not found")

    reviewed = await db.get_latest_reviewed_article(query_log_id)
    body = (reviewed["edited_response"] if reviewed else ql.get("response_text")) or ""
    refs = _coerce_references(ql.get("references"))

    rendered = to_vv_html(body, refs)
    filename_slug = _slugify_ascii(
        (reviewed["title"] if reviewed else ql.get("query_text", "")) or "svar"
    )
    filename = f"{query_log_id}_{filename_slug or 'svar'}.html"
    return StreamingResponse(
        io.BytesIO(rendered.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/reviews/export/visindavefur",
    summary="Bulk-export reviewed articles in Vísindavefur publish format (ZIP)",
)
async def export_articles_visindavefur_zip():
    articles = await db.get_all_reviewed_articles_latest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for art in articles:
            refs = _coerce_references(art.get("references"))
            rendered = to_vv_html(art["edited_response"] or "", refs)
            slug = _slugify(art["title"])
            filename = f"{art['query_log_id']}_{slug}.html"
            zf.writestr(filename, rendered)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="reviewed_articles_visindavefur.zip"'
        },
    )


# ── Review progress analytics ───────────────────────────────

@router.get(
    "/review-stats",
    summary="Admin review progress overview",
    description="Aggregate stats for the admin review progress dashboard: "
    "status counts, weekly activity, per-reviewer leaderboard, oldest pending.",
)
async def admin_review_stats():
    return await db.get_admin_review_stats()


# ── Flagged references (admin view) ─────────────────────────

@router.get(
    "/flagged-references",
    summary="List flagged references (articles + web URLs)",
    description="Grouped by article or URL, ordered by open-flag count descending. "
    "Filter with ?resolved=true|false|all (default: false = open flags only). "
    "Also returns a separate domain aggregate for URL-based flags.",
)
async def list_flagged_references(resolved: str = "false"):
    resolved_val: bool | None
    if resolved == "true":
        resolved_val = True
    elif resolved == "all":
        resolved_val = None
    else:
        resolved_val = False
    items = await db.list_flagged_references(resolved=resolved_val)
    domains = await db.list_flagged_domains(resolved=resolved_val)
    stats = await db.get_flag_stats()
    return {"items": items, "domains": domains, "stats": stats}


@router.post(
    "/flagged-references/{flag_id}/resolve",
    summary="Mark a flag as resolved",
)
async def resolve_flag(flag_id: int):
    ok = await db.resolve_flag(flag_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Flag not found or already resolved")
    return {"resolved": True}


@router.post(
    "/flagged-references/articles/{article_id}/resolve-all",
    summary="Resolve all open flags on an article",
)
async def resolve_all_for_article(article_id: str):
    n = await db.resolve_all_flags_for_article(article_id)
    return {"resolved": n}


class _ResolveUrlBody(BaseModel):
    url: str


@router.post(
    "/flagged-references/urls/resolve-all",
    summary="Resolve all open flags on a URL",
)
async def resolve_all_for_url(body: _ResolveUrlBody):
    n = await db.resolve_all_flags_for_url(body.url)
    return {"resolved": n}


# ── Admin playground (parity with reviewer playground) ──────

def _get_rag(request: Request) -> RAGService:
    return request.app.state.rag


@router.post(
    "/playground",
    summary="Admin playground query",
    description="Submit a query from the admin playground. Logged without reviewer attribution. "
    "Supports web search mode (bypasses RAG) and SSE streaming.",
)
async def admin_playground(body: ReviewPlaygroundRequest, request: Request):
    rag = _get_rag(request)
    ip_address = request.client.host if request.client else None
    start_time = time.monotonic()

    if body.stream:
        return EventSourceResponse(
            rag.process_query_stream(
                body.query, body.top_k, body.language,
                ip_address=ip_address, start_time=start_time,
                score_threshold=body.score_threshold,
                include_thinking=body.include_thinking,
                web_search=body.web_search,
                model_override=body.model,
            )
        )

    try:
        return await rag.process_query_json(
            body.query, body.top_k, body.language,
            ip_address=ip_address, start_time=start_time,
            score_threshold=body.score_threshold,
            include_thinking=body.include_thinking,
            web_search=body.web_search,
            model_override=body.model,
        )
    except Exception:
        logger.error("Admin playground query failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Villa kom upp við úrvinnslu fyrirspurnar.")
