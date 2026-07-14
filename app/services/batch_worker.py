"""Single-worker asyncio task that drains the batch_items queue.

Runs one FIFO batch at a time. Respects neither cache nor daily Pro quota
(batch jobs always use `model_override='pro'`). Retries failed calls up to
3 times with exponential backoff (2s, 4s, 8s) before marking an item failed.
"""
import asyncio
import logging

from app.config import get_settings
from app.db import queries as db
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL_SEC = 5
BACKOFF_BASE_SEC = 2  # 2s, 4s, 8s


class BatchWorker:
    def __init__(self, rag: RAGService, batch_user_id: int):
        self._rag = rag
        self._batch_user_id = batch_user_id
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        reset = await db.reset_stale_processing()
        if reset:
            logger.info("Batch worker: reset %d stale 'processing' items to 'pending'", reset)
        self._task = asyncio.create_task(self._run(), name="batch-worker")
        logger.info("Batch worker started (user_id=%d)", self._batch_user_id)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Batch worker stopped")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                item = await db.claim_next_batch_item()
            except Exception:
                logger.error("Batch worker: failed to claim item", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            if not item:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=POLL_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    pass
                continue

            await self._process_item(item)

    async def _process_item(self, item: dict) -> None:
        item_id = item["id"]
        batch_id = item["batch_id"]
        mode = item["mode"]
        question = item["question_text"]
        retry_count = item["retry_count"]

        logger.info(
            "Batch worker: processing item_id=%d batch_id=%d mode=%s attempt=%d",
            item_id, batch_id, mode, retry_count + 1,
        )

        try:
            response = await self._rag.process_query_json(
                query=question,
                top_k=get_settings().rag_top_k,
                language="is",
                model_override="pro",
                web_search=(mode == "websearch"),
                reviewer_id=self._batch_user_id,
                skip_cache=True,
            )
            query_log_id = response.query_log_id
            if query_log_id is None:
                # Logging failed — treat as error so we can retry/surface it
                raise RuntimeError("query_log_id is None (logging failed)")

            # Guard against silent-empty responses: the upstream LLM call can
            # occasionally return an empty string without raising. Those rows
            # do get logged (so `query_log_id` is set) but they have nothing
            # useful for a reviewer. Treat that as a failure so the normal
            # retry-and-backoff path kicks in.
            if not (response.answer or "").strip():
                raise RuntimeError("empty response from model")

            await db.complete_batch_item(item_id, query_log_id)
            await db.try_complete_batch(batch_id)
            logger.info("Batch worker: item_id=%d done (query_log_id=%d)", item_id, query_log_id)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            logger.warning("Batch worker: item_id=%d failed (attempt %d/%d): %s",
                           item_id, retry_count + 1, MAX_RETRIES, err_msg)

            if retry_count + 1 >= MAX_RETRIES:
                await db.fail_batch_item(item_id, err_msg)
                await db.try_complete_batch(batch_id)
            else:
                backoff = BACKOFF_BASE_SEC * (2 ** retry_count)
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                await db.requeue_batch_item(item_id, retry_count + 1, err_msg)
