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


def test_query_hash_call_sites_use_auto_fallback():
    """Pin the literal call-site pattern in rag_service.py.

    If someone refactors `_query_hash(query, model_override or "auto")`
    to `_query_hash(query, model_override)`, public requests would land
    in a `None|...` cache namespace and never hit cache. Internal callers
    (playground, batch worker) that pass model_override=None would get
    `None|...` instead of `auto|...`, dropping cache hits entirely.

    Catch the regression by asserting the literal expression survives
    in both expected call sites.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "app" / "services" / "rag_service.py"
    text = src.read_text(encoding="utf-8")
    pattern = '_query_hash(query, model_override or "auto")'
    count = text.count(pattern)
    assert count == 2, (
        f"Expected exactly 2 call sites using `{pattern}` in {src.name}, "
        f"found {count}. If a call site was refactored, update this test "
        f"and verify the new pattern still namespaces by model role."
    )
