# Admin batch upload + playground parity — design

## Context

Three admin-side improvements bundled into one spec because they share the deploy and touch overlapping files:

1. **Citation links should open in a new tab.** Today inline `[[N]](url)` links use default `<a target>` behavior (same tab), which pulls reviewers away from the answer they're reviewing.
2. **Admin playground should match reviewer playground.** The reviewer playground has Web Search, model selector (Pro/Flash/Auto), and RAG advanced controls; the admin playground only has language + stream. The two UIs drift without a shared component.
3. **Bulk question upload for batch processing.** Operators want to upload `questions.jsonl` (1,257 questions) and have the system answer each one twice — once via RAG, once via web search — using Gemini 3.1 Pro. Questions queue up, processing continues in the background across page navigations, and each answered question lands in the reviewer queue like a regular playground submission. Progress is visible on an admin page.

## Design decisions (resolved during brainstorming)

- **Model for batch**: Pro-strict. Worker always uses `model_override="pro"`, bypassing daily quota. Rate-limits are handled by 429-retry, not by quota enforcement.
- **Worker architecture**: single in-process `asyncio.Task` launched at FastAPI `lifespan` startup. State lives in Postgres; survives restart. Single worker means no concurrency races.
- **Attribution**: seed a `review_users` row with username `batch`. All batch query_log rows get `reviewer_id = batch_user_id`. Batch queries appear in the normal reviewer queue.
- **Failure handling**: 3 automatic retries with exponential backoff (2s, 4s, 8s), then status=`failed` with error message stored. Both per-item retry (click failed row → retry) and bulk "Retry all failed" available.
- **Concurrency**: strictly one batch at a time, FIFO. If a second batch is uploaded while one is running, its items queue after the first batch's.
- **Per-question output**: two `batch_items` rows (mode=`rag`, mode=`websearch`), each producing its own `query_log` row so each answer can be reviewed independently.

## Feature 1 — Citation links open in a new tab

Both playground pages render answers via `ReactMarkdown`. Extract a shared `admin/src/components/MarkdownAnswer.tsx` that passes a `components` prop:

```tsx
components={{
  a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}}
```

Replace the inline `<ReactMarkdown>` usage in `PlaygroundPage.tsx` and `ReviewPlaygroundPage.tsx` with `<MarkdownAnswer>`. Any future markdown surface can reuse it.

## Feature 2 — Admin playground feature parity

`PlaygroundPage.tsx` currently lacks Web Search, model selector, thinking toggle, and RAG advanced controls. Rather than copy-paste the reviewer page's UI, extract a shared component:

**`admin/src/components/PlaygroundForm.tsx`** — stateful form that handles all inputs + SSE/JSON submit logic. Props:
- `endpoint: string` — where to POST (`"/api/v1/query"` for admin, `"/api/v1/review/playground"` for reviewer)
- `getAuthHeaders: () => Record<string, string>` — returns `{Authorization: "Bearer ..."}` using either the admin API key or the reviewer JWT

Both `PlaygroundPage.tsx` (admin) and `ReviewPlaygroundPage.tsx` become thin wrappers: set the endpoint + auth function, render `<PlaygroundForm>`.

**Backend check:** the admin `/api/v1/query` endpoint must accept the same request shape as `/api/v1/review/playground` (web_search, model override, include_thinking, top_k, score_threshold). If it doesn't, extend the request schema + router to match. RAGService already supports all these params via `process_query_json` / `process_query_stream`.

## Feature 3 — Batch question upload + background queue

### Schema

Two new tables in `app/db/database.py`:

```sql
CREATE TABLE IF NOT EXISTS query_batches (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    total INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'completed' | 'cancelled'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS batch_items (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES query_batches(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL,           -- from JSONL (e.g., "q_00000")
    question_text TEXT NOT NULL,
    mode TEXT NOT NULL,                  -- 'rag' | 'websearch'
    status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'processing' | 'done' | 'failed' | 'cancelled'
    query_log_id BIGINT REFERENCES query_log(id),  -- set when done
    error TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id ON batch_items (batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_pending ON batch_items (status) WHERE status = 'pending';
```

