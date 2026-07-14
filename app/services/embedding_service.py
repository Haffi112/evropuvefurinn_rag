import logging
import re

import httpx
import numpy as np

from app.config import Settings
from app.db.database import get_pool

logger = logging.getLogger(__name__)

DEEPINFRA_EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings"

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

    async def embed_text(self, text: str, input_type: str = "passage") -> list[float]:
        """Embed text via DeepInfra API. input_type: 'passage' or 'query'."""
        prefix = "query: " if input_type == "query" else "passage: "
        prefixed = prefix + text

        resp = await self._client.post(
            DEEPINFRA_EMBED_URL,
            json={
                "model": self._settings.deepinfra_model,
                "input": [prefixed],
                "encoding_format": "float",
            },
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    async def embed_texts_batch(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        """Embed multiple texts in a single API call."""
        prefix = "query: " if input_type == "query" else "passage: "
        prefixed = [prefix + t for t in texts]

        resp = await self._client.post(
            DEEPINFRA_EMBED_URL,
            json={
                "model": self._settings.deepinfra_model,
                "input": prefixed,
                "encoding_format": "float",
            },
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Sort by index to guarantee order
        data.sort(key=lambda x: x["index"])
        return [d["embedding"] for d in data]

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
