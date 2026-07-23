"""Tests for the DeepInfra embedding client's resilience and caching:

- 429/5xx retry-with-backoff (so a burst from the evropuvefur.is backend that
  briefly trips DeepInfra's rate limit degrades gracefully instead of 500ing an
  end user), and
- the per-process query-embedding LRU (so a hot preset question embeds once,
  not once per request).

No network or DB: a fake httpx client feeds canned responses and asyncio.sleep
is stubbed so backoff is instant.
"""

from types import SimpleNamespace

import httpx
import pytest

from app.services import embedding_service
from app.services.embedding_service import DEEPINFRA_EMBED_URL, EmbeddingService


# ── Test doubles ─────────────────────────────────────────────

class _FakeClient:
    """Records posted payloads and returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url, json=None):
        self.calls.append(json)
        return self._responses.pop(0)


def _ok(embeddings):
    """A 200 response carrying `embeddings` as OpenAI-style indexed data.
    Emitted out of index order to exercise the input-order guarantee."""
    data = [{"index": i, "embedding": e} for i, e in enumerate(embeddings)]
    data.reverse()
    r = httpx.Response(200, json={"data": data})
    r.request = httpx.Request("POST", DEEPINFRA_EMBED_URL)  # raise_for_status needs it
    return r


def _err(status, headers=None):
    r = httpx.Response(status, headers=headers or {})
    r.request = httpx.Request("POST", DEEPINFRA_EMBED_URL)  # raise_for_status needs it
    return r


def _service(responses):
    svc = EmbeddingService(SimpleNamespace(deepinfra_model="test-model"))
    svc._client = _FakeClient(responses)
    return svc


@pytest.fixture
def no_sleep(monkeypatch):
    """Stub asyncio.sleep, recording every backoff delay requested."""
    delays: list[float] = []

    async def _fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(embedding_service.asyncio, "sleep", _fake_sleep)
    return delays


# ── Retry behaviour ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_retries_then_succeeds_on_429(no_sleep):
    svc = _service([_err(429), _err(429), _ok([[0.1, 0.2]])])

    out = await svc.embed_text("halló", input_type="passage")

    assert out == [0.1, 0.2]
    assert len(svc._client.calls) == 3          # two failures + one success
    assert no_sleep == [0.5, 1.0]               # exponential backoff between them


@pytest.mark.asyncio
async def test_retry_honours_numeric_retry_after(no_sleep):
    svc = _service([_err(429, headers={"Retry-After": "2"}), _ok([[1.0]])])

    await svc.embed_text("q", input_type="passage")

    assert no_sleep == [2.0]                     # header wins over exponential backoff


@pytest.mark.asyncio
async def test_retry_after_cap(no_sleep, monkeypatch):
    monkeypatch.setattr(embedding_service, "EMBED_RETRY_AFTER_CAP", 5.0)
    svc = _service([_err(503, headers={"Retry-After": "999"}), _ok([[1.0]])])

    await svc.embed_text("q", input_type="passage")

    assert no_sleep == [5.0]                     # absurd Retry-After is clamped


@pytest.mark.asyncio
async def test_exhausted_retries_raise(no_sleep):
    # EMBED_MAX_RETRIES=3 → 4 attempts, all 429.
    svc = _service([_err(429), _err(429), _err(429), _err(429)])

    with pytest.raises(httpx.HTTPStatusError):
        await svc.embed_text("q", input_type="passage")
    assert len(svc._client.calls) == 4
    assert len(no_sleep) == 3                     # slept before each retry, not the last


@pytest.mark.asyncio
async def test_permanent_error_not_retried(no_sleep):
    svc = _service([_err(400)])

    with pytest.raises(httpx.HTTPStatusError):
        await svc.embed_text("q", input_type="passage")
    assert len(svc._client.calls) == 1            # 4xx (non-429) is not retryable
    assert no_sleep == []


@pytest.mark.asyncio
async def test_batch_preserves_input_order_despite_shuffled_response(no_sleep):
    svc = _service([_ok([[0.0], [1.0], [2.0]])])

    out = await svc.embed_texts_batch(["a", "b", "c"], input_type="passage")

    assert out == [[0.0], [1.0], [2.0]]           # index-sorted back to input order


# ── Query-embedding cache ────────────────────────────────────

@pytest.mark.asyncio
async def test_query_embedding_cached_across_calls(no_sleep):
    svc = _service([_ok([[9.0]])])                # only one response queued

    first = await svc.embed_text("Chat control", input_type="query")
    second = await svc.embed_text("Chat control", input_type="query")

    assert first == second == [9.0]
    assert len(svc._client.calls) == 1            # second call served from cache


@pytest.mark.asyncio
async def test_passage_embeddings_are_not_cached(no_sleep):
    svc = _service([_ok([[1.0]]), _ok([[1.0]])])  # needs two responses

    await svc.embed_text("x", input_type="passage")
    await svc.embed_text("x", input_type="passage")

    assert len(svc._client.calls) == 2            # passage path always hits the API


@pytest.mark.asyncio
async def test_batch_only_embeds_cache_misses(no_sleep):
    svc = _service([_ok([[7.0]]), _ok([[8.0], [9.0]])])

    # Warm the cache with "a".
    await svc.embed_text("a", input_type="query")
    # "a" is cached; only "b" and "c" should be sent.
    out = await svc.embed_texts_batch(["a", "b", "c"], input_type="query")

    assert out == [[7.0], [8.0], [9.0]]           # order preserved across cache + fetch
    assert svc._client.calls[1]["input"] == ["query: b", "query: c"]


@pytest.mark.asyncio
async def test_query_cache_evicts_least_recently_used(no_sleep, monkeypatch):
    monkeypatch.setattr(embedding_service, "QUERY_EMBED_CACHE_SIZE", 2)
    svc = _service([_ok([[1.0]]), _ok([[2.0]]), _ok([[3.0]]), _ok([[1.0]])])

    await svc.embed_text("one", input_type="query")
    await svc.embed_text("two", input_type="query")
    await svc.embed_text("three", input_type="query")   # evicts "one" (LRU)

    assert "one" not in svc._query_cache
    assert set(svc._query_cache) == {"two", "three"}
    # "one" is gone, so asking again re-embeds (consumes the 4th response).
    assert await svc.embed_text("one", input_type="query") == [1.0]
    assert len(svc._client.calls) == 4
