"""Endpoint tests for POST /api/v1/query — model param wiring.

We replace `app.state.rag` with a fake that records what `model_override`
the router forwards. This isolates the public-API contract from the LLM,
DB, and embedding layers.
"""
import pytest
from fastapi.testclient import TestClient


class FakeRAG:
    def __init__(self):
        self.calls: list[dict] = []

    async def process_query_json(self, query, top_k, language, **kwargs):
        self.calls.append({"stream": False, "query": query, "kwargs": kwargs})
        return {
            "query": query,
            "answer": "stub",
            "references": [],
            "model_used": f"google/gemini-3-{kwargs.get('model_override') or 'flash'}-preview",
            "cached": False,
            "query_id": "test-id",
            "query_log_id": None,
            "scope_declined": False,
        }

    async def process_query_stream(self, query, top_k, language, **kwargs):
        self.calls.append({"stream": True, "query": query, "kwargs": kwargs})
        # Yield the SSE events shape so EventSourceResponse is happy.
        yield {"event": "done", "data": "{}"}


@pytest.fixture
def client_and_fake(monkeypatch):
    # `app.main` opens a Postgres connection at lifespan startup, which would
    # block the TestClient in environments without a live DB. Mount the query
    # router on a fresh FastAPI app instead — that exercises the same router
    # code path without needing the DB / embeddings / OpenRouter to be live.
    from fastapi import FastAPI
    from app.routers.query import router as query_router
    from app.middleware.rate_limit import limiter

    # The @limiter.limit("10/minute") decorator on query_endpoint is otherwise
    # unconditionally active; disabling slowapi's module-level flag bypasses
    # it. monkeypatch.setattr auto-restores after the test so other tests in
    # the same process aren't affected.
    monkeypatch.setattr(limiter, "enabled", False)

    app = FastAPI()
    app.include_router(query_router)
    fake = FakeRAG()
    app.state.rag = fake
    # The @limiter.limit decorator inside the router still looks up
    # app.state.limiter via slowapi, even though no SlowAPIMiddleware is
    # mounted on this isolated app.
    app.state.limiter = limiter
    with TestClient(app) as client:
        yield client, fake


def _post(client, **body):
    return client.post("/api/v1/query", json={"query": "Hvað er ESB?", "stream": False, **body})


def test_omitted_model_resolves_to_flash(client_and_fake):
    client, fake = client_and_fake
    r = _post(client)
    assert r.status_code == 200
    assert fake.calls[-1]["kwargs"]["model_override"] == "flash"


def test_explicit_pro_resolves_to_pro(client_and_fake):
    client, fake = client_and_fake
    r = _post(client, model="pro")
    assert r.status_code == 200
    assert fake.calls[-1]["kwargs"]["model_override"] == "pro"


def test_explicit_flash_resolves_to_flash(client_and_fake):
    client, fake = client_and_fake
    r = _post(client, model="flash")
    assert r.status_code == 200
    assert fake.calls[-1]["kwargs"]["model_override"] == "flash"


def test_invalid_model_falls_back_to_flash(client_and_fake):
    client, fake = client_and_fake
    r = _post(client, model="banana")
    assert r.status_code == 200
    assert fake.calls[-1]["kwargs"]["model_override"] == "flash"


def test_pro_normalisation_case_and_whitespace(client_and_fake):
    client, fake = client_and_fake
    r = _post(client, model=" PRO ")
    assert r.status_code == 200
    assert fake.calls[-1]["kwargs"]["model_override"] == "pro"


def test_non_string_model_returns_422(client_and_fake):
    client, _fake = client_and_fake
    r = _post(client, model=123)
    assert r.status_code == 422


def test_oversized_model_string_returns_422(client_and_fake):
    client, _fake = client_and_fake
    # max_length=32; 33 chars should be rejected by Pydantic.
    r = _post(client, model="x" * 33)
    assert r.status_code == 422


def test_streaming_branch_forwards_model_override(client_and_fake):
    # Pin down that the stream=True call site forwards model_override too —
    # a refactor that drops it from `process_query_stream(...)` should fail
    # this test.
    client, fake = client_and_fake
    r = client.post(
        "/api/v1/query",
        json={"query": "Hvað er ESB?", "stream": True, "model": "pro"},
    )
    assert r.status_code == 200
    assert fake.calls[-1]["stream"] is True
    assert fake.calls[-1]["kwargs"]["model_override"] == "pro"
