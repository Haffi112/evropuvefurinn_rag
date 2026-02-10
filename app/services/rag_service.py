import hashlib
import json
import logging
import uuid

from app.config import Settings
from app.db import queries as db
from app.models.schemas import QueryResponse, Reference
from app.services.gemini_service import GeminiService
from app.services.pinecone_service import PineconeService

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
    def __init__(self, settings: Settings, pinecone: PineconeService, gemini: GeminiService):
        self._settings = settings
        self._pinecone = pinecone
        self._gemini = gemini

    # ── JSON (non-streaming) mode ────────────────────────────

    async def process_query_json(self, query: str, top_k: int, language: str) -> QueryResponse:
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query)

        # Cache check
        cached = await db.cache_get(qhash)
        if cached:
            return QueryResponse(**cached, cached=True, query_id=query_id)

        # Scope guard
        scope = await self._gemini.check_scope(query)
        if scope == "no":
            decline = DECLINE_EN if language == "en" else DECLINE_IS
            return QueryResponse(
                query=query, answer=decline, references=[],
                model_used=self._settings.gemini_flash_model,
                cached=False, query_id=query_id, scope_declined=True,
            )

        # Vector search
        matches = await self._pinecone.query(query, top_k=top_k)
        article_ids = [m["id"] for m in matches if m["score"] >= self._settings.rag_score_threshold]
        if not article_ids:
            return QueryResponse(
                query=query,
                answer="Engar greinar fundust í þekkingargrunni sem tengjast þessari spurningu."
                       if language != "en"
                       else "No articles found in the knowledge base related to this question.",
                references=[], model_used=self._settings.gemini_flash_model,
                cached=False, query_id=query_id,
            )

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
        model_used, answer_text = await self._gemini.generate_non_streaming(query, articles, language)

        response = QueryResponse(
            query=query, answer=answer_text, references=references,
            model_used=model_used, cached=False, query_id=query_id,
        )

        # Store in cache
        cache_data = response.model_dump()
        cache_data.pop("cached", None)
        cache_data.pop("query_id", None)
        # Convert Reference objects and datetimes to serialisable form
        cache_data["references"] = [r.model_dump() for r in references]
        await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)

        return response

    # ── SSE (streaming) mode ─────────────────────────────────

    async def process_query_stream(self, query: str, top_k: int, language: str):
        """Yields dicts with 'event' and 'data' keys for sse-starlette."""
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query)

        # Cache check
        cached = await db.cache_get(qhash)
        if cached:
            # Replay cached answer as fast stream
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
            return

        # Vector search
        matches = await self._pinecone.query(query, top_k=top_k)
        article_ids = [m["id"] for m in matches if m["score"] >= self._settings.rag_score_threshold]

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
            return

        # Fetch full articles
        articles = await db.get_articles_by_ids(article_ids)
        score_map = {m["id"]: m["score"] for m in matches}

        # Status: generating
        yield {"event": "status", "data": json.dumps({"stage": "generating", "message": "Bý til svar..."})}

        # Stream LLM response
        model_used, token_stream = await self._gemini.generate_stream(query, articles, language)
        full_answer = []
        async for chunk in token_stream:
            full_answer.append(chunk)
            yield {"event": "token", "data": json.dumps({"text": chunk})}

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

        # Store in cache (fire-and-forget style, but within same coroutine)
        answer_text = "".join(full_answer)
        cache_data = {
            "query": query, "answer": answer_text, "references": references,
            "model_used": model_used,
        }
        await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)
