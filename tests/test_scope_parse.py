"""Tests for the scope-guard response parser: the structured
{scope, search_queries} envelope, the legacy one-word reply (a stale DB
prompt override), and malformed output must all degrade safely."""

from app.services.llm_service import _parse_scope_response


def test_structured_envelope():
    scope, queries = _parse_scope_response(
        '{"scope": "yes", "search_queries": '
        '["Er Evrópusambandið með Evrópuher?", "her Evrópuher varnarmál"]}'
    )
    assert scope == "yes"
    assert queries == [
        "Er Evrópusambandið með Evrópuher?",
        "her Evrópuher varnarmál",
    ]


def test_structured_envelope_code_fenced():
    scope, queries = _parse_scope_response(
        '```json\n{"scope": "adjacent", "search_queries": ["a"]}\n```'
    )
    assert scope == "adjacent"
    assert queries == ["a"]


def test_legacy_one_word_reply():
    for word, expected in (("yes", "yes"), ("  No \n", "no"), ("ADJACENT", "adjacent")):
        scope, queries = _parse_scope_response(word)
        assert scope == expected
        assert queries == []


def test_garbage_defaults_to_adjacent():
    scope, queries = _parse_scope_response("I think this question is about the EU.")
    assert scope == "adjacent"
    assert queries == []


def test_malformed_json_defaults_to_adjacent():
    scope, queries = _parse_scope_response('{"scope": "yes", "search_queries": [')
    assert scope == "adjacent"
    assert queries == []


def test_invalid_scope_value_defaults_to_adjacent():
    scope, _ = _parse_scope_response('{"scope": "maybe", "search_queries": []}')
    assert scope == "adjacent"


def test_queries_trimmed_to_two_and_blank_dropped():
    scope, queries = _parse_scope_response(
        '{"scope": "yes", "search_queries": ["  ", "a", "b", "c"]}'
    )
    assert scope == "yes"
    assert queries == ["a", "b"]
