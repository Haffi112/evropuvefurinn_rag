import logging
from pathlib import Path

import yaml
from google import genai
from google.genai import types

from app.config import Settings
from app.db import queries as db

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class GeminiService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: genai.Client | None = None
        self._system_prompt: str | None = None
        self._scope_guard_prompt: str | None = None

    async def initialize(self) -> None:
        self._client = genai.Client(api_key=self._settings.gemini_api_key)
        self._load_prompts()
        logger.info("GeminiService initialized")

    def _load_prompts(self) -> None:
        with open(PROMPTS_DIR / "system_prompt.yaml") as f:
            data = yaml.safe_load(f)
            self._system_prompt = data["system"]
        with open(PROMPTS_DIR / "scope_guard.yaml") as f:
            data = yaml.safe_load(f)
            self._scope_guard_prompt = data["task"]

    async def close(self) -> None:
        self._client = None
        logger.info("GeminiService closed")

    # ── Scope guard ──────────────────────────────────────────

    async def check_scope(self, query: str) -> str:
        """Returns 'yes', 'adjacent', or 'no'. Always uses Flash (no Pro quota)."""
        response = await self._client.aio.models.generate_content(
            model=self._settings.gemini_flash_model,
            contents=f"{self._scope_guard_prompt}\n\nQuestion: {query}",
            config=types.GenerateContentConfig(temperature=0),
        )
        result = response.text.strip().lower()
        if result not in ("yes", "adjacent", "no"):
            logger.warning("Scope guard returned unexpected value: %s, defaulting to 'adjacent'", result)
            return "adjacent"
        return result

    # ── Model selection ──────────────────────────────────────

    async def select_model(self) -> str:
        """Check Pro quota; return Pro model name if under limit, else Flash."""
        pro_count = await db.quota_get("pro")
        if pro_count < self._settings.gemini_pro_daily_limit:
            return self._settings.gemini_pro_model
        logger.info("Pro quota exhausted (%d/%d), falling back to Flash",
                     pro_count, self._settings.gemini_pro_daily_limit)
        return self._settings.gemini_flash_model

    # ── Generation ───────────────────────────────────────────

    def _build_context(self, articles: list[dict], language: str) -> str:
        parts = []
        for i, a in enumerate(articles, 1):
            parts.append(
                f"[Grein {i}]\n"
                f"Titill: {a['title']}\n"
                f"Spurning: {a['question']}\n"
                f"Heimild: {a['source_url']}\n"
                f"Svar:\n{a['answer']}\n"
            )
        context = "\n---\n".join(parts)
        lang_instruction = ""
        if language == "en":
            lang_instruction = "\n\nIMPORTANT: The user's question is in English. Respond in English."
        elif language == "is":
            lang_instruction = "\n\nMIKILVÆGT: Spurning notandans er á íslensku. Svaraðu á íslensku."
        return f"## Greinar úr þekkingargrunni\n\n{context}{lang_instruction}"

    async def generate_stream(self, query: str, articles: list[dict], language: str = "auto"):
        """Returns (model_used, async_iterator_of_text_chunks)."""
        model = await self.select_model()
        context = self._build_context(articles, language)

        # Track quota
        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        user_content = f"{context}\n\n## Spurning notanda\n{query}"

        stream = await self._client.aio.models.generate_content_stream(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                temperature=0.3,
            ),
        )

        async def text_iterator():
            async for chunk in stream:
                if chunk.text:
                    yield chunk.text

        return model, text_iterator()

    async def generate_non_streaming(self, query: str, articles: list[dict], language: str = "auto") -> tuple[str, str]:
        """Returns (model_used, full_text)."""
        model = await self.select_model()
        context = self._build_context(articles, language)

        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        user_content = f"{context}\n\n## Spurning notanda\n{query}"

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                temperature=0.3,
            ),
        )
        return model, response.text
