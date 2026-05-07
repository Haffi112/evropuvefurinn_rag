# Evrópuvefurinn API

RAG backend for [Evrópuvefurinn](https://evropuvefur.is) — an Icelandic-language Q&A site about the European Union, run by the University of Iceland. The API stores ~670 articles in PostgreSQL with pgvector for semantic search, embeds queries via DeepInfra (multilingual-e5-large, 1024 dims), and generates AI answers via OpenRouter with SSE streaming. Includes an admin dashboard and human review interface built with React.

**Stack:** FastAPI, asyncpg, pgvector (multilingual-e5-large via DeepInfra, 1024 dims), OpenRouter (Gemini Pro/Flash), SSE streaming, React + Tailwind CSS + Radix UI admin/review UI.

## Features

- Semantic vector search (pgvector HNSW index + DeepInfra embeddings)
- AI-generated answers (via OpenRouter) with source citations
- SSE streaming responses
- Scope guard (rejects off-topic queries via Flash model)
- Query caching (PostgreSQL, configurable TTL)
- Daily model quota with automatic fallback (Pro → Flash)
- Admin dashboard (React + Tailwind + Radix UI)
- Human review/evaluation interface with JWT authentication
- Runtime-configurable models, prompts, and limits
- Rate limiting (per-IP)
- Full query audit logging with analytics

## Prerequisites

- Python 3.12+
- PostgreSQL 15+ with pgvector extension
- Node.js 20+ (for building admin UI)
- [uv](https://docs.astral.sh/uv/) package manager
- [DeepInfra](https://deepinfra.com) API key (for embeddings)
- [OpenRouter](https://openrouter.ai) API key (for LLM)

## Local Setup

```bash
# 1. Clone & enter the project
cd evropuvefur_api

# 2. Create virtual environment
uv venv --python 3.12
source .venv/bin/activate

# 3. Install dependencies
uv pip install -e ".[dev]"

# 4. Configure environment
cp .env.template .env
# Edit .env — fill in DEEPINFRA_API_KEY, OPEN_ROUTER_API_KEY, CMS_API_KEY, DATABASE_URL, REVIEW_JWT_SECRET

# 5. Start PostgreSQL
brew services start postgresql    # macOS
# or: docker run -d --name pg -p 5432:5432 -e POSTGRES_DB=evropuvefur -e POSTGRES_PASSWORD=pass postgres:15

# 6. Create database (pgvector extension is created automatically on first run)
createdb evropuvefur

# 7. Build admin UI
cd admin && npm ci && npm run build && cd ..

# 8. Start the API (tables auto-created on first run)
uvicorn app.main:app --reload

# 9. Seed articles from the JSON dataset
python scripts/seed_articles.py

# 10. Verify
curl http://localhost:8000/api/v1/health
```

## API Endpoints

All endpoints are prefixed with `/api/v1`.

**Auth types:** `none` = public, `Bearer` = `Authorization: Bearer <CMS_API_KEY>`, `JWT` = reviewer Bearer token from `/review/auth/login`.

### Health & Stats

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | none | Health check (Postgres, embeddings, LLM) |
| `GET` | `/stats` | API key | Dashboard stats, quota info, vector index stats |

### Articles

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/articles` | none | List articles (paginated) |
| `GET` | `/articles/{id}` | none | Get single article |
| `POST` | `/articles` | API key | Create article |
| `PUT` | `/articles/{id}` | API key | Update article |
| `DELETE` | `/articles/{id}` | API key | Delete article |
| `POST` | `/articles/bulk` | API key | Bulk upsert (max 100/batch) |

### Query (RAG)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/query` | none | Ask a question (supports SSE streaming) |

`POST /query` accepts an optional `model` field: `"pro"` selects Gemini 3.1 Pro, anything else (including omission) selects Gemini 3 Flash.

### Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/query-log` | API key | Paginated, filterable query logs |
| `GET` | `/admin/query-log/stats` | API key | Aggregate query statistics |
| `PATCH` | `/admin/query-log/{id}/review-status` | API key | Set review status for a query |
| `POST` | `/admin/reviewers` | API key | Create reviewer account |
| `GET` | `/admin/reviewers` | API key | List all reviewers |
| `DELETE` | `/admin/reviewers/{id}` | API key | Deactivate a reviewer |
| `PUT` | `/admin/reviewers/{id}/reset-password` | API key | Reset reviewer password |
| `GET` | `/admin/reviews` | API key | List all evaluations (paginated) |
| `GET` | `/admin/reviews/export/csv` | API key | Export evaluations as CSV |
| `GET` | `/admin/reviews/export/articles` | API key | Export reviewed articles as ZIP |
| `GET` | `/admin/reviews/export/all` | API key | Export all data as ZIP |

### Review

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/review/auth/login` | none | Reviewer login (returns JWT) |
| `GET` | `/review/queries` | JWT | List queries for review |
| `GET` | `/review/queries/{id}` | JWT | Get query detail |
| `POST` | `/review/queries/{id}/evaluate` | JWT | Submit evaluation checklist |
| `POST` | `/review/queries/{id}/article` | JWT | Save edited article draft |
| `GET` | `/review/queries/{id}/article` | JWT | Get latest article draft |
| `GET` | `/review/queries/{id}/export/{fmt}` | JWT | Export article as .md or .docx |

### Settings

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/settings/` | API key | List all runtime settings |
| `PUT` | `/admin/settings/{key}` | API key | Update a setting |
| `DELETE` | `/admin/settings/{key}` | API key | Reset a setting to default |

## Query Example

**JSON response:**

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Hvað er ESB?", "stream": false}'
```

**SSE streaming:**

```bash
curl -N -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Hvað er ESB?", "stream": true}'
```

SSE events: `references` (sources found), `chunk` (answer tokens), `done` (final metadata).

**Force the Pro model:**

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Hvað er ESB?", "stream": false, "model": "pro"}'
```

Without `model`, requests use Gemini 3 Flash. Pass `"model": "pro"` to opt into Gemini 3.1 Pro. Unrecognised values (e.g. `"banana"`) fall back to Flash. Pro and Flash answers are cached under separate keys, so identical queries with different `model` values do not collide.

## Architecture

The RAG pipeline follows these steps for each query:

```
1. Cache check     → Return cached response if fresh hit
2. Scope guard     → Flash model classifies query as EU-related or off-topic
3. Embed query     → DeepInfra multilingual-e5-large → 1024-dim vector
4. Vector search   → pgvector HNSW cosine similarity → top-K articles
5. Fetch articles  → Load full article content from PostgreSQL
6. Generate answer → Pro model (or Flash if quota exceeded) via OpenRouter with article context
7. Cache + log     → Store response in cache, write to query_log
```

## Admin & Review UI

The admin and review interfaces are single-page apps served by the API.

**Build:**

```bash
cd admin && npm ci && npm run build
```

This outputs to `app/static/admin/` which the API serves automatically.

**Access:**
- Admin dashboard: `/admin` (uses API key auth)
- Review interface: `/review` (uses JWT auth)

**Tech:** React 19, TypeScript, Vite, Tailwind CSS 4, Radix UI, TanStack Query.

## Seeding Data

```bash
# Uses defaults: --api-url http://localhost:8000, reads CMS_API_KEY from .env
python scripts/seed_articles.py

# Override for remote API:
python scripts/seed_articles.py --api-url https://your-api.example.com --api-key your-key
```

To re-index embeddings (e.g. after changing the embedding model):

```bash
python scripts/backfill_embeddings.py
```

## Deployment

### University server (primary)

The API runs on `evropa.rhi.hi.is` as a systemd service.

**Service:** `evropuvefur-api.service`

**Update procedure:**

```bash
git pull
pip install .
cd admin && npm ci && npm run build && cd ..
sudo systemctl restart evropuvefur-api
```

## Environment Variables

Full reference (see `.env.template`):

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Environment name |
| `APP_VERSION` | `1.0.0` | Reported in health check |
| `LOG_LEVEL` | `info` | Python log level |
| `CMS_API_KEY` | `change-me-to-a-secret` | API key for protected endpoints |
| `REVIEW_JWT_SECRET` | `change-me` | Secret for reviewer JWT tokens |
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `DEEPINFRA_API_KEY` | | DeepInfra API key (for embeddings) |
| `DEEPINFRA_MODEL` | `intfloat/multilingual-e5-large` | Embedding model |
| `OPEN_ROUTER_API_KEY` | | OpenRouter API key (for LLM) |
| `LLM_PRO_MODEL` | `google/gemini-3.1-pro-preview` | Model for complex queries |
| `LLM_FLASH_MODEL` | `google/gemini-3-flash-preview` | Model for scope guard + fallback |
| `LLM_PRO_DAILY_LIMIT` | `200` | Daily Pro model request cap |
| `CORS_ALLOWED_ORIGINS` | `https://www.evropuvefur.is,...` | Comma-separated allowed origins |
| `QUERY_RATE_LIMIT` | `10/minute` | Rate limit for /query |
| `SYNC_RATE_LIMIT` | `100/minute` | Rate limit for article endpoints |
| `QUERY_CACHE_TTL_HOURS` | `24` | Query cache time-to-live |
| `RAG_TOP_K` | `5` | Number of articles retrieved per query |
| `RAG_SCORE_THRESHOLD` | `0.3` | Minimum similarity score for retrieval |

## Rate Limiting

Rate limits are enforced per IP address using [slowapi](https://github.com/laurentS/slowapi):

| Endpoint group | Limit |
|----------------|-------|
| `/query` | 10 req/min |
| All other endpoints | 100 req/min |
