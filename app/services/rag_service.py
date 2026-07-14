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
from app.services.visindavefur_export import to_vv_html

logger = logging.getLogger(__name__)

# How many fused retrieval candidates are kept in the query log. Wider than
# top_k on purpose: the admin can spot articles that *should* have been cited
# but ranked below the cutoff, and label them for the evaluation set.
CANDIDATE_LOG_SIZE = 30


def _query_hash(query: str, model_role: str = "auto") -> str:
    """SHA-256 of normalised query text, namespaced by model role.

    The role prefix ensures Pro and Flash answers occupy separate cache
    rows in `query_cache`, so a `model: "pro"` request never returns a
    cached Flash answer and vice versa. Role ``"auto"`` (default) preserves
    the existing single-namespace behaviour for callers that don't pass a
    role (the admin playground and the batch worker).
    """
    normalized = f"{model_role}|{query.strip().lower()}"
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


_REFS_HEADING_RE = re.compile(
    r"^(##\s+(?:Heimildir|References)\s*)$", re.MULTILINE
)
_REFS_ITEM_RE = re.compile(r"^\s*-\s*\[(\d+)\]\s*(.+?)\s*$")


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]\>]+")


def _reconcile_web_search_citations(answer: str) -> str:
    """Repair numbering drift in web-search answers.

    Models occasionally mis-number citations — an inline `[[5]](some-url)`
    may point at a source the References list happens to label `[4]`, or
    inline numbers outrun the references list entirely. This function
    rebuilds both sides to a single canonical dense 1..K ordering by:

    1. Parsing each `- [N] ...URL...` entry from the References section.
    2. For each inline citation `[[N]](URL)` or bare `[N]`, resolving its
       canonical identity — preferring URL match against References when
       we have a URL, falling back to the inline number otherwise.
    3. Assigning new 1..K numbers in the order citations first appear in
       the body, then rewriting both the body and the References list to
       match.

    Returns the input unchanged if there is no References/Heimildir heading
    or no inline citations.
    """
    if not answer:
        return answer

    heading_match = _REFS_HEADING_RE.search(answer)
    if not heading_match:
        return answer

    body = answer[: heading_match.start()]
    heading_line = heading_match.group(1).rstrip()
    refs_section = answer[heading_match.end():]

    # Parse References entries: preserve insertion order
    ref_by_num: dict[int, str] = {}
    refnum_by_url: dict[str, int] = {}
    for line in refs_section.splitlines():
        m = _REFS_ITEM_RE.match(line)
        if not m:
            continue
        n = int(m.group(1))
        text = m.group(2)
        if n not in ref_by_num:
            ref_by_num[n] = text
        url_m = _URL_IN_TEXT_RE.search(text)
        if url_m:
            url = url_m.group(0).rstrip(".,;)'")
            refnum_by_url.setdefault(url, n)

    # Walk inline citations in order. Each one gets a canonical source number:
    # prefer URL match against References, fall back to the inline number.
    canonical_order: list[int] = []  # canonical refnum in first-seen order
    citation_canonicals: list[int] = []  # parallel list for each citation token (to allow rewriting)

    # Match either `[[N]](URL)` or bare `[N]` (outside of links).
    citation_re = re.compile(r"\[\[(\d+)\]\]\((https?://[^)]+)\)|\[(\d+)\]")

    tokens: list[tuple[int, int, int, int]] = []  # (start, end, inline_num, canonical_num)
    for m in citation_re.finditer(body):
        if m.group(1) is not None:
            inline_n = int(m.group(1))
            url = m.group(2).rstrip(".,;")
            canonical = refnum_by_url.get(url, inline_n)
        else:
            inline_n = int(m.group(3))
            canonical = inline_n
        tokens.append((m.start(), m.end(), inline_n, canonical))
        if canonical not in canonical_order:
            canonical_order.append(canonical)

    if not canonical_order:
        return answer

    canonical_to_new = {c: i + 1 for i, c in enumerate(canonical_order)}

    # Rewrite body by walking tokens in reverse to preserve offsets
    new_body = body
    for start, end, inline_n, canonical in reversed(tokens):
        new_n = canonical_to_new[canonical]
        original = body[start:end]
        # Replace the first `[<digits>]` inside the matched token (preserves
        # any trailing `(URL)` and both outer brackets of `[[N]](URL)`)
        replaced = re.sub(rf"\[{inline_n}\]", f"[{new_n}]", original, count=1)
        new_body = new_body[:start] + replaced + new_body[end:]

    # Rebuild References list: iterate new_num = 1..K, emit entry from the
    # canonical source's original text if available.
    lines = [heading_line.rstrip(), ""]
    for new_num, canonical in enumerate(canonical_order, start=1):
        text = ref_by_num.get(canonical)
        if text is None:
            text = "(source not listed)"
        lines.append(f"- [{new_num}] {text}")

    return new_body.rstrip() + "\n\n" + "\n".join(lines) + "\n"


