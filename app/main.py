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
from app.routers import admin, articles, health, query
from app.services.gemini_service import GeminiService
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static" / "admin"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Configure logging
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    # Start database
    await init_pool(settings.database_url)

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


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Evrópuvefurinn API",
        version=settings.app_version,
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

    return app


app = create_app()
