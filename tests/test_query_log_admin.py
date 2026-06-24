"""Tests for the admin query-log list/export endpoints: time sorting and the
filtered CSV export.
"""
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import admin as admin_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[admin_router.verify_api_key] = lambda: "ok"
    return TestClient(app)


def test_list_forwards_order_param(monkeypatch):
    captured = {}

    async def fake_list(**kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(admin_router.db, "list_query_logs", fake_list)
    r = _client().get("/api/v1/admin/query-log?order=asc&page=2&per_page=10")
    assert r.status_code == 200, r.text
    assert captured["order"] == "asc"
    assert captured["page"] == 2


def test_list_rejects_invalid_order(monkeypatch):
    async def fake_list(**kwargs):
        return [], 0

    monkeypatch.setattr(admin_router.db, "list_query_logs", fake_list)
    r = _client().get("/api/v1/admin/query-log?order=sideways")
    assert r.status_code == 422


def test_export_returns_csv_respecting_filters(monkeypatch):
    captured = {}

    async def fake_export(**kwargs):
        captured.update(kwargs)
        return [
            {
                "id": 7,
                "query_text": "Hvað er EES?",
                "response_text": "Svar með íslensku: þjóð.",
                "model_used": "google/gemini-3.5-flash",
                "mode": "rag",
                "references": [{"id": "a", "title": "t"}],
                "scope_declined": False,
                "cached": True,
                "latency_ms": 1234,
                "review_status": "pending",
                "ip_address": "10.0.0.1",
                "created_at": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            }
        ]

    monkeypatch.setattr(admin_router.db, "export_query_logs", fake_export)
    r = _client().get(
        "/api/v1/admin/query-log/export"
        "?cached=true&search=EES&model_used=google/gemini-3.5-flash"
        "&date_from=2026-05-01T00:00:00&order=asc"
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
    assert ".csv" in r.headers["content-disposition"]

    # Filters forwarded to the DB layer (export matches what the operator views).
    assert captured["cached"] is True
    assert captured["search"] == "EES"
    assert captured["model_used"] == "google/gemini-3.5-flash"
    assert captured["order"] == "asc"
    assert captured["date_from"] == datetime(2026, 5, 1, 0, 0)

    body = r.content.decode("utf-8-sig")  # strip BOM
    lines = body.strip().splitlines()
    assert lines[0].startswith("id,created_at,query_text")
    assert "Hvað er EES?" in body
    assert "2026-06-01T12:00:00+00:00" in body  # created_at serialized
    # references serialized as a count, not raw JSON.
    assert "rag" in lines[1]


def test_export_empty_still_returns_header(monkeypatch):
    async def fake_export(**kwargs):
        return []

    monkeypatch.setattr(admin_router.db, "export_query_logs", fake_export)
    r = _client().get("/api/v1/admin/query-log/export")
    assert r.status_code == 200
    body = r.content.decode("utf-8-sig")
    assert body.strip().startswith("id,created_at,query_text")
