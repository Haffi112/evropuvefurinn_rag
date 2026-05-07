# Optional `model` parameter on the public RAG query API

**Status:** Approved (2026-05-07)
**Endpoint:** `POST /api/v1/query`

## Goal

Let public callers of `/api/v1/query` choose between Gemini 3 Flash and Gemini 3.1 Pro for answer generation, by sending an optional `model` field. Invalid or omitted values fall back to Flash.

## Background

The internal plumbing already supports this: `LLMService.select_model("pro" | "flash" | None)` returns the configured Pro or Flash model name, and `RAGService.process_query_*` accepts a `model_override=` kwarg that flows down to it. The admin playground (`ReviewPlaygroundRequest.model`) already exposes a `pro | flash | None` field. The public `QueryRequest` does not.

Default model names (`app/config.py:25-26`):

- `model.pro_name` → `google/gemini-3.1-pro-preview`
- `model.flash_name` → `google/gemini-3-flash-preview`

## Behaviour

| Request `model` value | Resolved role | Model used | Cache namespace |
|---|---|---|---|
| omitted (`null`) | `flash` | Flash | `flash\|<query>` |
| `"pro"`, `" Pro "`, `"PRO"` | `pro` | Pro | `pro\|<query>` |
| `"flash"` | `flash` | Flash | `flash\|<query>` |
| `"banana"`, `""`, `"gemini-3.1-pro"` | `flash` | Flash | `flash\|<query>` |
| any non-string (e.g. integer) | — | — | 422 from Pydantic |

Forcing `"pro"` does not consult the daily Pro quota counter (matches today's playground behaviour). Quota is still incremented when Pro is actually called.

### Behavioural change for existing callers

The current `/api/v1/query` defaults to *auto* selection (Pro until daily quota, then Flash). After this change, omitting `model` means Flash unconditionally. Existing callers that omit the field will silently receive Flash answers instead of Pro. This is intentional — the user has chosen Flash as the safer default for the public surface — but must be flagged in the README so callers can explicitly request `"pro"` if they were relying on the previous behaviour.

The admin playground and the batch worker are unaffected; they continue to call `RAGService` with their existing `model_override` semantics (`None` = auto/quota-based).

## Implementation

### 1. Schema

`app/models/schemas.py`, on `QueryRequest`:

```python
model: str | None = Field(
    default=None,
    max_length=32,
    description=(
        "Optional model selector. 'pro' selects Gemini 3.1 Pro, 'flash' selects "
        "Gemini 3 Flash. Omitted, empty, or any unrecognised value defaults to Flash. "
        "Matching is case-insensitive and whitespace-trimmed."
    ),
)
```

`max_length=32` caps pathological input; the value is `str | None` (not an enum) so unknown strings can normalise to Flash rather than 422.

### 2. Router normalisation

`app/routers/query.py`:

```python
def _resolve_model(raw: str | None) -> str:
    if raw is None:
        return "flash"
    return "pro" if raw.strip().lower() == "pro" else "flash"
```

In `query_endpoint`, compute `resolved = _resolve_model(body.model)` and pass it as `model_override=resolved` to both `process_query_stream` and `process_query_json`.

The router is the only layer that knows the public-API mapping (omitted = flash). The service layer continues to interpret `model_override=None` as "auto/quota-based", preserving the playground's semantics.

### 3. Cache key separation

`app/services/rag_service.py:18`:

```python
def _query_hash(query: str, model_role: str = "auto") -> str:
    normalized = f"{model_role}|{query.strip().lower()}"
    return hashlib.sha256(normalized.encode()).hexdigest()
```

Both call sites (`process_query_json` line 186, `process_query_stream` line 339) pass the resolved role:

```python
qhash = _query_hash(query, model_override or "auto")
```

For the playground (`model_override=None`), the role becomes `"auto"`, giving it its own cache namespace consistent with today's behaviour. Pro and Flash answers from `/api/v1/query` are cached separately under `"pro|..."` and `"flash|..."`.

The cache key uses the *requested* role, not the actual model that produced the answer. If a `model: "pro"` request gets transparently fulfilled by Flash (e.g. upstream Pro fallback), the answer is still cached under `"pro|..."` so a follow-up identical request hits the cache instead of regenerating. `QueryResponse.model_used` reflects the actual model and remains the source of truth for what produced the text.

The `query_cache` table schema is unchanged. Existing rows hashed without a model prefix will never be hit again under the new scheme and expire naturally via TTL.

### 4. LLM service

No changes. `select_model("pro")` and `select_model("flash")` already return the right model names, and quota increment paths are untouched.

### 5. Response

`QueryResponse.model_used` already echoes the resolved model ID (e.g. `"google/gemini-3.1-pro-preview"`), so callers can confirm which model produced the answer. No schema change.

## Tests

In `tests/` (mock `LLMService` and OpenRouter; assert what's passed and what's cached):

1. `test_query_endpoint_model_pro_uses_pro_model` — POST with `model: "pro"`; assert `model_used` ends with `pro-preview` and `LLMService.select_model` was called with `"pro"`.
2. `test_query_endpoint_model_flash_uses_flash_model` — POST with `model: "flash"`; assert Flash.
3. `test_query_endpoint_invalid_model_falls_back_to_flash` — POST with `model: "banana"`; assert Flash.
4. `test_query_endpoint_omitted_model_defaults_to_flash` — POST without `model`; assert Flash. Regression flag for the behavioural change.
5. `test_query_endpoint_model_normalisation` — POST with `model: " PRO "`; assert Pro.
6. `test_query_cache_separated_by_model` — POST same query first with `model: "flash"`, then `model: "pro"`; assert two distinct rows in `query_cache` and that the second call invoked the LLM (no cache hit across models).
7. `test_playground_model_override_unchanged` — Call `RAGService.process_query_json` with `model_override=None`; assert it uses the `"auto"` cache namespace (i.e. existing playground behaviour preserved by the cache-key change).
8. `test_query_endpoint_model_non_string_returns_422` — POST with `model: 123`; assert HTTP 422 from Pydantic.

## Documentation updates

`README.md`:

- **Query (RAG) section (line 91-95):** add a note under the table:
  > `POST /query` accepts an optional `model` field: `"pro"` (Gemini 3.1 Pro) or `"flash"` (Gemini 3 Flash, default). Unrecognised or omitted values use Flash.
- **Query Example section (line 133+):** add a third curl example forcing Pro:
  ```bash
  curl -X POST http://localhost:8000/api/v1/query \
    -H "Content-Type: application/json" \
    -d '{"query": "Hvað er ESB?", "stream": false, "model": "pro"}'
  ```
  And a one-line callout that the previous default (auto/quota-based Pro) is now `"pro"` opt-in only.

The `description=` on the new `QueryRequest.model` field is surfaced automatically by FastAPI's `/docs` (Swagger UI) and `/openapi.json`, so the OpenAPI reference stays in sync without further work.

## Out of scope

- No changes to `ReviewPlaygroundRequest` or `app/routers/review.py`.
- No changes to the batch worker.
- No new admin setting for "default public model"; the default lives in `_resolve_model` and can be promoted to a setting later if needed.
- No new model choices beyond Pro and Flash. Adding more (e.g. a future Gemini 4 tier) would be a follow-up.
