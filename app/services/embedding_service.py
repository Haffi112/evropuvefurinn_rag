import asyncio
import logging
import re
from collections import OrderedDict

import httpx
import numpy as np

from app.config import Settings
from app.db.database import get_pool

logger = logging.getLogger(__name__)

DEEPINFRA_EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings"

# Retry policy for the DeepInfra embeddings call. evropuvefur.is funnels every
# site visitor through a single server-side backend, so identical popular
# questions arrive as bursts from one source and briefly outrun DeepInfra's
# rate limit — a 429 that used to surface to the end user as a 500 (this is
# what produced the query-log error spike). Retry transient failures (429 and
# 5xx) with exponential backoff; permanent 4xx errors are not retried.
EMBED_MAX_RETRIES = 3          # retries *after* the initial attempt
EMBED_BACKOFF_BASE = 0.5       # seconds; doubled each successive retry
EMBED_RETRY_AFTER_CAP = 30.0   # never honour a Retry-After longer than this
EMBED_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Per-process LRU cache of query embeddings, keyed on the raw query text. A hot
# preset question (e.g. "Chat control" was asked 132× in a single day) would
# otherwise re-embed the identical string on every retrieval. multilingual-e5
# vectors are 1024 floats (~8 KB), so this caps the cache near ~16 MB.
QUERY_EMBED_CACHE_SIZE = 2048

# Chunking geometry. multilingual-e5-large truncates input at 512 tokens and
# Icelandic costs roughly 2-2.5 tokens per word on its tokenizer, so a chunk
# must stay well under ~200 words once the title+question prefix is added —
# otherwise the tail of the chunk silently never reaches the embedding.
CHUNK_WORDS = 160
CHUNK_OVERLAP_WORDS = 40

# How deep each individual ranked list goes before fusion.
HYBRID_LIST_SIZE = 20
# Chunk rows fetched per vector search. Must be generous: several chunks share
# a parent article, and a handful of on-topic articles can occupy dozens of
# the nearest chunk slots, crowding everything else out of the article list.
# (Observed in production: an article ranked #9 at article level missed the
# candidates entirely at LIMIT 60.) Requires hnsw.ef_search >= this value —
# pgvector's default is 40, set per-transaction in _vector_search_articles.
CHUNK_FETCH_LIMIT = 200
# Standard reciprocal-rank-fusion dampening constant.
RRF_K = 60

