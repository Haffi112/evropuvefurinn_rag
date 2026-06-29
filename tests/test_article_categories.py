"""Pages without a published category must be accepted by the ingest layer.

A collaborator seeds answers that belong to no published category. Such pages
arrive with `categories` either absent, an empty list, or an explicit `null`
(depending on the serializer). All three must validate and normalise to an
empty list — `null` is the case that previously 422'd, because a
`default_factory` only fills in a *missing* key, not an explicit `null`.
"""
import pytest
from pydantic import ValidationError

from app.models.schemas import ArticleCreate

BASE = {
    "id": "EV_1",
    "title": "Titill",
    "question": "Spurning?",
    "answer": "Svar.",
    "source_url": "https://evropuvefur.is/svar.php?id=1",
    "date": "2026-01-01",
}


def test_categories_omitted_defaults_to_empty():
    art = ArticleCreate(**BASE)
    assert art.categories == []
    assert art.tags == []


def test_categories_empty_list_accepted():
    art = ArticleCreate(**BASE, categories=[], tags=[])
    assert art.categories == []
    assert art.tags == []


def test_categories_null_normalised_to_empty():
    # The case the middle layer used to reject.
    art = ArticleCreate(**BASE, categories=None, tags=None)
    assert art.categories == []
    assert art.tags == []


def test_categories_values_preserved():
    art = ArticleCreate(**BASE, categories=["ESB", "Grunnur"], tags=["esb"])
    assert art.categories == ["ESB", "Grunnur"]
    assert art.tags == ["esb"]


def test_non_string_category_still_rejected():
    # Coercing null → [] must not weaken the element-type check.
    with pytest.raises(ValidationError):
        ArticleCreate(**BASE, categories=[123])