Progress = `count(*) FILTER (WHERE status IN ('done','failed','cancelled')) / count(*)`.

### Startup seed

On app startup (one-time idempotent): `INSERT INTO review_users (username, password_hash, is_active) VALUES ('batch', '<random-locked-hash>', true) ON CONFLICT (username) DO NOTHING;`. The locked hash means the account cannot log in — it exists purely for attribution. Cache the resolved `batch_user_id` in a module-level variable after startup.

### Background worker

**`app/services/batch_worker.py`** — one `asyncio.Task` launched by FastAPI `lifespan`.

Loop:
1. On startup, run `UPDATE batch_items SET status='pending' WHERE status='processing'` (stale items from a previous crash — safe because single worker).
2. Poll loop: atomically claim one item:
   ```sql
   UPDATE batch_items
   SET status='processing', updated_at=now()
   WHERE id = (
       SELECT id FROM batch_items
       WHERE status='pending'
       ORDER BY id
       LIMIT 1
   )
   RETURNING *;
   ```
3. If no row: `await asyncio.sleep(5)` and retry.
4. Otherwise, call RAGService with appropriate args:
   ```python
   response = await rag.process_query_json(
       query=item.question_text,
       top_k=5,
       language="is",
       model_override="pro",
       web_search=(item.mode == "websearch"),
       reviewer_id=batch_user_id,
       skip_cache=True,  # new flag
   )
   ```
