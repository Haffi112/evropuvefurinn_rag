import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.middleware.rate_limit import limiter
from app.models.schemas import QueryRequest, QueryResponse
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["query"])


def _get_rag(request: Request) -> RAGService:
    return request.app.state.rag


@router.post("/query")
@limiter.limit("10/minute")
async def query_endpoint(request: Request, body: QueryRequest):
    rag = _get_rag(request)

    if body.stream:
        return EventSourceResponse(
            rag.process_query_stream(body.query, body.top_k, body.language)
        )

    response = await rag.process_query_json(body.query, body.top_k, body.language)
    return response
