import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.database import close_pool, init_pool
from app.middleware.rate_limit import setup_rate_limiting
from app.routers import articles, health, query
from app.services.gemini_service import GeminiService
from app.services.embedding_service import EmbeddingService
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)


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

    # Routers
    app.include_router(health.router)
    app.include_router(articles.router)
    app.include_router(query.router)

    return app


app = create_app()