class RAGService:
    def __init__(self, settings: Settings, embeddings: EmbeddingService, llm: LLMService):
        self._settings = settings
        self._embeddings = embeddings
        self._llm = llm

    async def _log_query(
        self, query_text: str, response_text: str | None, model_used: str | None,
        references: list | None, scope_declined: bool, cached: bool,
        start_time: float | None, ip_address: str | None,
        reviewer_id: int | None = None, mode: str = "rag",
        retrieval_candidates: list | None = None,
        retrieval_top_k: int | None = None,
    ) -> int | None:
        try:
            latency_ms = round((time.monotonic() - start_time) * 1000) if start_time else None
            return await db.insert_query_log(
                query_text=query_text, response_text=response_text,
                model_used=model_used, references=references,
                scope_declined=scope_declined, cached=cached,
                latency_ms=latency_ms, ip_address=ip_address,
                reviewer_id=reviewer_id, mode=mode,
                retrieval_candidates=retrieval_candidates,
                retrieval_top_k=retrieval_top_k,
            )
        except Exception:
            # Logged at ERROR (not WARNING): a persistent failure here means the
            # audit log is silently going blank, which is exactly how this broke
            # unnoticed before. Still swallowed so logging never breaks a query.
            logger.error("Failed to write query log", exc_info=True)
            return None

    async def log_failed_query(
        self, query_text: str, ip_address: str | None,
        start_time: float | None, mode: str = "error",
    ) -> None:
        """Record a query that errored out before it could be answered, so the
        audit log covers every query the API received — not only successful ones."""
        await self._log_query(
            query_text, None, None, [], False, False, start_time, ip_address,
            mode=mode,
        )

    # ── Retrieval (shared by JSON and streaming paths) ───────

    async def _retrieve(
        self, query: str, expansions: list[str], top_k: int, threshold: float,
    ) -> tuple[list[str], dict[str, float], list[dict]]:
        """Hybrid retrieval over the raw query plus its LLM expansions.

        Returns ``(prompt_ids, score_map, candidates_log)``:
        - prompt_ids: article ids to feed the LLM — the top_k *qualifying*
          candidates in fused (RRF) order. A candidate qualifies if its best
          chunk cosine score clears the threshold OR it was a lexical hit
          (lexical matches have no comparable cosine score, and an exact
          keyword hit like "ÁTVR" is precisely what the lexical arm is for).
        - score_map: article id → best vector score (for relevance display).
        - candidates_log: the full fused top-CANDIDATE_LOG_SIZE list for the
          query log, each entry marked with whether it reached the prompt.
        """
        candidates = await self._embeddings.hybrid_search(
            [query, *expansions], limit=CANDIDATE_LOG_SIZE,
        )
        qualified = [
            c for c in candidates
            if (c["vector_score"] is not None and c["vector_score"] >= threshold)
            or c["lexical_rank"] is not None
        ]
        prompt_ids = [c["id"] for c in qualified[:top_k]]
        in_prompt = set(prompt_ids)

        candidates_log = [
            {
                "rank": i + 1,
                "id": c["id"],
                "title": c["title"],
                "source_url": c["source_url"],
                "rrf_score": round(c["rrf_score"], 5),
                "vector_score": round(c["vector_score"], 4) if c["vector_score"] is not None else None,
                "vector_rank": c["vector_rank"],
                "lexical_rank": c["lexical_rank"],
                "in_prompt": c["id"] in in_prompt,
            }
            for i, c in enumerate(candidates)
        ]
        score_map = {c["id"]: (c["vector_score"] or 0.0) for c in candidates}
        return prompt_ids, score_map, candidates_log

    # ── JSON (non-streaming) mode ────────────────────────────

    async def process_query_json(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
        web_search: bool = False, reviewer_id: int | None = None,
        model_override: str | None = None, skip_cache: bool = False,
        output_format: str = "markdown",
    ) -> QueryResponse:
        """Answer a query as a single JSON response.

        ``output_format`` controls the rendering of the ``answer`` field:
        ``"markdown"`` (the internal default, used by the review playground and
        batch worker) returns the raw Markdown the model emits; ``"vv"`` (the
        public-API default, set by the ``/api/v1/query`` router) converts it to
        the Vísindavefur publish format so callers get the same output as the
        review/export mechanism. Markdown stays canonical everywhere else —
        cache, query_log, and review drafts are unaffected — so VV is purely a
        presentation layer applied on the way out.
        """
        response = await self._process_query_json(
            query, top_k, language,
            ip_address=ip_address, start_time=start_time,
            score_threshold=score_threshold, include_thinking=include_thinking,
            web_search=web_search, reviewer_id=reviewer_id,
            model_override=model_override, skip_cache=skip_cache,
        )
        # Web-search answers carry their sources inline with an empty structured
        # references list, so VV's reference-reconstruction would drop them —
        # leave those as Markdown.
        if output_format == "vv" and not web_search:
            refs = [r.model_dump() for r in response.references]
            response = response.model_copy(
                update={"answer": to_vv_html(response.answer, refs)}
            )
        return response

    async def _process_query_json(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
        web_search: bool = False, reviewer_id: int | None = None,
        model_override: str | None = None, skip_cache: bool = False,
    ) -> QueryResponse:
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        qhash = _query_hash(query, model_override or "auto")
        threshold = score_threshold if score_threshold is not None else self._settings.rag_score_threshold
        bypass_cache = include_thinking or skip_cache

        # Web search mode — skip RAG entirely
        if web_search:
            model_used, answer_text, thinking_text, _ = await self._llm.generate_web_search_non_streaming(
                query, language, include_thinking=include_thinking,
                model_override=model_override,
            )
            answer_text = _reconcile_web_search_citations(answer_text)
            log_id = await self._log_query(query, answer_text, model_used,
                                           [], False, False, start_time, ip_address,
                                           reviewer_id=reviewer_id, mode="websearch")
            return QueryResponse(
                query=query, answer=answer_text, references=[],
                model_used=model_used, cached=False, query_id=query_id,
                query_log_id=log_id,
            )

        # Cache check (skip when thinking or skip_cache)
        if not bypass_cache:
            cached = await db.cache_get(qhash)
            if cached:
                log_id = await self._log_query(query, cached.get("answer"), cached.get("model_used"),
                                               cached.get("references", []), False, True,
                                               start_time, ip_address, reviewer_id=reviewer_id)
                return QueryResponse(**cached, cached=True, query_id=query_id, query_log_id=log_id)

        # Scope guard + query expansion (one Flash call)
        scope, expansions = await self._llm.check_scope(query)
        if scope == "no":
            decline = (settings_service.get("prompt.decline_en") if language == "en"
                       else settings_service.get("prompt.decline_is"))
            flash_model = settings_service.get("model.flash_name")
            log_id = await self._log_query(query, decline, flash_model,
                                           [], True, False, start_time, ip_address,
                                           reviewer_id=reviewer_id)
            return QueryResponse(
                query=query, answer=decline, references=[],
                model_used=flash_model,
                cached=False, query_id=query_id, scope_declined=True,
                query_log_id=log_id,
            )

        # Hybrid retrieval (vector over chunks + lexical, RRF-fused)
        article_ids, score_map, candidates_log = await self._retrieve(
            query, expansions, top_k, threshold,
        )
        if not article_ids:
            no_result = (settings_service.get("prompt.no_results_en") if language == "en"
                         else settings_service.get("prompt.no_results_is"))
            flash_model = settings_service.get("model.flash_name")
            log_id = await self._log_query(query, no_result, flash_model,
                                           [], False, False, start_time, ip_address,
                                           reviewer_id=reviewer_id,
                                           retrieval_candidates=candidates_log,
                                           retrieval_top_k=top_k)
            return QueryResponse(
                query=query, answer=no_result,
                references=[], model_used=flash_model,
                cached=False, query_id=query_id,
                query_log_id=log_id,
            )

        # Fetch full articles, preserving fused-rank order (ANY() doesn't)
        articles = await db.get_articles_by_ids(article_ids)
        order = {aid: i for i, aid in enumerate(article_ids)}
        articles.sort(key=lambda a: order[a["id"]])

        # Generate answer (structured output returns references_used)
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

        # Store in cache (skip when thinking or skip_cache)
        if not bypass_cache:
            cache_data = {
                "query": query, "answer": answer_text,
                "references": [r.model_dump() for r in references],
                "model_used": model_used,
            }
            await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)

        log_id = await self._log_query(query, answer_text, model_used,
                                       [r.model_dump() for r in references],
                                       False, False, start_time, ip_address,
                                       reviewer_id=reviewer_id,
                                       retrieval_candidates=candidates_log,
                                       retrieval_top_k=top_k)
        return QueryResponse(
            query=query, answer=answer_text, references=references,
            model_used=model_used, cached=False, query_id=query_id,
            query_log_id=log_id,
        )

    # ── SSE (streaming) mode ─────────────────────────────────

    async def process_query_stream(
        self, query: str, top_k: int, language: str,
        ip_address: str | None = None, start_time: float | None = None,
        score_threshold: float | None = None, include_thinking: bool = False,
        web_search: bool = False, reviewer_id: int | None = None,
        model_override: str | None = None, skip_cache: bool = False,
        output_format: str = "markdown",
    ):
        """Yields dicts with 'event' and 'data' keys for sse-starlette.

        ``output_format="vv"`` (the public-API default) renders the final answer
        in the Vísindavefur publish format. VV is a whole-document transform —
        it reconstructs footnotes and a Heimildir block from the structured
        references — so it cannot be applied to a half-streamed answer. The
        per-``token`` events therefore always carry Markdown; the VV result is
        delivered in the terminal ``answer_final`` event, which clients already
        treat as the authoritative text to replace their accumulated tokens
        with. ``"markdown"`` (the internal default) leaves the answer untouched.
        """
        query_id = f"q_{uuid.uuid4().hex[:12]}"
        bypass_cache = include_thinking or skip_cache

        def _answer_final_event(text: str, refs: list[dict]) -> dict:
            """Build the terminal `answer_final` event, VV-rendered when the
            caller asked for the Vísindavefur publish format."""
            rendered = to_vv_html(text, refs) if output_format == "vv" else text
            return {"event": "answer_final", "data": json.dumps({"text": rendered})}

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
                # Reconcile inline [N] and the Heimildir/References list
                answer_text = _reconcile_web_search_citations("".join(full_answer))
                yield {"event": "answer_final", "data": json.dumps({"text": answer_text})}
                yield {"event": "references", "data": json.dumps({"references": []})}
                yield {"event": "done", "data": json.dumps({"model_used": model_used, "cached": False, "query_id": query_id})}
                await self._log_query(query, answer_text, model_used,
                                      [], False, False, start_time, ip_address,
                                      reviewer_id=reviewer_id, mode="websearch")
                return

            qhash = _query_hash(query, model_override or "auto")
            threshold = score_threshold if score_threshold is not None else self._settings.rag_score_threshold

            # Cache check (skip when thinking or skip_cache)
            if not bypass_cache:
                cached = await db.cache_get(qhash)
                if cached:
                    yield {"event": "status", "data": json.dumps({"stage": "complete", "message": "Cached response"})}
                    for word in cached.get("answer", "").split():
                        yield {"event": "token", "data": json.dumps({"text": word + " "})}
                    if output_format == "vv":
                        yield _answer_final_event(
                            cached.get("answer", ""), cached.get("references", [])
                        )
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

            # Scope guard + query expansion (one Flash call)
            scope, expansions = await self._llm.check_scope(query)
            if scope == "no":
                decline = (settings_service.get("prompt.decline_en") if language == "en"
                           else settings_service.get("prompt.decline_is"))
                flash_model = settings_service.get("model.flash_name")
                for word in decline.split():
                    yield {"event": "token", "data": json.dumps({"text": word + " "})}
                if output_format == "vv":
                    yield _answer_final_event(decline, [])
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

            # Hybrid retrieval (vector over chunks + lexical, RRF-fused)
            article_ids, score_map, candidates_log = await self._retrieve(
                query, expansions, top_k, threshold,
            )

            top_score = max((score_map.get(aid, 0.0) for aid in article_ids), default=0.0)
            yield {
                "event": "context",
                "data": json.dumps({"articles_found": len(article_ids), "top_score": round(top_score, 4)}),
            }

            if not article_ids:
                no_result = (settings_service.get("prompt.no_results_en") if language == "en"
                             else settings_service.get("prompt.no_results_is"))
                yield {"event": "token", "data": json.dumps({"text": no_result})}
                if output_format == "vv":
                    yield _answer_final_event(no_result, [])
                yield {"event": "references", "data": json.dumps({"references": []})}
                yield {
                    "event": "done",
                    "data": json.dumps({"model_used": "none", "cached": False, "query_id": query_id}),
                }
                await self._log_query(query, no_result, "none",
                                      [], False, False, start_time, ip_address,
                                      retrieval_candidates=candidates_log,
                                      retrieval_top_k=top_k)
                return

            # Fetch full articles, preserving fused-rank order (ANY() doesn't)
            articles = await db.get_articles_by_ids(article_ids)
            order = {aid: i for i, aid in enumerate(article_ids)}
            articles.sort(key=lambda a: order[a["id"]])

            # Status: generating
            yield {"event": "status", "data": json.dumps({"stage": "generating", "message": "Bý til svar..."})}

            # Stream LLM response
            model_used, token_stream = await self._llm.generate_stream(
                query, articles, language, include_thinking=include_thinking,
                model_override=model_override,
            )
            full_answer = []
            final_answer: str | None = None
            used_ids: set[str] = set()
            async for chunk_type, chunk_text in token_stream:
                if chunk_type == "thinking":
                    yield {"event": "thinking", "data": json.dumps({"text": chunk_text})}
                elif chunk_type == "references":
                    used_ids = set(chunk_text)  # chunk_text is a list of IDs
                elif chunk_type == "answer_final":
                    # Authoritative, robustly-extracted answer (see llm_service);
                    # overrides the streamed preview for the final/cached text.
                    final_answer = chunk_text
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

            # Prefer the clean extracted answer; if extraction came back empty
            # (e.g. a truncated/unrecoverable envelope), keep the streamed
            # tokens so a partial answer still reaches the user.
            raw_answer = final_answer or "".join(full_answer)
            answer_text = _renumber_citations(raw_answer, number_map)
            yield _answer_final_event(answer_text, references)
            yield {"event": "references", "data": json.dumps({"references": references})}
            if used_ids and not re.search(r"\[\d+\]", answer_text):
                logger.warning("Answer has references but no [N] citations (query_id=%s)", query_id)

            # Done
            yield {
                "event": "done",
                "data": json.dumps({"model_used": model_used, "cached": False, "query_id": query_id}),
            }

            # Store in cache (skip when thinking or skip_cache)
            if not bypass_cache:
                cache_data = {
                    "query": query, "answer": answer_text, "references": references,
                    "model_used": model_used,
                }
                await db.cache_store(qhash, query, cache_data, article_ids, self._settings.query_cache_ttl_hours)
            await self._log_query(query, answer_text, model_used,
                                  references, False, False, start_time, ip_address,
                                  reviewer_id=reviewer_id,
                                  retrieval_candidates=candidates_log,
                                  retrieval_top_k=top_k)

        except Exception:
            logger.error("Stream failed for query_id=%s", query_id, exc_info=True)
            # Log the failed query too, so the audit log covers every API query.
            await self._log_query(
                query, None, None, [], False, False, start_time, ip_address,
                reviewer_id=reviewer_id, mode="error",
            )
            yield {
                "event": "error",
                "data": json.dumps({
                    "message": "Villa kom upp við úrvinnslu fyrirspurnar. Reyndu aftur.",
                    "query_id": query_id,
                }),
            }
