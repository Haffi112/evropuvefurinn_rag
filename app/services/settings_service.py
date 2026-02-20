"""Runtime-configurable settings backed by PostgreSQL with in-memory cache.

Module-level service (no class) matching the app/db/queries.py pattern.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.db.database import get_pool

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


@dataclass
class SettingMeta:
    label: str
    description: str
    category: str  # "model" or "prompt"
    default: str
    input_type: str  # "text", "number", "textarea"


# ── Registry ─────────────────────────────────────────────────
# Populated with static defaults; init_defaults() overrides from env/YAML.

_registry: dict[str, SettingMeta] = {}
_cache: dict[str, str] = {}


def _build_registry() -> dict[str, SettingMeta]:
    return {
        "model.pro_name": SettingMeta(
            label="Pro Model Name",
            description="Gemini Pro model identifier",
            category="model", default="", input_type="text",
        ),
        "model.flash_name": SettingMeta(
            label="Flash Model Name",
            description="Gemini Flash model identifier",
            category="model", default="", input_type="text",
        ),
        "model.pro_daily_limit": SettingMeta(
            label="Pro Daily Limit",
            description="Maximum Pro model requests per day",
            category="model", default="200", input_type="number",
        ),
        "model.temperature": SettingMeta(
            label="Temperature",
            description="LLM generation temperature (0.0-2.0)",
            category="model", default="0.3", input_type="number",
        ),
        "model.thinking_budget": SettingMeta(
            label="Thinking Budget",
            description="Token budget for chain-of-thought reasoning",
            category="model", default="4096", input_type="number",
        ),
        "prompt.system": SettingMeta(
            label="System Prompt",
            description="Main system instruction for the LLM",
            category="prompt", default="", input_type="textarea",
        ),
        "prompt.scope_guard": SettingMeta(
            label="Scope Guard Prompt",
            description="Prompt for classifying whether a query is in-scope",
            category="prompt", default="", input_type="textarea",
        ),
        "prompt.decline_is": SettingMeta(
            label="Decline Message (IS)",
            description="Out-of-scope decline message in Icelandic",
            category="prompt",
            default=(
                "Þessi spurning fellur utan efnissviðs Evrópuvefsins. "
                "Evrópuvefurinn svarar spurningum um Evrópusambandið, EES og tengsl Íslands við Evrópu. "
                "Vinsamlegast reyndu aftur með spurningu um þessi efni."
            ),
            input_type="textarea",
        ),
        "prompt.decline_en": SettingMeta(
            label="Decline Message (EN)",
            description="Out-of-scope decline message in English",
            category="prompt",
            default=(
                "This question is outside the scope of Evrópuvefurinn. "
                "Evrópuvefurinn answers questions about the European Union, EEA, and Iceland's relations with Europe. "
                "Please try again with a question about these topics."
            ),
            input_type="textarea",
        ),
        "prompt.no_results_is": SettingMeta(
            label="No Results Message (IS)",
            description="Message when no articles match the query (Icelandic)",
            category="prompt",
            default="Engar greinar fundust í þekkingargrunni sem tengjast þessari spurningu.",
            input_type="textarea",
        ),
        "prompt.no_results_en": SettingMeta(
            label="No Results Message (EN)",
            description="Message when no articles match the query (English)",
            category="prompt",
            default="No articles found in the knowledge base related to this question.",
            input_type="textarea",
        ),
        "prompt.lang_override_en": SettingMeta(
            label="Language Override (EN)",
            description="Instruction appended when query is in English",
            category="prompt",
            default="\n\nIMPORTANT: The user's question is in English. Respond in English.",
            input_type="textarea",
        ),
        "prompt.lang_override_is": SettingMeta(
            label="Language Override (IS)",
            description="Instruction appended when query is in Icelandic",
            category="prompt",
            default="\n\nMIKILVÆGT: Spurning notandans er á íslensku. Svaraðu á íslensku.",
            input_type="textarea",
        ),
        "prompt.context_header": SettingMeta(
            label="Context Header",
            description="Header text before knowledge-base articles in the prompt",
            category="prompt",
            default="## Greinar úr þekkingargrunni",
            input_type="text",
        ),
    }


def init_defaults(settings) -> None:
    """Populate registry defaults from env vars and YAML files. Call once at startup."""
    global _registry
    _registry = _build_registry()

    # Override defaults from Settings (env vars)
    _registry["model.pro_name"].default = settings.gemini_pro_model
    _registry["model.flash_name"].default = settings.gemini_flash_model
    _registry["model.pro_daily_limit"].default = str(settings.gemini_pro_daily_limit)

    # Override defaults from YAML files
    try:
        with open(PROMPTS_DIR / "system_prompt.yaml") as f:
            data = yaml.safe_load(f)
            _registry["prompt.system"].default = data["system"]
    except Exception:
        logger.warning("Could not load system_prompt.yaml for defaults")

    try:
        with open(PROMPTS_DIR / "scope_guard.yaml") as f:
            data = yaml.safe_load(f)
            _registry["prompt.scope_guard"].default = data["task"]
    except Exception:
        logger.warning("Could not load scope_guard.yaml for defaults")

    logger.info("Settings registry initialized with %d keys", len(_registry))


async def load_cache() -> None:
    """Load all app_settings rows into the in-memory cache."""
    global _cache
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM app_settings")
    _cache = {row["key"]: row["value"] for row in rows}
    logger.info("Settings cache loaded: %d overrides", len(_cache))


# ── Read helpers ─────────────────────────────────────────────

def get(key: str) -> str:
    """Return the cached override, or the registry default."""
    if key in _cache:
        return _cache[key]
    meta = _registry.get(key)
    if meta:
        return meta.default
    raise KeyError(f"Unknown setting: {key}")


def get_int(key: str) -> int:
    return int(get(key))


def get_float(key: str) -> float:
    return float(get(key))


# ── Write helpers ────────────────────────────────────────────

async def set_value(key: str, value: str) -> None:
    """Upsert a setting to the DB and update the cache."""
    if key not in _registry:
        raise KeyError(f"Unknown setting: {key}")
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ($1, $2, now())
               ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
            key, value,
        )
    _cache[key] = value
    logger.info("Setting %s updated", key)


async def delete_override(key: str) -> None:
    """Remove a DB override, reverting to the registry default."""
    if key not in _registry:
        raise KeyError(f"Unknown setting: {key}")
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key = $1", key)
    _cache.pop(key, None)
    logger.info("Setting %s reset to default", key)


# ── Bulk read ────────────────────────────────────────────────

def get_all() -> list[dict]:
    """Return all settings with value, default, is_overridden, and metadata."""
    result = []
    for key, meta in _registry.items():
        is_overridden = key in _cache
        result.append({
            "key": key,
            "value": _cache[key] if is_overridden else meta.default,
            "default": meta.default,
            "is_overridden": is_overridden,
            "label": meta.label,
            "description": meta.description,
            "category": meta.category,
            "input_type": meta.input_type,
        })
    return result
