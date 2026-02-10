import asyncio
import logging

from pinecone import Pinecone

from app.config import Settings

logger = logging.getLogger(__name__)


class PineconeService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pc: Pinecone | None = None
        self._index = None

    async def initialize(self) -> None:
        self._pc = Pinecone(api_key=self._settings.pinecone_api_key)
        self._index = self._pc.Index(self._settings.pinecone_index_name)
        logger.info("PineconeService initialized (index=%s)", self._settings.pinecone_index_name)

    async def close(self) -> None:
        self._index = None
        self._pc = None
        logger.info("PineconeService closed")

    # ── Embedding helpers ────────────────────────────────────

    @staticmethod
    def _build_embed_text(title: str, question: str, answer: str) -> str:
        words = answer.split()
        truncated = " ".join(words[:1000])
        return f"{title}\n{question}\n{truncated}"

    @staticmethod
    def _build_metadata(article: dict) -> dict:
        words = article["answer"].split()
        preview = " ".join(words[:1000])
        return {
            "article_id": article["id"],
            "title": article["title"],
            "question": article["question"],
            "source_url": article["source_url"],
            "date": article["date"],
            "author": article["author"],
            "categories": article["categories"],
            "content_preview": preview,
        }

    async def embed_text(self, text: str, input_type: str = "passage") -> list[float]:
        """Embed text via Pinecone Inference API. input_type: 'passage' or 'query'."""
        result = await asyncio.to_thread(
            self._pc.inference.embed,
            model="multilingual-e5-large",
            inputs=[text],
            parameters={"input_type": input_type},
        )
        return result.data[0].values

    # ── Vector operations ────────────────────────────────────

    async def upsert_article(self, article: dict) -> None:
        text = self._build_embed_text(article["title"], article["question"], article["answer"])
        embedding = await self.embed_text(text, input_type="passage")
        metadata = self._build_metadata(article)
        await asyncio.to_thread(
            self._index.upsert,
            vectors=[{"id": article["id"], "values": embedding, "metadata": metadata}],
        )

    async def upsert_articles_batch(self, articles: list[dict]) -> None:
        vectors = []
        for article in articles:
            text = self._build_embed_text(article["title"], article["question"], article["answer"])
            embedding = await self.embed_text(text, input_type="passage")
            metadata = self._build_metadata(article)
            vectors.append({"id": article["id"], "values": embedding, "metadata": metadata})
        if vectors:
            await asyncio.to_thread(self._index.upsert, vectors=vectors)

    async def delete_vector(self, article_id: str) -> None:
        await asyncio.to_thread(self._index.delete, ids=[article_id])

    async def query(self, text: str, top_k: int = 5) -> list[dict]:
        embedding = await self.embed_text(text, input_type="query")
        result = await asyncio.to_thread(
            self._index.query,
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
        )
        return [
            {
                "id": match.id,
                "score": match.score,
                "metadata": match.metadata,
            }
            for match in result.matches
        ]

    async def get_index_stats(self) -> dict:
        stats = await asyncio.to_thread(self._index.describe_index_stats)
        return {
            "vector_count": stats.total_vector_count,
            "index_fullness": stats.index_fullness,
        }

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(self._index.describe_index_stats)
            return True
        except Exception:
            return False
