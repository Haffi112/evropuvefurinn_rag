"""Unit tests for the cache-key hash. Confirms that the model role
namespaces the hash so Pro/Flash answers don't collide in `query_cache`.
"""
from app.services.rag_service import _query_hash


def test_query_hash_default_role_is_auto():
    # Backwards-compatible signature: callers that pass no role get the
    # 'auto' namespace (used by the playground and batch worker).
    assert _query_hash("Hvað er ESB?") == _query_hash("Hvað er ESB?", "auto")


def test_query_hash_normalises_query_text():
    # Whitespace and case should not matter for the query portion.
    assert _query_hash("  Hvað er ESB?  ", "flash") == _query_hash("hvað er esb?", "flash")


def test_query_hash_separates_pro_and_flash():
    pro = _query_hash("Hvað er ESB?", "pro")
    flash = _query_hash("Hvað er ESB?", "flash")
    assert pro != flash


def test_query_hash_separates_auto_from_pro_and_flash():
    auto = _query_hash("Hvað er ESB?", "auto")
    pro = _query_hash("Hvað er ESB?", "pro")
    flash = _query_hash("Hvað er ESB?", "flash")
    assert len({auto, pro, flash}) == 3


def test_query_hash_call_site_pattern():
    # Mirrors how rag_service.py uses _query_hash:
    #   _query_hash(query, model_override or "auto")
    # so that None (playground/batch) → "auto" namespace, while "pro"/"flash"
    # (public endpoint after _resolve_model) get their own namespaces.
    assert _query_hash("q", None or "auto") == _query_hash("q", "auto")
    assert _query_hash("q", "pro" or "auto") == _query_hash("q", "pro")
    assert _query_hash("q", "flash" or "auto") == _query_hash("q", "flash")