# Minimal Icelandic/English stopword list for the lexical arm. The lexical
# tsquery ORs terms together, so high-frequency function words would otherwise
# drown out the rare tokens (ÁTVR, Schengen, ...) it exists to catch.
_STOPWORDS = {
    "og", "að", "af", "á", "í", "er", "eru", "var", "voru", "vera", "um",
    "en", "ef", "sem", "til", "það", "þessi", "þetta", "hann", "hún", "ég",
    "þú", "við", "þið", "þeir", "þær", "þau", "svo", "eða", "ekki", "sé",
    "the", "a", "an", "is", "are", "was", "were", "of", "in", "on", "to",
    "and", "or", "for", "does", "do", "did", "what", "how", "why",
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class EmbeddingService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        # LRU (insertion-ordered) cache of query embeddings; see
        # QUERY_EMBED_CACHE_SIZE. Query-only: passage embeddings are unique and
        # computed once at index time, so caching them would only waste memory.
        self._query_cache: "OrderedDict[str, list[float]]" = OrderedDict()

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self._settings.deepinfra_api_key}",
                "Content-Type": "application/json",
            },
        )
        logger.info("EmbeddingService initialized (model=%s)", self._settings.deepinfra_model)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("EmbeddingService closed")

    # ── Embedding helpers ────────────────────────────────────

    @staticmethod
    def _build_embed_text(title: str, question: str, answer: str) -> str:
        words = answer.split()
        truncated = " ".join(words[:1000])
        return f"{title}\n{question}\n{truncated}"

    @staticmethod
    def _chunk_answer(
        answer: str,
        max_words: int = CHUNK_WORDS,
        overlap: int = CHUNK_OVERLAP_WORDS,
    ) -> list[str]:
        """Split an answer into overlapping word-window chunks."""
        words = answer.split()
        if not words:
            return []
        if len(words) <= max_words:
            return [" ".join(words)]
        step = max_words - overlap
        chunks = []
        for start in range(0, len(words), step):
            window = words[start : start + max_words]
            chunks.append(" ".join(window))
            if start + max_words >= len(words):
                break
        return chunks

    @staticmethod
    def _build_chunk_texts(title: str, question: str, answer: str) -> list[str]:
        """Chunk texts for embedding: each carries the title+question prefix so
        a mid-article passage still embeds with its parent topic attached."""
        return [
            f"{title}\n{question}\n{chunk}"
            for chunk in EmbeddingService._chunk_answer(answer)
        ]

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Backoff before the next retry. Honour a numeric Retry-After header
        when present (capped), otherwise fall back to exponential backoff."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), EMBED_RETRY_AFTER_CAP)
            except ValueError:
                pass  # HTTP-date form — ignore and use exponential backoff
        return EMBED_BACKOFF_BASE * (2 ** attempt)

    async def _post_embeddings(self, prefixed: list[str]) -> list[list[float]]:
        """POST prefixed inputs to DeepInfra, returning embeddings in input
        order. Retries transient failures (429/5xx) with exponential backoff;
        any other error (or exhausting the retries) raises via raise_for_status.
        """
        payload = {
            "model": self._settings.deepinfra_model,
            "input": prefixed,
            "encoding_format": "float",
        }
        resp: httpx.Response | None = None
        for attempt in range(EMBED_MAX_RETRIES + 1):
            resp = await self._client.post(DEEPINFRA_EMBED_URL, json=payload)
            retryable = resp.status_code in EMBED_RETRYABLE_STATUS
            if retryable and attempt < EMBED_MAX_RETRIES:
                delay = self._retry_delay(resp, attempt)
                logger.warning(
                    "DeepInfra embeddings HTTP %d (attempt %d/%d), retrying in %.2fs",
                    resp.status_code, attempt + 1, EMBED_MAX_RETRIES + 1, delay,
                )
                await asyncio.sleep(delay)
                continue
            # Success, permanent error, or last attempt: let raise_for_status
            # turn any non-2xx into an exception, otherwise parse the result.
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda x: x["index"])  # index order == input order
            return [d["embedding"] for d in data]
        # Unreachable: the final iteration always returns or raises above.
        assert resp is not None
        resp.raise_for_status()
        return []

    def _query_cache_get(self, text: str) -> list[float] | None:
        emb = self._query_cache.get(text)
        if emb is not None:
            self._query_cache.move_to_end(text)  # mark most-recently-used
        return emb

    def _query_cache_put(self, text: str, embedding: list[float]) -> None:
        self._query_cache[text] = embedding
        self._query_cache.move_to_end(text)
        while len(self._query_cache) > QUERY_EMBED_CACHE_SIZE:
            self._query_cache.popitem(last=False)  # evict least-recently-used

    async def embed_text(self, text: str, input_type: str = "passage") -> list[float]:
        """Embed text via DeepInfra API. input_type: 'passage' or 'query'.
        Query embeddings are served from (and stored in) the per-process LRU."""
        if input_type == "query":
            cached = self._query_cache_get(text)
            if cached is not None:
                return cached
            embedding = (await self._post_embeddings(["query: " + text]))[0]
            self._query_cache_put(text, embedding)
            return embedding
        return (await self._post_embeddings(["passage: " + text]))[0]

    async def embed_texts_batch(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        """Embed multiple texts in a single API call.

        For query embeddings the per-process cache is consulted first and only
        the cache misses are sent to DeepInfra — so a burst of the same popular
        question embeds once, not once per request. Passage embeddings bypass
        the cache (each is unique and computed a single time at index time).
        """
        if input_type != "query":
            return await self._post_embeddings(["passage: " + t for t in texts])

        cache_hits: dict[int, list[float]] = {}
        missing_idx: list[int] = []
        for i, t in enumerate(texts):
            hit = self._query_cache_get(t)
            if hit is None:
                missing_idx.append(i)
            else:
                cache_hits[i] = hit

        fetched: dict[int, list[float]] = {}
        if missing_idx:
            embeddings = await self._post_embeddings(
                ["query: " + texts[i] for i in missing_idx]
            )
            for i, emb in zip(missing_idx, embeddings):
                fetched[i] = emb
                self._query_cache_put(texts[i], emb)

        return [
            cache_hits[i] if i in cache_hits else fetched[i]
            for i in range(len(texts))
        ]

    # ── Vector operations ────────────────────────────────────

    async def upsert_article(self, article: dict) -> None:
        """Embed the whole-article vector (kept as a search fallback) plus one
        vector per answer chunk, and replace the article's chunk rows."""
        whole_text = self._build_embed_text(article["title"], article["question"], article["answer"])
        chunk_texts = self._build_chunk_texts(article["title"], article["question"], article["answer"])

        embeddings = await self.embed_texts_batch([whole_text, *chunk_texts], input_type="passage")
        whole_vec = np.array(embeddings[0], dtype=np.float32)
        chunk_vecs = [np.array(e, dtype=np.float32) for e in embeddings[1:]]

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE articles SET embedding = $1 WHERE id = $2",
                    whole_vec, article["id"],
                )
                await conn.execute(
                    "DELETE FROM article_chunks WHERE article_id = $1", article["id"],
                )
                for i, (chunk, vec) in enumerate(zip(chunk_texts, chunk_vecs)):
                    await conn.execute(
                        """
                        INSERT INTO article_chunks (article_id, chunk_index, content, embedding)
                        VALUES ($1, $2, $3, $4)
                        """,
                        article["id"], i, chunk, vec,
                    )

    async def upsert_articles_batch(self, articles: list[dict]) -> None:
        for article in articles:
            await self.upsert_article(article)

    # ── Hybrid search (vector + lexical, RRF fusion) ─────────

    @staticmethod
    def _lexical_tsquery(text: str) -> str | None:
        """Build a sanitised OR tsquery string from the meaningful query words.
        Returns None when nothing usable remains (e.g. an all-stopword query)."""
        terms = []
        seen = set()
        for word in _WORD_RE.findall(text.lower()):
            if len(word) < 2 or word in _STOPWORDS or word in seen:
                continue
            seen.add(word)
            terms.append(word)
            if len(terms) >= 12:
                break
        return " | ".join(terms) if terms else None

    @staticmethod
    def _rrf_fuse(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
        """Reciprocal-rank fusion over article-level ranked lists.

        Each input row needs an ``id``; rows may carry ``vector_score`` or
        ``lexical_score`` depending on which arm produced them. Returns one
        merged row per article, sorted by descending RRF score, annotated with
        the best vector score / vector rank / lexical rank seen in any list.
        """
        fused: dict[str, dict] = {}
        for ranked in ranked_lists:
            for pos, row in enumerate(ranked):
                entry = fused.setdefault(row["id"], {
                    **{k_: v for k_, v in row.items()
                       if k_ not in ("vector_score", "lexical_score")},
                    "rrf_score": 0.0,
                    "vector_score": None,
                    "vector_rank": None,
                    "lexical_rank": None,
                })
                entry["rrf_score"] += 1.0 / (k + pos + 1)
                if "vector_score" in row:
                    if entry["vector_score"] is None or row["vector_score"] > entry["vector_score"]:
                        entry["vector_score"] = row["vector_score"]
                    if entry["vector_rank"] is None or pos + 1 < entry["vector_rank"]:
                        entry["vector_rank"] = pos + 1
                if "lexical_score" in row:
                    if entry["lexical_rank"] is None or pos + 1 < entry["lexical_rank"]:
                        entry["lexical_rank"] = pos + 1
        return sorted(fused.values(), key=lambda e: e["rrf_score"], reverse=True)

    async def _vector_search_articles(self, vec: np.ndarray, limit: int) -> list[dict]:
        """Chunk-level vector search collapsed to parent articles (best chunk
        wins). Falls back to the legacy whole-article embedding when the chunk
        table hasn't been backfilled yet, so a deploy without the backfill
        degrades to the old behaviour instead of returning nothing."""
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # pgvector's HNSW scan only surfaces ef_search candidates
                # (default 40); anything past that in LIMIT is garbage. Scope
                # the raise to this transaction so other queries keep the
                # cheap default.
                await conn.execute(f"SET LOCAL hnsw.ef_search = {CHUNK_FETCH_LIMIT + 40}")
                rows = await conn.fetch(
                    """
                    WITH nearest AS (
                        SELECT article_id, 1 - (embedding <=> $1::vector) AS score
                        FROM article_chunks
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                    ),
                    best AS (
                        SELECT article_id, max(score) AS score
                        FROM nearest GROUP BY article_id
                    )
                    SELECT a.id, a.title, a.question, a.source_url, a.date, b.score
                    FROM best b JOIN articles a ON a.id = b.article_id
                    ORDER BY b.score DESC
                    LIMIT $3
                    """,
                    vec, CHUNK_FETCH_LIMIT, limit,
                )
                if not rows:
                    rows = await conn.fetch(
                        """
                        SELECT id, title, question, source_url, date,
                               1 - (embedding <=> $1::vector) AS score
                        FROM articles
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> $1::vector
                        LIMIT $2
                        """,
                        vec, limit,
                    )
        return [
            {
                "id": r["id"], "title": r["title"], "question": r["question"],
                "source_url": r["source_url"], "date": r["date"],
                "vector_score": float(r["score"]),
            }
            for r in rows
        ]

    async def _lexical_search_articles(self, text: str, limit: int) -> list[dict]:
        tsquery = self._lexical_tsquery(text)
        if tsquery is None:
            return []
        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, title, question, source_url, date,
                   ts_rank_cd(search_tsv, query, 32) AS score
            FROM articles, to_tsquery('simple', $1) query
            WHERE search_tsv @@ query
            ORDER BY score DESC
            LIMIT $2
            """,
            tsquery, limit,
        )
        return [
            {
                "id": r["id"], "title": r["title"], "question": r["question"],
                "source_url": r["source_url"], "date": r["date"],
                "lexical_score": float(r["score"]),
            }
            for r in rows
        ]

    async def hybrid_search(self, queries: list[str], limit: int = 30) -> list[dict]:
        """Hybrid retrieval over one or more query variants.

        For every query string this runs a chunk-level vector search and an
        article-level lexical search, then fuses all ranked lists with
        reciprocal-rank fusion. Returns up to ``limit`` article candidates:
        ``{id, title, question, source_url, date, rrf_score, vector_score,
        vector_rank, lexical_rank}`` sorted by fused relevance.
        """
        queries = [q for q in (q.strip() for q in queries) if q]
        if not queries:
            return []

        embeddings = await self.embed_texts_batch(queries, input_type="query")

        ranked_lists: list[list[dict]] = []
        for emb in embeddings:
            vec = np.array(emb, dtype=np.float32)
            ranked_lists.append(await self._vector_search_articles(vec, HYBRID_LIST_SIZE))
        for q in queries:
            lexical = await self._lexical_search_articles(q, HYBRID_LIST_SIZE)
            if lexical:
                ranked_lists.append(lexical)

        return self._rrf_fuse(ranked_lists)[:limit]

    async def query(self, text: str, top_k: int = 5) -> list[dict]:
        embedding = await self.embed_text(text, input_type="query")
        vec = np.array(embedding, dtype=np.float32)

        pool = get_pool()
        rows = await pool.fetch(
            """
            SELECT id, title, question, source_url, date, author, categories,
                   1 - (embedding <=> $1::vector) AS score
            FROM articles
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec, top_k,
        )

        return [
            {
                "id": row["id"],
                "score": float(row["score"]),
                "metadata": {
                    "article_id": row["id"],
                    "title": row["title"],
                    "question": row["question"],
                    "source_url": row["source_url"],
                    "date": row["date"],
                    "author": row["author"],
                    "categories": list(row["categories"]),
                },
            }
            for row in rows
        ]

    async def get_index_stats(self) -> dict:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT count(*) AS total, count(embedding) AS embedded FROM articles"
        )
        return {
            "total_articles": row["total"],
            "embedded_articles": row["embedded"],
        }

    async def health_check(self) -> bool:
        try:
            resp = await self._client.post(
                DEEPINFRA_EMBED_URL,
                json={
                    "model": self._settings.deepinfra_model,
                    "input": ["health check"],
                    "encoding_format": "float",
                },
            )
            return resp.status_code == 200
        except Exception:
            return False
