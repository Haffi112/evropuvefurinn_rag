import json
import logging
import re

from openai import AsyncOpenAI, APIError
from pydantic import BaseModel

from app.config import Settings
from app.db import queries as db
from app.services import settings_service

logger = logging.getLogger(__name__)

JSON_FORMAT_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON matching this exact schema and nothing else:\n"
    '{"answer": "<markdown string>", "references_used": ["<article_id>", ...]}'
)


class LLMResponse(BaseModel):
    answer: str  # Markdown-formatted answer
    references_used: list[str]  # Article IDs the model actually cited


class LLMService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: AsyncOpenAI | None = None

    async def initialize(self) -> None:
        self._client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self._settings.open_router_api_key,
            default_headers={"HTTP-Referer": "https://evropuvefur.is"},
        )
        logger.info("LLMService initialized (OpenRouter)")

    async def close(self) -> None:
        if self._client:
            await self._client.close()
        self._client = None
        logger.info("LLMService closed")

    # ── Fallback helper ────────────────────────────────────────

    def _fallback_model(self, failed_model: str, kind: str = "pro") -> str | None:
        """Return the config.py default model if it differs from *failed_model*."""
        key = f"model.{kind}_name"
        default = settings_service._registry[key].default
        if default and default != failed_model:
            return default
        return None

    # ── Scope guard ──────────────────────────────────────────

    async def check_scope(self, query: str) -> str:
        """Returns 'yes', 'adjacent', or 'no'. Always uses Flash (no Pro quota)."""
        scope_prompt = settings_service.get("prompt.scope_guard")
        flash_model = settings_service.get("model.flash_name")
        try:
            response = await self._client.chat.completions.create(
                model=flash_model,
                messages=[{"role": "user", "content": f"{scope_prompt}\n\nQuestion: {query}"}],
                temperature=0,
            )
        except APIError:
            fallback = self._fallback_model(flash_model, "flash")
            if fallback is None:
                raise
            logger.warning("LLM API error with model %r, retrying with %r", flash_model, fallback)
            response = await self._client.chat.completions.create(
                model=fallback,
                messages=[{"role": "user", "content": f"{scope_prompt}\n\nQuestion: {query}"}],
                temperature=0,
            )
        result = response.choices[0].message.content.strip().lower()
        if result not in ("yes", "adjacent", "no"):
            logger.warning("Scope guard returned unexpected value: %s, defaulting to 'adjacent'", result)
            return "adjacent"
        return result

    # ── Model selection ──────────────────────────────────────

    async def select_model(self) -> str:
        """Check Pro quota; return Pro model name if under limit, else Flash."""
        pro_limit = settings_service.get_int("model.pro_daily_limit")
        pro_name = settings_service.get("model.pro_name")
        flash_name = settings_service.get("model.flash_name")
        pro_count = await db.quota_get("pro")
        if pro_count < pro_limit:
            return pro_name
        logger.info("Pro quota exhausted (%d/%d), falling back to Flash",
                     pro_count, pro_limit)
        return flash_name

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
            lang_instruction = settings_service.get("prompt.lang_override_en")
        elif language == "is":
            lang_instruction = settings_service.get("prompt.lang_override_is")
        header = settings_service.get("prompt.context_header")
        return f"{header}\n\n{context}{lang_instruction}"

    def _build_messages(self, system_prompt: str, user_content: str) -> list[dict]:
        return [
            {"role": "system", "content": system_prompt + JSON_FORMAT_INSTRUCTION},
            {"role": "user", "content": user_content},
        ]

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
        system_prompt = settings_service.get("prompt.system")
        messages = self._build_messages(system_prompt, user_content)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": settings_service.get_float("model.temperature"),
            "response_format": {"type": "json_object"},
            "stream": True,
        }
        if include_thinking:
            kwargs["extra_body"] = {
                "reasoning": {
                    "effort": "medium",
                    "max_tokens": settings_service.get_int("model.thinking_budget"),
                },
            }

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except APIError:
            kind = "pro" if "pro" in model.lower() else "flash"
            fallback = self._fallback_model(model, kind)
            if fallback is None:
                raise
            logger.warning("LLM API error with model %r, retrying stream with %r", model, fallback)
            model = fallback
            kwargs["model"] = model
            stream = await self._client.chat.completions.create(**kwargs)

        async def text_iterator():
            json_buffer = []  # accumulate full JSON for final parse
            # State machine for incremental answer extraction
            # States: "before" → waiting for "answer":" prefix
            #         "in_answer" → inside the answer string value
            #         "after" → past the answer string, accumulating rest
            state = "before"
            escape = False  # next char is escaped

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Handle reasoning/thinking content (OpenRouter extension)
                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning and include_thinking:
                    yield ("thinking", reasoning)
                    continue

                text = delta.content
                if not text:
                    continue

                json_buffer.append(text)

                if state == "before":
                    # Check if we've accumulated enough to find "answer": "
                    joined = "".join(json_buffer)
                    match = re.search(r'"answer"\s*:\s*"', joined)
                    if match:
                        state = "in_answer"
                        # Anything after the marker is answer content
                        after_marker = joined[match.end():]
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
                    for ch in text:
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
        system_prompt = settings_service.get("prompt.system")
        messages = self._build_messages(system_prompt, user_content)

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": settings_service.get_float("model.temperature"),
            "response_format": {"type": "json_object"},
        }
        if include_thinking:
            kwargs["extra_body"] = {
                "reasoning": {
                    "effort": "medium",
                    "max_tokens": settings_service.get_int("model.thinking_budget"),
                },
            }

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except APIError:
            kind = "pro" if "pro" in model.lower() else "flash"
            fallback = self._fallback_model(model, kind)
            if fallback is None:
                raise
            logger.warning("LLM API error with model %r, retrying with %r", model, fallback)
            model = fallback
            kwargs["model"] = model
            response = await self._client.chat.completions.create(**kwargs)

        # Extract thinking/reasoning if available
        thinking_text: str | None = None
        if include_thinking:
            reasoning = getattr(response.choices[0].message, "reasoning_content", None)
            if reasoning:
                thinking_text = reasoning

        raw_json = response.choices[0].message.content or ""

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

    # ── Web search generation (no RAG) ────────────────────────

    def _web_search_model(self, model: str) -> str:
        """Append :online suffix for OpenRouter web search."""
        return f"{model}:online"

    async def generate_web_search_stream(
        self, query: str, language: str = "auto",
        include_thinking: bool = False,
    ):
        """Returns (model_used, async_iterator) for web search mode.
        Iterator yields ("thinking", text), ("answer", text), or ("references", [])."""
        model = await self.select_model()
        online_model = self._web_search_model(model)

        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        system_prompt = settings_service.get("prompt.web_search")
        lang_instruction = ""
        if language == "en":
            lang_instruction = settings_service.get("prompt.lang_override_en")
        elif language == "is":
            lang_instruction = settings_service.get("prompt.lang_override_is")

        messages = [
            {"role": "system", "content": system_prompt + lang_instruction},
            {"role": "user", "content": query},
        ]

        kwargs: dict = {
            "model": online_model,
            "messages": messages,
            "temperature": settings_service.get_float("model.temperature"),
            "stream": True,
        }
        if include_thinking:
            kwargs["extra_body"] = {
                "reasoning": {
                    "effort": "medium",
                    "max_tokens": settings_service.get_int("model.thinking_budget"),
                },
            }

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except APIError:
            kind = "pro" if "pro" in model.lower() else "flash"
            fallback = self._fallback_model(model, kind)
            if fallback is None:
                raise
            logger.warning("LLM API error with model %r, retrying with %r", online_model, fallback)
            model = fallback
            online_model = self._web_search_model(model)
            kwargs["model"] = online_model
            stream = await self._client.chat.completions.create(**kwargs)

        async def text_iterator():
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning and include_thinking:
                    yield ("thinking", reasoning)
                    continue

                text = delta.content
                if text:
                    yield ("answer", text)

            yield ("references", [])

        return online_model, text_iterator()

    async def generate_web_search_non_streaming(
        self, query: str, language: str = "auto",
        include_thinking: bool = False,
    ) -> tuple[str, str, str | None, list[str]]:
        """Returns (model_used, answer_text, thinking_text_or_None, [])."""
        model = await self.select_model()
        online_model = self._web_search_model(model)

        model_key = "pro" if "pro" in model.lower() else "flash"
        await db.quota_increment(model_key)

        system_prompt = settings_service.get("prompt.web_search")
        lang_instruction = ""
        if language == "en":
            lang_instruction = settings_service.get("prompt.lang_override_en")
        elif language == "is":
            lang_instruction = settings_service.get("prompt.lang_override_is")

        messages = [
            {"role": "system", "content": system_prompt + lang_instruction},
            {"role": "user", "content": query},
        ]

        kwargs: dict = {
            "model": online_model,
            "messages": messages,
            "temperature": settings_service.get_float("model.temperature"),
        }
        if include_thinking:
            kwargs["extra_body"] = {
                "reasoning": {
                    "effort": "medium",
                    "max_tokens": settings_service.get_int("model.thinking_budget"),
                },
            }

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except APIError:
            kind = "pro" if "pro" in model.lower() else "flash"
            fallback = self._fallback_model(model, kind)
            if fallback is None:
                raise
            logger.warning("LLM API error with model %r, retrying with %r", online_model, fallback)
            model = fallback
            online_model = self._web_search_model(model)
            kwargs["model"] = online_model
            response = await self._client.chat.completions.create(**kwargs)

        thinking_text: str | None = None
        if include_thinking:
            reasoning = getattr(response.choices[0].message, "reasoning_content", None)
            if reasoning:
                thinking_text = reasoning

        answer_text = response.choices[0].message.content or ""
        return online_model, answer_text, thinking_text, []
