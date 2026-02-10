# Evrópuvefurinn API

Middleware and RAG backend for [Evrópuvefurinn](https://evropuvefur.is) — an Icelandic-language Q&A site about the European Union. The API stores ~670 articles in PostgreSQL, indexes them in Pinecone for semantic search, and uses Google Gemini to generate answers grounded in retrieved content.

**Stack:** FastAPI, asyncpg, Pinecone (multilingual-e5-large, 1024 dims), Google Gemini 3 Pro/Flash, SSE streaming.

## Prerequisites

- Python 3.12+
- PostgreSQL 15+
- [Pinecone](https://pinecone.io) account (free tier works)
- [Google AI Studio](https://aistudio.google.com) API key (Gemini)
- [uv](https://docs.astral.sh/uv/) package manager

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
# Edit .env — fill in PINECONE_API_KEY, GEMINI_API_KEY, CMS_API_KEY, DATABASE_URL

# 5. Start PostgreSQL
brew services start postgresql    # macOS
# or: docker run -d --name pg -p 5432:5432 -e POSTGRES_DB=evropuvefur -e POSTGRES_PASSWORD=pass postgres:15

# 6. Create database
createdb evropuvefur

# 7. Create Pinecone index (one-time)
# In Pinecone console: create serverless index
#   Name: evropuvefur
#   Dimensions: 1024
#   Metric: cosine
#   Cloud: aws, Region: eu-west-1

# 8. Start the API (tables auto-created on first run)
uvicorn app.main:app --reload

# 9. Seed articles from the JSON dataset
python scripts/seed_articles.py

# 10. Verify
curl http://localhost:8000/api/v1/health
```

## API Endpoints

All endpoints are prefixed with `/api/v1`. Endpoints marked with a key require the `X-API-Key` header.

| Method   | Path                       | Auth | Description                          |
|----------|----------------------------|------|--------------------------------------|
| `GET`    | `/health`                  |      | Health check (Postgres, Pinecone, Gemini) |
| `GET`    | `/stats`                   | key  | Dashboard stats and quota info       |
| `GET`    | `/articles`                |      | List articles (paginated)            |
| `GET`    | `/articles/{article_id}`   |      | Get single article                   |
| `POST`   | `/articles`                | key  | Create article                       |
| `PUT`    | `/articles/{article_id}`   | key  | Update article                       |
| `DELETE` | `/articles/{article_id}`   | key  | Delete article                       |
| `POST`   | `/articles/bulk`           | key  | Bulk upsert articles (max 100/batch) |
| `POST`   | `/query`                   |      | RAG query (supports SSE streaming)   |

### Query example

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Hvað er ESB?", "stream": false}'
```

## Seeding Data

The seed script loads all ~670 articles from the prototype's JSON dataset into the running API:

```bash
# Uses defaults: --api-url http://localhost:8000, reads CMS_API_KEY from .env
python scripts/seed_articles.py

# Or override:
python scripts/seed_articles.py --api-url https://your-api.onrender.com --api-key your-key
```

The existing `scripts/migrate_articles.py` is available for production use with explicit flags.

## Render Deployment

### 1. Create services

Use the **render.yaml** Blueprint (recommended) or set up manually:

**Option A — Blueprint:**
Push this repo to GitHub, then in Render Dashboard → **New** → **Blueprint** → select the repo. Render reads `render.yaml` and creates the web service + database.

**Option B — Manual setup:**

1. **Web Service:** New Web Service → Python → connect your repo
   - Root directory: `evropuvefur_api`
   - Build command: `pip install .`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

2. **PostgreSQL:** New PostgreSQL → name it `evropuvefur-db` (starter plan)

### 2. Environment variables

In the web service settings, add:

| Variable              | Value                                              |
|-----------------------|----------------------------------------------------|
| `DATABASE_URL`        | Internal connection string from Render PostgreSQL   |
| `PINECONE_API_KEY`    | Your Pinecone API key                              |
| `PINECONE_INDEX_NAME` | `evropuvefur`                                      |
| `GEMINI_API_KEY`      | Your Google AI Studio key                          |
| `CMS_API_KEY`         | A strong secret for article management             |
| `APP_ENV`             | `production`                                       |
| `CORS_ALLOWED_ORIGINS`| `https://www.evropuvefur.is,https://evropuvefur.is`|

### 3. Health check

Set the health check path to `/api/v1/health` in the web service settings.

### 4. Seed production data

After deployment, seed articles from your local machine:

```bash
python scripts/seed_articles.py \
  --api-url https://your-api.onrender.com \
  --api-key your-production-cms-api-key
```

## Environment Variables

Full reference (see `.env.template`):

| Variable               | Default                    | Description                              |
|------------------------|----------------------------|------------------------------------------|
| `APP_ENV`              | `development`              | Environment name                         |
| `APP_VERSION`          | `1.0.0`                    | Reported in health check                 |
| `LOG_LEVEL`            | `info`                     | Python log level                         |
| `CMS_API_KEY`          | `change-me-to-a-secret`    | API key for article management endpoints |
| `DATABASE_URL`         | `postgresql://...`         | PostgreSQL connection string             |
| `PINECONE_API_KEY`     |                            | Pinecone API key                         |
| `PINECONE_INDEX_NAME`  | `evropuvefur`              | Pinecone index name                      |
| `PINECONE_CLOUD`       | `aws`                      | Pinecone cloud provider                  |
| `PINECONE_REGION`      | `eu-west-1`                | Pinecone region                          |
| `GEMINI_API_KEY`       |                            | Google AI Studio API key                 |
| `GEMINI_PRO_MODEL`     | `gemini-3-pro`             | Model for complex queries                |
| `GEMINI_FLASH_MODEL`   | `gemini-3-flash`           | Model for simple queries                 |
| `GEMINI_PRO_DAILY_LIMIT`| `200`                    | Daily Pro model request cap              |
| `CORS_ALLOWED_ORIGINS` | `https://www.evropuvefur.is,...` | Comma-separated allowed origins    |
| `QUERY_RATE_LIMIT`     | `10/minute`                | Rate limit for /query                    |
| `SYNC_RATE_LIMIT`      | `100/minute`               | Rate limit for article endpoints         |
| `QUERY_CACHE_TTL_HOURS`| `24`                       | Query cache time-to-live                 |
| `RAG_TOP_K`            | `5`                        | Number of articles retrieved per query   |
| `RAG_SCORE_THRESHOLD`  | `0.3`                      | Minimum similarity score for retrieval   |
