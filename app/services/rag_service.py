import hashlib
import json
import logging
import re
import time
import uuid

from app.config import Settings
from app.db import queries as db
from app.models.schemas import QueryResponse, Reference
from app.services import settings_service
from app.services.llm_service import LLMService
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def _query_hash(query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _build_number_map(articles: list[dict], used_ids: set[str]) -> dict[int, int]:
    """Map retrieval-rank number (1-indexed article position) → dense citation
    number (1..N_cited), skipping articles that weren't cited."""
    number_map: dict[int, int] = {}
    new_num = 0
    for i, a in enumerate(articles):
        if a["id"] in used_ids:
            new_num += 1
            number_map[i + 1] = new_num
    return number_map


def _renumber_citations(answer_text: str, number_map: dict[int, int]) -> str:
    """Rewrite inline [N] markers so they use dense 1..N_cited numbering.
    Numbers not in the mapping are left untouched (safe against non-citation
    bracket text like "[2023]")."""
    if not number_map:
        return answer_text
    return re.sub(
        r"\[(\d+)\]",
        lambda m: f"[{number_map.get(int(m.group(1)), int(m.group(1)))}]",
        answer_text,
    )


class RAGService:
    def __init__(self, settings: Settings, embeddings: EmbeddingService, llm: LLMService):
        self._settings = settings
        self._embeddings = embeddings
        self._llm = llm

    async def _log_query(
        self, query_text: str, response_text: str | None, model_used: str | None,
        references: list | None, scope_declined: bool, cached: bool,
        start_time: float | None, ip_address: str | None,
        reviewer_id: int | None = None,
    ) -> None:
        try:
            latency_ms = round((time.monotonic() - start_time) * 1000) if start_time else None
            await db.insert_query_log(
                query_text=query_text, response_text=response_text,
                model_used=model_used, references=references,
                scope_declined=scope_declined, cached=cached,
                latency_ms=latency_ms, ip_address=ip_address,
                reviewer_id=reviewer_id,
            )
        except Exception:
            logger.warning("Failed to write query log", exc_info=True)

    # ── JSON (non-streaming) mode ────────────────────────────

    async def process_query_json(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
        web_search: bool = False, reviewer_id: int | None = None,
        model_override: str | None = None,
    ) -> QueryResponse:
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query)
        threshold = score_threshold if score_threshold is not None else self._settings.rag_score_threshold

        # Web search mode — skip RAG entirely
        if web_search:
            model_used, answer_text, thinking_text, _ = await self._llm.generate_web_search_non_streaming(
                query, language, include_thinking=include_thinking,
                model_override=model_override,
            )
            response = QueryResponse(
                query=query, answer=answer_text, references=[],
                model_used=model_used, cached=False, query_id=query_id,
            )
            await self._log_query(query, answer_text, model_used,
                                  [], False, False, start_time, ip_address,
                                  reviewer_id=reviewer_id)
            return response

        # Cache check (skip when thinking — it's a debug tool)
        if not include_thinking:
            cached = await db.cache_get(qhash)
            if cached:
                resp = QueryResponse(**cached, cached=True, query_id=query_id)
                await self._log_query(query, cached.get("answer"), cached.get("model_used"),
                                      cached.get("references", []), False, True, start_time, ip_address)
                return resp

        # Scope guard
        scope = await self._llm.check_scope(query)
        if scope == "no":
            decline = (settings_service.get("prompt.decline_en") if language == "en"
                       else settings_service.get("prompt.decline_is"))
            flash_model = settings_service.get("model.flash_name")
            resp = QueryResponse(
                query=query, answer=decline, references=[],
                model_used=flash_model,
                cached=False, query_id=query_id, scope_declined=True,
            )
            await self._log_query(query, decline, flash_model,
                                  [], True, False, start_time, ip_address)
            return resp

        # Vector search
        matches = await self._embeddings.query(query, top_k=top_k)
        article_ids = [m["id"] for m in matches if m["score"] >= threshold]
        if not article_ids:
            no_result = (settings_service.get("prompt.no_results_en") if language == "en"
                         else settings_service.get("prompt.no_results_is"))
            flash_model = settings_service.get("model.flash_name")
            resp = QueryResponse(
                query=query, answer=no_result,
                references=[], model_used=flash_model,
                cached=False, query_id=query_id,
            )
            await self._log_query(query, no_result, flash_model,
                                  [], False, False, start_time, ip_address)
            return resp

        # Fetch full articles, preserving vector-score order (ANY() doesn't)
        articles = await db.get_articles_by_ids(article_ids)
        order = {aid: i for i, aid in enumerate(article_ids)}
        articles.sort(key=lambda a: order[a["id"]])

        # Generate answer (structured output returns references_used)
        score_map = {m["id"]: m["score"] for m in matches}
        model_used, answer_text, thinking_text, references_used = await self._llm.generate_non_streaming(
            query, articles, language, include_thinking=include_thinking,
            model_override=model_override,
        )

        # Build references only from articles the model actually cited, using
        # dense 1..N_cited numbering (retrieval-rank gaps collapsed).
        used_ids = set(references_used)
        number_map = _build_number_map(articles, used_ids)
        references = [
            Reference(
                number=number_map[i + 1],
                id=a["id"], title=a["title"], source_url=a["source_url"],
                date=a["date"], relevance_score=round(score_map.get(a["id"], 0), 4),
            )
            for i, a in enumerate(articles)
            if a["id"] in used_ids
        ]

        # Rewrite inline [N] markers in the answer to the dense numbering
        answer_text = _renumber_citations(answer_text, number_map)

        if used_ids and not re.search(r"\[\d+\]", answer_text):
            logger.warning("Answer has references but no [N] citations (query_id=%s)", query_id)

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
                              [r.model_dump() for r in references], False, False, start_time, ip_address,
                              reviewer_id=reviewer_id)
        return response

    # ── SSE (streaming) mode ─────────────────────────────────

    async def process_query_stream(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
        web_search: bool = False, reviewer_id: int | None = None,
        model_override: str | None = None,
    ):
        """Yields dicts with 'event' and 'data' keys for sse-starlette."""
        query_id = f"q_{uuid.uuid4().hex[:12]}"

        try:
            # Web search mode — skip RAG entirely
            if web_search:
                yield {"event": "status", "data": json.dumps({"stage": "generating", "message": "Web search..."})}
                model_used, token_stream = await self._llm.generate_web_search_stream(
                    query, language, include_thinking=include_thinking,
                    model_override=model_override,
                )
                full_answer = []
                async for chunk_type, chunk_text in token_stream:
                    if chunk_type == "thinking":
                        yield {"event": "thinking", "data": json.dumps({"text": chunk_text})}
                    elif chunk_type == "references":
                        pass  # no structured refs in web search mode
                    else:
                        full_answer.append(chunk_text)
                        yield {"event": "token", "data": json.dumps({"text": chunk_text})}
                yield {"event": "references", "data": json.dumps({"references": []})}
                yield {"event": "done", "data": json.dumps({"model_used": model_used, "cached": False, "query_id": query_id})}
                answer_text = "".join(full_answer)
                await self._log_query(query, answer_text, model_used,
                                      [], False, False, start_time, ip_address,
                                      reviewer_id=reviewer_id)
                return

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
            scope = await self._llm.check_scope(query)
            if scope == "no":
                decline = (settings_service.get("prompt.decline_en") if language == "en"
                           else settings_service.get("prompt.decline_is"))
                flash_model = settings_service.get("model.flash_name")
                for word in decline.split():
                    yield {"event": "token", "data": json.dumps({"text": word + " "})}
                yield {"event": "references", "data": json.dumps({"references": []})}
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "model_used": flash_model,
                        "cached": False, "query_id": query_id, "scope_declined": True,
                    }),
                }
                await self._log_query(query, decline, flash_model,
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
                no_result = (settings_service.get("prompt.no_results_en") if language == "en"
                             else settings_service.get("prompt.no_results_is"))
                yield {"event": "token", "data": json.dumps({"text": no_result})}
                yield {"event": "references", "data": json.dumps({"references": []})}
                yield {
                    "event": "done",
                    "data": json.dumps({"model_used": "none", "cached": False, "query_id": query_id}),
                }
                await self._log_query(query, no_result, "none",
                                      [], False, False, start_time, ip_address)
                return

            # Fetch full articles, preserving vector-score order (ANY() doesn't)
            articles = await db.get_articles_by_ids(article_ids)
            order = {aid: i for i, aid in enumerate(article_ids)}
            articles.sort(key=lambda a: order[a["id"]])
            score_map = {m["id"]: m["score"] for m in matches}

            # Status: generating
            yield {"event": "status", "data": json.dumps({"stage": "generating", "message": "Bý til svar..."})}

            # Stream LLM response
            model_used, token_stream = await self._llm.generate_stream(
                query, articles, language, include_thinking=include_thinking,
                model_override=model_override,
            )
            full_answer = []
            used_ids: set[str] = set()
            async for chunk_type, chunk_text in token_stream:
                if chunk_type == "thinking":
                    yield {"event": "thinking", "data": json.dumps({"text": chunk_text})}
                elif chunk_type == "references":
                    used_ids = set(chunk_text)  # chunk_text is a list of IDs
                else:
                    full_answer.append(chunk_text)
                    yield {"event": "token", "data": json.dumps({"text": chunk_text})}

            # Build references only from articles the model actually cited, using
            # dense 1..N_cited numbering. Tokens were streamed with the model's raw
            # (retrieval-rank) numbers; we emit a renumbered `answer_final` event
            # below so the client replaces its accumulated state with the corrected
            # text.
            number_map = _build_number_map(articles, used_ids)
            references = [
                {
                    "number": number_map[i + 1],
                    "id": a["id"], "title": a["title"], "source_url": a["source_url"],
                    "date": a["date"], "relevance_score": round(score_map.get(a["id"], 0), 4),
                }
                for i, a in enumerate(articles)
                if a["id"] in used_ids
            ]

            answer_text = _renumber_citations("".join(full_answer), number_map)
            yield {"event": "answer_final", "data": json.dumps({"text": answer_text})}
            yield {"event": "references", "data": json.dumps({"references": references})}
            if used_ids and not re.search(r"\[\d+\]", answer_text):
                logger.warning("Answer has references but no [N] citations (query_id=%s)", query_id)

            # Done
            yield {
                "event": "done",
                "data": json.dumps({"model_used": model_used, "cached": False, "query_id": query_id}),
            }

            # Store in cache (skip when thinking)
            if not include_thinking:
                cache_data = {
                    "query": query, "answer": answer_text, "references": references,
                    "model_used": model_used,
                }
                await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)
            await self._log_query(query, answer_text, model_used,
                                  references, False, False, start_time, ip_address,
                                  reviewer_id=reviewer_id)

        except Exception:
            logger.error("Stream failed for query_id=%s", query_id, exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "Villa kom upp við úrvinnslu fyrirspurnar. Reyndu aftur.",
                    "query_id": query_id,
                }),
            }
