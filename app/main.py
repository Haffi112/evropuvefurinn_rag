import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.database import close_pool, init_pool
from app.middleware.rate_limit import setup_rate_limiting
from app.routers import admin, articles, health, query, review
from app.routers import settings as settings_router
from app.services.gemini_service import GeminiService
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import RAGService
from app.services import settings_service

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static" / "admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Configure logging
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    # Start database
    await init_pool(settings.database_url)

    # Load runtime settings
    settings_service.init_defaults(settings)
    await settings_service.load_cache()

    # Start Embeddings
    emb = EmbeddingService(settings)
    await emb.initialize()
    app.state.embeddings = emb

    # Start Gemini
    gemini = GeminiService(settings)
    await gemini.initialize()

    # Start RAG orchestrator
    rag = RAGService(settings, emb, gemini)
    app.state.rag = rag

    logger.info("Application started (env=%s)", settings.app_env)
    yield

    # Shutdown
    await gemini.close()
    await emb.close()
    await close_pool()
    logger.info("Application shut down")


OPENAPI_TAGS = [
    {
        "name": "query",
        "description": "RAG-powered question answering about the EU and Iceland's membership. "
        "Supports both streaming (SSE) and JSON responses.",
    },
    {
        "name": "articles",
        "description": "CRUD operations for EU-related articles that form the knowledge base. "
        "Write operations require an API key.",
    },
    {
        "name": "operational",
        "description": "Health checks and usage statistics for monitoring the API.",
    },
    {
        "name": "admin",
        "description": "Admin-only endpoints for query logs and analytics. Requires an API key.",
    },
    {
        "name": "review",
        "description": "Review interface for evaluating LLM responses and editing articles. "
        "Uses JWT-based authentication for reviewer accounts.",
    },
]

API_DESCRIPTION = """
Evrópuvefurinn API provides **RAG-powered question answering** about the European Union
and Iceland's relationship with it, backed by a curated knowledge base of ~670 articles.

## Key features

- **Semantic search** — queries are embedded with `multilingual-e5-large` via DeepInfra
  and matched against article vectors stored in pgvector.
- **AI-generated answers** — matched articles are passed to Google Gemini (Pro or Flash)
  to produce grounded, referenced answers in Icelandic or English.
- **Streaming** — the `/query` endpoint supports Server-Sent Events (SSE) for
  real-time token streaming.
- **Article management** — full CRUD + bulk upsert for the knowledge base, with
  automatic vector re-indexing.

## Authentication

Protected endpoints (article writes, stats, admin) require an **API key** sent in the
`X-API-Key` header. Use the **Authorize** button above to enter your key.

## Rate limits

| Endpoint group | Limit |
|----------------|-------|
| `/query` | 10 req/min per IP |
| All other endpoints | 100 req/min per IP |
"""


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Evrópuvefurinn API",
        version=settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting
    setup_rate_limiting(app)

    # API Routers (must come before static/SPA mounts)
    app.include_router(health.router)
    app.include_router(articles.router)
    app.include_router(query.router)
    app.include_router(admin.router)
    app.include_router(review.router)
    app.include_router(settings_router.router)

    # Admin SPA static assets (only mount if directory exists)
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/admin/assets", StaticFiles(directory=str(assets_dir)), name="admin-assets")

    # SPA catch-all — serves index.html for all /admin/* routes
    @app.get("/admin")
    @app.get("/admin/{path:path}")
    async def admin_spa(request: Request, path: str = ""):
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(str(index), media_type="text/html")
        return HTMLResponse("<h1>Admin UI not built</h1><p>Run <code>cd admin && npm run build</code></p>", status_code=404)

    # Review SPA — serves review.html for all /review/* routes
    @app.get("/review")
    @app.get("/review/{path:path}")
    async def review_spa(request: Request, path: str = ""):
        index = STATIC_DIR / "review.html"
        if index.is_file():
            return FileResponse(str(index), media_type="text/html")
        return HTMLResponse("<h1>Review UI not built</h1><p>Run <code>cd admin && npm run build</code></p>", status_code=404)

    return app


app = create_app()
