import json
import logging
from pathlib import Path

import yaml
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import Settings
from app.db import queries as db

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class GeminiResponse(BaseModel):
    answer: str  # Markdown-formatted answer
    references_used: list[str]  # Article IDs the model actually cited


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
                f"[Grein {i} | ID: {a['id']}]\n"
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

    async def generate_stream(
        self, query: str, articles: list[dict], language: str = "auto",
        include_thinking: bool = False,
    ):
        """Returns (model_used, async_iterator) where iterator yields
        ("thinking", text), ("answer", text), or ("references", list[str])."""
        model = await self.select_model()
        context = self._build_context(articles, language)

        # Track quota
        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        user_content = f"{context}\n\n## Spurning notanda\n{query}"

        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            temperature=0.3,
            response_mime_type="application/json",
            response_schema=GeminiResponse,
        )
        if include_thinking:
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=4096,
                include_thoughts=True,
            )

        stream = await self._client.aio.models.generate_content_stream(
            model=model,
            contents=user_content,
            config=config,
        )

        async def text_iterator():
            json_buffer = []  # accumulate full JSON for final parse
            # State machine for incremental answer extraction
            # States: "before" → waiting for "answer":" prefix
            #         "in_answer" → inside the answer string value
            #         "after" → past the answer string, accumulating rest
            state = "before"
            escape = False  # next char is escaped

            async for chunk in stream:
                if not chunk.candidates:
                    continue
                for part in chunk.candidates[0].content.parts:
                    if part.thought:
                        yield ("thinking", part.text)
                        continue
                    if not part.text:
                        continue

                    json_buffer.append(part.text)

                    if state == "before":
                        # Check if we've accumulated enough to find "answer":"
                        joined = "".join(json_buffer)
                        marker = '"answer":"'
                        idx = joined.find(marker)
                        if idx != -1:
                            state = "in_answer"
                            # Anything after the marker is answer content
                            after_marker = joined[idx + len(marker):]
                            # Process this initial chunk through the answer parser
                            decoded = []
                            for ch in after_marker:
                                if escape:
                                    if ch == "n":
                                        decoded.append("\n")
                                    elif ch == "t":
                                        decoded.append("\t")
                                    else:
                                        decoded.append(ch)  # \", \\, etc.
                                    escape = False
                                elif ch == "\\":
                                    escape = True
                                elif ch == '"':
                                    state = "after"
                                    break
                                else:
                                    decoded.append(ch)
                            if decoded:
                                yield ("answer", "".join(decoded))
                    elif state == "in_answer":
                        decoded = []
                        for ch in part.text:
                            if escape:
                                if ch == "n":
                                    decoded.append("\n")
                                elif ch == "t":
                                    decoded.append("\t")
                                else:
                                    decoded.append(ch)
                                escape = False
                            elif ch == "\\":
                                escape = True
                            elif ch == '"':
                                state = "after"
                                break
                            else:
                                decoded.append(ch)
                        if decoded:
                            yield ("answer", "".join(decoded))
                    # state == "after": just accumulate, parsed at end

            # Parse complete JSON to extract references_used
            full_json = "".join(json_buffer)
            try:
                parsed = json.loads(full_json)
                refs = parsed.get("references_used", [])
            except (json.JSONDecodeError, KeyError):
                logger.warning("Failed to parse structured response JSON")
                refs = []
            yield ("references", refs)

        return model, text_iterator()

    async def generate_non_streaming(
        self, query: str, articles: list[dict], language: str = "auto",
        include_thinking: bool = False,
    ) -> tuple[str, str, str | None, list[str]]:
        """Returns (model_used, answer_text, thinking_text_or_None, references_used)."""
        model = await self.select_model()
        context = self._build_context(articles, language)

        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        user_content = f"{context}\n\n## Spurning notanda\n{query}"

        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            temperature=0.3,
            response_mime_type="application/json",
            response_schema=GeminiResponse,
        )
        if include_thinking:
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=4096,
                include_thoughts=True,
            )

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_content,
            config=config,
        )

        # Extract thinking parts if requested
        thinking_text: str | None = None
        raw_json = ""
        if include_thinking:
            thinking_parts: list[str] = []
            json_parts: list[str] = []
            for part in response.candidates[0].content.parts:
                if part.thought:
                    thinking_parts.append(part.text)
                elif part.text:
                    json_parts.append(part.text)
            thinking_text = "".join(thinking_parts) or None
            raw_json = "".join(json_parts)
        else:
            raw_json = response.text

        # Parse structured JSON response
        try:
            parsed = json.loads(raw_json)
            answer_text = parsed.get("answer", raw_json)
            refs = parsed.get("references_used", [])
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to parse structured response JSON, using raw text")
            answer_text = raw_json
            refs = []

        return model, answer_text, thinking_text, refs
