import hashlib
import json
import logging
import time
import uuid

from app.config import Settings
from app.db import queries as db
from app.models.schemas import QueryResponse, Reference
from app.services.gemini_service import GeminiService
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

DECLINE_IS = (
    "Þessi spurning fellur utan efnissviðs Evrópuvefsins. "
    "Evrópuvefurinn svarar spurningum um Evrópusambandið, EES og tengsl Íslands við Evrópu. "
    "Vinsamlegast reyndu aftur með spurningu um þessi efni."
)
DECLINE_EN = (
    "This question is outside the scope of Evrópuvefurinn. "
    "Evrópuvefurinn answers questions about the European Union, EEA, and Iceland's relations with Europe. "
    "Please try again with a question about these topics."
)


def _query_hash(query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


class RAGService:
    def __init__(self, settings: Settings, embeddings: EmbeddingService, gemini: GeminiService):
        self._settings = settings
        self._embeddings = embeddings
        self._gemini = gemini

    async def _log_query(
        self, query_text: str, response_text: str | None, model_used: str | None,
        references: list | None, scope_declined: bool, cached: bool,
        start_time: float | None, ip_address: str | None,
    ) -> None:
        try:
            latency_ms = round((time.monotonic() - start_time) * 1000) if start_time else None
            await db.insert_query_log(
                query_text=query_text, response_text=response_text,
                model_used=model_used, references=references,
                scope_declined=scope_declined, cached=cached,
                latency_ms=latency_ms, ip_address=ip_address,
            )
        except Exception:
            logger.warning("Failed to write query log", exc_info=True)

    # ── JSON (non-streaming) mode ────────────────────────────

    async def process_query_json(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
    ) -> QueryResponse:
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query)
        threshold = score_threshold if score_threshold is not None else self._settings.rag_score_threshold

        # Cache check (skip when thinking — it's a debug tool)
        if not include_thinking:
            cached = await db.cache_get(qhash)
            if cached:
                resp = QueryResponse(**cached, cached=True, query_id=query_id)
                await self._log_query(query, cached.get("answer"), cached.get("model_used"),
                                      cached.get("references", []), False, True, start_time, ip_address)
                return resp

        # Scope guard
        scope = await self._gemini.check_scope(query)
        if scope == "no":
            decline = DECLINE_EN if language == "en" else DECLINE_IS
            resp = QueryResponse(
                query=query, answer=decline, references=[],
                model_used=self._settings.gemini_flash_model,
                cached=False, query_id=query_id, scope_declined=True,
            )
            await self._log_query(query, decline, self._settings.gemini_flash_model,
                                  [], True, False, start_time, ip_address)
            return resp

        # Vector search
        matches = await self._embeddings.query(query, top_k=top_k)
        article_ids = [m["id"] for m in matches if m["score"] >= threshold]
        if not article_ids:
            no_result = ("Engar greinar fundust í þekkingargrunni sem tengjast þessari spurningu."
                         if language != "en"
                         else "No articles found in the knowledge base related to this question.")
            resp = QueryResponse(
                query=query, answer=no_result,
                references=[], model_used=self._settings.gemini_flash_model,
                cached=False, query_id=query_id,
            )
            await self._log_query(query, no_result, self._settings.gemini_flash_model,
                                  [], False, False, start_time, ip_address)
            return resp

        # Fetch full articles
        articles = await db.get_articles_by_ids(article_ids)

        # Build references with scores
        score_map = {m["id"]: m["score"] for m in matches}
        references = [
            Reference(
                id=a["id"], title=a["title"], source_url=a["source_url"],
                date=a["date"], relevance_score=round(score_map.get(a["id"], 0), 4),
            )
            for a in articles
        ]

        # Generate answer
        model_used, answer_text, thinking_text = await self._gemini.generate_non_streaming(
            query, articles, language, include_thinking=include_thinking,
        )

        response = QueryResponse(
            query=query, answer=answer_text, references=references,
            model_used=model_used, cached=False, query_id=query_id,
        )

        # Store in cache (skip when thinking)
        if not include_thinking:
            cache_data = response.model_dump()
            cache_data.pop("cached", None)
            cache_data.pop("query_id", None)
            refs_dicts = [r.model_dump() for r in references]
            cache_data["references"] = refs_dicts
            await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)

        await self._log_query(query, answer_text, model_used,
                              [r.model_dump() for r in references], False, False, start_time, ip_address)
        return response

    # ── SSE (streaming) mode ─────────────────────────────────

    async def process_query_stream(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
    ):
        """Yields dicts with 'event' and 'data' keys for sse-starlette."""
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query)
        threshold = score_threshold if score_threshold is not None else self._settings.rag_score_threshold

        # Cache check (skip when thinking — it's a debug tool)
        if not include_thinking:
            cached = await db.cache_get(qhash)
            if cached:
                yield {"event": "status", "data": json.dumps({"stage": "complete", "message": "Cached response"})}
                for word in cached.get("answer", "").split():
                    yield {"event": "token", "data": json.dumps({"text": word + " "})}
                yield {"event": "references", "data": json.dumps({"references": cached.get("references", [])})}
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "model_used": cached.get("model_used", "cache"),
                        "cached": True, "query_id": query_id,
                    }),
                }
                await self._log_query(query, cached.get("answer"), cached.get("model_used"),
                                      cached.get("references", []), False, True, start_time, ip_address)
                return

        # Status: searching
        yield {"event": "status", "data": json.dumps({"stage": "searching", "message": "Leita í þekkingargrunni..."})}

        # Scope guard
        scope = await self._gemini.check_scope(query)
        if scope == "no":
            decline = DECLINE_EN if language == "en" else DECLINE_IS
            for word in decline.split():
                yield {"event": "token", "data": json.dumps({"text": word + " "})}
            yield {"event": "references", "data": json.dumps({"references": []})}
            yield {
                "event": "done",
                "data": json.dumps({
                    "model_used": self._settings.gemini_flash_model,
                    "cached": False, "query_id": query_id, "scope_declined": True,
                }),
            }
            await self._log_query(query, decline, self._settings.gemini_flash_model,
                                  [], True, False, start_time, ip_address)
            return

        # Vector search
        matches = await self._embeddings.query(query, top_k=top_k)
        article_ids = [m["id"] for m in matches if m["score"] >= threshold]

        top_score = matches[0]["score"] if matches else 0.0
        yield {
            "event": "context",
            "data": json.dumps({"articles_found": len(article_ids), "top_score": round(top_score, 4)}),
        }

        if not article_ids:
            no_result = ("No articles found related to this question."
                         if language == "en"
                         else "Engar greinar fundust sem tengjast þessari spurningu.")
            yield {"event": "token", "data": json.dumps({"text": no_result})}
            yield {"event": "references", "data": json.dumps({"references": []})}
            yield {
                "event": "done",
                "data": json.dumps({"model_used": "none", "cached": False, "query_id": query_id}),
            }
            await self._log_query(query, no_result, "none",
                                  [], False, False, start_time, ip_address)
            return

        # Fetch full articles
        articles = await db.get_articles_by_ids(article_ids)
        score_map = {m["id"]: m["score"] for m in matches}

        # Status: generating
        yield {"event": "status", "data": json.dumps({"stage": "generating", "message": "Bý til svar..."})}

        # Stream LLM response
        model_used, token_stream = await self._gemini.generate_stream(
            query, articles, language, include_thinking=include_thinking,
        )
        full_answer = []
        async for chunk_type, chunk_text in token_stream:
            if chunk_type == "thinking":
                yield {"event": "thinking", "data": json.dumps({"text": chunk_text})}
            else:
                full_answer.append(chunk_text)
                yield {"event": "token", "data": json.dumps({"text": chunk_text})}

        # References
        references = [
            {
                "id": a["id"], "title": a["title"], "source_url": a["source_url"],
                "date": a["date"], "relevance_score": round(score_map.get(a["id"], 0), 4),
            }
            for a in articles
        ]
        yield {"event": "references", "data": json.dumps({"references": references})}

        # Done
        yield {
            "event": "done",
            "data": json.dumps({"model_used": model_used, "cached": False, "query_id": query_id}),
        }

        # Store in cache (skip when thinking)
        answer_text = "".join(full_answer)
        if not include_thinking:
            cache_data = {
                "query": query, "answer": answer_text, "references": references,
                "model_used": model_used,
            }
            await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)
        await self._log_query(query, answer_text, model_used,
                              references, False, False, start_time, ip_address)
