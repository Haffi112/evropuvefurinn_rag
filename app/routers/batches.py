"""Batch upload + monitoring endpoints.

Admin-only (Bearer API key). See spec
`docs/superpowers/specs/2026-04-18-admin-batch-and-playground-parity-design.md`.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from app.db import queries as db
from app.middleware.auth import verify_api_key
from app.models.schemas import (
    BatchCreateResponse,
    BatchDetail,
    BatchItemDetail,
    BatchListItem,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/batches",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)

MAX_QUESTIONS_PER_BATCH = 10_000


@router.post("", response_model=BatchCreateResponse, summary="Upload JSONL, create batch")
async def create_batch(file: UploadFile = File(...)):
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    items: list[dict] = []
    skipped_reasons: list[str] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            skipped_reasons.append(f"line {line_no}: invalid JSON ({e.msg})")
            continue
        qid = obj.get("id")
        qtext = obj.get("question_is") or obj.get("question")
        if not isinstance(qid, str) or not isinstance(qtext, str) or not qtext.strip():
            skipped_reasons.append(f"line {line_no}: missing 'id' or 'question_is'")
            continue
        items.append({"question_id": qid, "question_text": qtext.strip()})

    if not items:
        raise HTTPException(status_code=400, detail="No valid questions found")
    if len(items) > MAX_QUESTIONS_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Batch too large: {len(items)} questions (max {MAX_QUESTIONS_PER_BATCH})",
        )

    # Each question produces two batch_items: one RAG, one web-search
    rows = []
    for q in items:
        rows.append({**q, "mode": "rag"})
        rows.append({**q, "mode": "websearch"})

    total = len(rows)
    batch_id = await db.create_batch(file.filename or "batch.jsonl", total)
    await db.insert_batch_items(batch_id, rows)

    logger.info("Created batch id=%d filename=%s questions=%d items=%d skipped=%d",
                batch_id, file.filename, len(items), total, len(skipped_reasons))

    return BatchCreateResponse(
        id=batch_id,
        filename=file.filename or "batch.jsonl",
        total=total,
        skipped=len(skipped_reasons),
        skipped_reasons=skipped_reasons[:20],  # cap payload
    )


@router.get("", response_model=list[BatchListItem], summary="List all batches")
async def list_batches():
    rows = await db.list_batches()
    return [BatchListItem(**r) for r in rows]


@router.get("/{batch_id}", response_model=BatchDetail, summary="Get batch with items")
async def get_batch(batch_id: int):
    data = await db.get_batch_detail(batch_id)
    if not data:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = data["batch"]
    return BatchDetail(
        id=batch["id"],
        filename=batch["filename"],
        total=batch["total"],
        status=batch["status"],
        created_at=batch["created_at"],
        completed_at=batch.get("completed_at"),
        items=[BatchItemDetail(**i) for i in data["items"]],
    )


@router.post("/{batch_id}/retry-failed", summary="Bulk retry all failed items")
async def retry_failed(batch_id: int):
    n = await db.retry_failed_batch_items(batch_id)
    return {"retried": n}


@router.post("/{batch_id}/items/{item_id}/retry", summary="Retry a single failed item")
async def retry_single(batch_id: int, item_id: int):
    ok = await db.retry_batch_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found or not in failed state")
    return {"retried": 1}


@router.post("/{batch_id}/cancel", summary="Cancel remaining pending items")
async def cancel(batch_id: int):
    n = await db.cancel_batch(batch_id)
    return {"cancelled": n}
