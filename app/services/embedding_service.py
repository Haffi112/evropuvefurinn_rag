import logging

import httpx
import numpy as np

from app.config import Settings
from app.db.database import get_pool

logger = logging.getLogger(__name__)

DEEPINFRA_EMBED_URL = "https://api.deepinfra.com/v1/openai/embeddings"


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
        text = self._build_embed_text(article["title"], article["question"], article["answer"])
        embedding = await self.embed_text(text, input_type="passage")
        vec = np.array(embedding, dtype=np.float32)

        pool = get_pool()
        await pool.execute(
            "UPDATE articles SET embedding = $1 WHERE id = $2",
            vec, article["id"],
        )

    async def upsert_articles_batch(self, articles: list[dict]) -> None:
        if not articles:
            return
        texts = [
            self._build_embed_text(a["title"], a["question"], a["answer"])
            for a in articles
        ]
        embeddings = await self.embed_texts_batch(texts, input_type="passage")

        pool = get_pool()
        async with pool.acquire() as conn:
            for article, emb in zip(articles, embeddings):
                vec = np.array(emb, dtype=np.float32)
                await conn.execute(
                    "UPDATE articles SET embedding = $1 WHERE id = $2",
                    vec, article["id"],
                )

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
