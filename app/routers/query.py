import logging
import time

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.middleware.rate_limit import limiter
from app.models.schemas import QueryRequest, QueryResponse
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


def _resolve_model(raw: str | None) -> str:
    """Map the public `model` param to an internal role.

    Returns ``"pro"`` only for an explicit case-insensitive ``"pro"`` (with
    optional surrounding whitespace). Anything else — including ``None``, the
    empty string, or unknown labels like ``"gemini-3.1-pro"`` — returns
    ``"flash"``. This is the public-API default and differs from the internal
    ``model_override=None`` semantics used by the playground (auto/quota).
    """
    if raw is None:
        return "flash"
    return "pro" if raw.strip().lower() == "pro" else "flash"


def _get_rag(request: Request) -> RAGService:
    return request.app.state.rag


@router.post(
    "/query",
    summary="Ask a question about the EU",
    description=(
        "Submit a natural-language question. The API retrieves the most relevant "
        "articles via semantic search, then generates an AI answer grounded in those "
        "sources.\n\n"
        "**Streaming (default):** Set `stream: true` to receive Server-Sent Events. "
        "Events include `references` (sources found), `chunk` (answer tokens), and "
        "`done` (final metadata).\n\n"
        "**JSON:** Set `stream: false` to receive a single JSON response with the "
        "complete answer and references."
    ),
)
@limiter.limit("10/minute")
async def query_endpoint(request: Request, body: QueryRequest):
    rag = _get_rag(request)
    ip_address = request.client.host if request.client else None
    start_time = time.monotonic()
    logger.info("Query received: stream=%s lang=%s ip=%s", body.stream, body.language, ip_address)

    if body.stream:
        return EventSourceResponse(
            rag.process_query_stream(
                body.query, body.top_k, body.language,
                ip_address=ip_address, start_time=start_time,
                score_threshold=body.score_threshold,
                include_thinking=body.include_thinking,
            )
        )

    try:
        response = await rag.process_query_json(
            body.query, body.top_k, body.language,
            ip_address=ip_address, start_time=start_time,
            score_threshold=body.score_threshold,
            include_thinking=body.include_thinking,
        )
    except Exception:
        logger.error("Non-streaming query failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Villa kom upp við úrvinnslu fyrirspurnar.")
    return response
