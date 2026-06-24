"""Tests for `_extract_structured_answer` — the robust parser that pulls the
answer/references out of the model's `{"answer": ..., "references_used": ...}`
envelope.

Regression context: gemini-3-flash intermittently returns a malformed envelope
(an extra trailing brace, CRLF pretty-print whitespace, or the whole envelope
nested inside the answer field). The previous fallback dumped that raw envelope
into the answer, which users saw as a garbled "answer-inside-an-answer". These
tests pin the rule that the envelope is NEVER leaked into the answer text.
"""
import json

from app.services.llm_service import (
    LLM_RESPONSE_SCHEMA,
    STRUCTURED_RESPONSE_FORMAT,
    LLMService,
    _extract_structured_answer,
)


# ── Decode-time prevention: grammar-constrained structured output ──────────


def test_response_format_is_strict_json_schema():
    """The request forces the schema at the decoder (json_schema + strict),
    not the weak best-effort `json_object` mode."""
    assert STRUCTURED_RESPONSE_FORMAT["type"] == "json_schema"
    js = STRUCTURED_RESPONSE_FORMAT["json_schema"]
    assert js is LLM_RESPONSE_SCHEMA
    assert js["strict"] is True
    schema = js["schema"]
    assert schema["required"] == ["answer", "references_used"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["answer"]["type"] == "string"
    assert schema["properties"]["references_used"]["type"] == "array"


def test_extra_body_forces_schema_enforcing_providers():
    """`provider.require_parameters` makes OpenRouter route only to providers
    that actually enforce the schema rather than silently downgrading."""
    svc = LLMService(settings=None)
    extra = svc._structured_extra_body(include_thinking=False)
    assert extra["provider"]["require_parameters"] is True
    # No reasoning budget unless thinking is requested.
    assert "reasoning" not in extra


def test_wellformed_envelope():
    raw = '{"answer": "Halló heimur", "references_used": ["EV_1", "EV_2"]}'
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Halló heimur"
    assert refs == ["EV_1", "EV_2"]


def test_plain_markdown_without_envelope_is_passed_through():
    raw = "Bara venjulegt markdown svar[[1]](https://x). Engin slaufa."
    answer, refs = _extract_structured_answer(raw)
    assert answer == raw
    assert refs == []


def test_extra_trailing_brace_is_repaired_not_leaked():
    """Guðmundur's captured case: a valid envelope with a stray extra `}` and
    CRLF whitespace. Must recover the real answer + references, and must NOT
    leak the envelope structure into the answer."""
    raw = (
        '{\r\n\r\n"answer": "Kostnaður Íslands við aðild væri um '
        "13–15 milljarðar króna á ári"
        "[[1]](https://www.evropuvefur.is/svar.php?id=60980).\","
        '\r\n\r\n"references_used": ["EV_60980", "EV_60463", "EV_60445"]'
        "\r\n\r\n}\r\n\r\n}"
    )
    answer, refs = _extract_structured_answer(raw)
    assert answer.startswith("Kostnaður Íslands")
    assert "[[1]](https://www.evropuvefur.is/svar.php?id=60980)" in answer
    assert refs == ["EV_60980", "EV_60463", "EV_60445"]
    # The envelope must not bleed into the answer text.
    assert "references_used" not in answer
    assert '"answer"' not in answer


def test_double_wrapped_envelope_is_unwrapped():
    inner = '{"answer": "Innra svarið", "references_used": ["EV_9"]}'
    raw = json.dumps({"answer": inner, "references_used": []})
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Innra svarið"
    # References from the inner (real) envelope are recovered.
    assert refs == ["EV_9"]


def test_code_fenced_envelope_is_parsed():
    raw = '```json\n{"answer": "Með girðingu", "references_used": ["EV_3"]}\n```'
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Með girðingu"
    assert refs == ["EV_3"]


def test_braces_inside_answer_text_do_not_break_parsing():
    raw = '{"answer": "Notaðu {{sniðmát}} og {kóða}", "references_used": []}'
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Notaðu {{sniðmát}} og {kóða}"
    assert refs == []


def test_broken_after_answer_string_salvages_answer_without_leaking():
    """If the answer string itself is intact but the rest of the envelope is
    broken, we salvage the answer text — and still never leak the envelope."""
    raw = '{"answer": "Svarið sjálft er heilt", "references_used'
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Svarið sjálft er heilt"
    assert "references_used" not in answer
    assert refs == []


def test_truncated_mid_answer_string_returns_empty_not_leak():
    """A response truncated mid-answer (no closing quote anywhere) can't be
    salvaged from the buffered JSON — it must yield empty, never the envelope.
    (In streaming mode the live tokens still carry the partial answer.)"""
    raw = '{"answer": "Texti sem var skorinn af í miðju og lokast aldr'
    answer, refs = _extract_structured_answer(raw)
    assert answer == ""
    assert "answer" not in answer


def test_references_used_missing_defaults_to_empty_list():
    raw = '{"answer": "Engar tilvísanir hér"}'
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Engar tilvísanir hér"
    assert refs == []


def test_empty_input():
    assert _extract_structured_answer("") == ("", [])
    assert _extract_structured_answer(None) == ("", [])


def test_partial_envelope_missing_closing_brace_recovers_answer():
    # Valid answer string but the object never closes — regex fallback recovers it.
    raw = '{"answer": "Svar sem nær yfir",  '
    answer, refs = _extract_structured_answer(raw)
    assert answer == "Svar sem nær yfir"