5. On success: the RAGService inserts the `query_log` row itself (already does this). We need the inserted `query_log.id`. Change `RAGService` to return the query_log_id, or include it in `QueryResponse` (already has `query_id` but that's the uuid hex, not the BIGSERIAL). Simplest: have `_log_query` return the PK and bubble it up in a new field on `QueryResponse` or a separate `batch_process` method.

   Then: `UPDATE batch_items SET status='done', query_log_id=?, updated_at=now() WHERE id=?`. After update, check if any items remain with status in (`pending`, `processing`) for this batch; if none, the batch is settled: `UPDATE query_batches SET status='completed', completed_at=now() WHERE id=? AND status='running'`. "Completed" here means "no more work to do" regardless of whether items ended `done`, `failed`, or `cancelled`.
6. On exception:
   - If `retry_count < 3`: `await asyncio.sleep(2 ** (retry_count + 1))`, `UPDATE batch_items SET status='pending', retry_count=retry_count+1` (item will be picked up again).
   - Else: `UPDATE batch_items SET status='failed', error=<str>`, continue.

Graceful shutdown: cancel the task on lifespan shutdown; any in-flight item stays `processing` and gets reset on next startup.

### RAG service changes

Add `skip_cache: bool = False` param to `process_query_json` and `process_query_stream`. When set, skip cache read and cache write. Reuse the existing mechanism (`include_thinking` already gates cache) but make it its own flag since semantics differ.

Also make RAGService.process_query_json return the inserted `query_log_id`. Options:
- Add a new `query_log_id: int | None = None` field to `QueryResponse` (backward-compatible, public API harmless).
- Create a separate `batch_process_query` that returns just `(query_log_id, response)`.

Prefer the former — one field, no new method, public clients ignore it.

### API endpoints

All gated by `verify_api_key` (Bearer), in a new `app/routers/batches.py`:

```
POST   /api/v1/admin/batches                    multipart/form-data .jsonl → {id, total, skipped}
GET    /api/v1/admin/batches                    → [{id, filename, total, done, failed, pending, status, created_at}]
GET    /api/v1/admin/batches/{id}               → {batch meta, items: [...]}
POST   /api/v1/admin/batches/{id}/retry-failed  bulk reset failed → pending (resets retry_count to 0)
POST   /api/v1/admin/batches/{id}/items/{item_id}/retry  single item reset (same behavior)
POST   /api/v1/admin/batches/{id}/cancel        pending → cancelled; batch → cancelled
```

Upload validation: each JSONL line must have `id` (string) and `question_is` (string). Skip invalid lines with a warning; response reports count skipped.

### Frontend

Three new pages + a batches link in the admin sidebar:

- **`BatchUploadPage.tsx`** (`/admin/batches/new`): file input, on change parse JSONL client-side, show preview (total, skipped with reasons). "Start batch" submits `multipart/form-data` to `POST /admin/batches`, then navigates to the batch detail page.
- **`BatchesListPage.tsx`** (`/admin/batches`): table of all batches, newest first. Columns: filename, created_at, total, progress bar (done / total), status badge, "View" link. Polls the list endpoint every 10s while any batch has status `running`.
- **`BatchDetailPage.tsx`** (`/admin/batches/:id`): header with filename, progress bar (done+failed+cancelled / total), counts, buttons: "Retry all failed", "Cancel remaining". Table of items sorted by id: question_id (e.g., `q_00012`), truncated question_text, two status cells (RAG / Web Search). Each cell shows a pill (`pending`/`processing`/`done`/`failed`/`cancelled`). If `done`, pill links to `/review/queries/{query_log_id}`. If `failed`, clicking opens a tooltip with error text and a "Retry" button for just that item. Polls `/admin/batches/:id` every 3s while batch status=`running`.

### Files to create/modify

**New**
- `app/services/batch_worker.py`
- `app/routers/batches.py`
- `app/models/schemas.py` — `BatchCreateResponse`, `BatchListItem`, `BatchDetail`, `BatchItemDetail`, etc.
- `admin/src/pages/BatchUploadPage.tsx`
- `admin/src/pages/BatchesListPage.tsx`
- `admin/src/pages/BatchDetailPage.tsx`
- `admin/src/components/MarkdownAnswer.tsx`
- `admin/src/components/PlaygroundForm.tsx`

**Modified**
- `app/db/database.py` — new schema + seed batch user on startup
- `app/db/queries.py` — batch CRUD helpers
- `app/services/rag_service.py` — `skip_cache` param, return query_log_id
- `app/main.py` — register batches router + start worker in lifespan
- `app/models/schemas.py` — add `query_log_id` to `QueryResponse`
- `admin/src/App.tsx` (or equivalent router config) — new routes + sidebar entry
- `admin/src/pages/PlaygroundPage.tsx` — replace inline form with `<PlaygroundForm>` + `<MarkdownAnswer>`
- `admin/src-review/pages/ReviewPlaygroundPage.tsx` — same refactor

## Verification

**Feature 1**: In either playground, submit a query that produces cited references; click a `[1]` link in the rendered answer and confirm it opens in a new tab while the playground page stays put.

**Feature 2**: Admin playground page shows Web Search toggle, model selector (Auto / Pro / Flash), include-thinking checkbox, and RAG advanced controls (threshold slider, max-articles input) identically to reviewer playground. Submitting with Web Search on bypasses RAG and cites web URLs.

**Feature 3**:
1. Upload a trimmed version of `questions.jsonl` (first 10 lines) — response reports `{id, total: 20, skipped: 0}`.
2. Batch detail page shows 20 items, progress bar increments as worker processes them.
3. Navigate away, return — progress continues from where it was.
4. Stop the service mid-batch, restart — stuck `processing` items reset to `pending`, batch resumes.
5. Cause a deliberate failure (wrong API key briefly, or an invalid model_override) — item gets `failed` with error message. Click the item's retry → returns to `pending` and succeeds next attempt.
6. Click a `done` item's status pill → opens `/review/queries/{query_log_id}` in a new tab.
7. Upload a second batch while the first is running; confirm it queues and starts only after the first completes.
8. Cancel a running batch; confirm pending items become `cancelled` and worker moves on (if the batch was last in queue, worker goes idle).

## Out of scope (YAGNI)

- Parallel question processing (single-serial worker only)
- Parallel batches
- Cost estimation or pre-flight billing preview
- CSV/plain-text upload formats (JSONL only)
- Resumable/partial uploads
- Batch deletion from UI (can be done via SQL if needed)
- Notifications when a batch completes (no email/webhook)

## Deployment

- Standard deploy: `git pull && cd admin && npm run build && sudo systemctl restart evropuvefur-api`.
- First deploy only: new tables and `batch` user are seeded on startup via existing idempotent schema creation.
- No cache flush needed (existing `query_cache` schema unchanged; `QueryResponse.query_log_id` is additive optional field).
