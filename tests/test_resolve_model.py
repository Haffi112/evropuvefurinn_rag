"""Unit tests for the public model param normaliser used by /api/v1/query.

The helper enforces the public-API contract: omitted or unrecognised values
fall back to Flash; only an explicit case-insensitive 'pro' selects Pro.
"""
import pytest

from app.routers.query import _resolve_model


@pytest.mark.parametrize("raw", [None, "", "flash", "FLASH", " flash ", "banana", "gemini-3.1-pro", "pro1", "0"])
def test_resolve_model_falls_back_to_flash(raw):
    assert _resolve_model(raw) == "flash"


@pytest.mark.parametrize("raw", ["pro", "PRO", " Pro ", "pRo"])
def test_resolve_model_accepts_pro_case_insensitive(raw):
    assert _resolve_model(raw) == "pro"
