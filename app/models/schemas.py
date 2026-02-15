from datetime import datetime

from pydantic import BaseModel, Field


# ── Article schemas ──────────────────────────────────────────

class ArticleCreate(BaseModel):
    id: str
    title: str
    question: str
    answer: str
    source_url: str
    date: str
    author: str
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ArticleResponse(BaseModel):
    id: str
    title: str
    source_url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    vector_indexed: bool = True


class ArticleFull(BaseModel):
    id: str
    title: str
    question: str
    answer: str
    source_url: str
    date: str
    author: str
    categories: list[str]
    tags: list[str]
    created_at: datetime
    updated_at: datetime | None = None


class ArticleListItem(BaseModel):
    id: str
    title: str
    source_url: str
    date: str
    updated_at: datetime | None = None


class ArticleListResponse(BaseModel):
    articles: list[ArticleListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


class BulkUpsertRequest(BaseModel):
    articles: list[ArticleCreate] = Field(..., max_length=100)


class BulkUpsertResponse(BaseModel):
    processed: int
    created: int
    updated: int
    failed: int
    errors: list[dict]


class DeleteResponse(BaseModel):
    id: str
    deleted: bool = True
    deleted_at: datetime


# ── Query schemas ────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., max_length=1000)
    stream: bool = True
    top_k: int = Field(default=5, ge=1, le=10)
    language: str = "auto"


class Reference(BaseModel):
    id: str
    title: str
    source_url: str
    date: str
    relevance_score: float


class QueryResponse(BaseModel):
    query: str
    answer: str
    references: list[Reference]
    model_used: str
    cached: bool = False
    query_id: str
    scope_declined: bool = False


# ── Operational schemas ──────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    checks: dict[str, str]


class StatsResponse(BaseModel):
    articles: dict
    queries: dict
    quota: dict
    vectors: dict


# ── Query log schemas ───────────────────────────────────────


class QueryLogEntry(BaseModel):
    id: int
    query_text: str
    response_text: str | None = None
    model_used: str | None = None
    references: list[dict] = Field(default_factory=list)
    scope_declined: bool = False
    cached: bool = False
    latency_ms: int | None = None
    ip_address: str | None = None
    created_at: datetime


class QueryLogListResponse(BaseModel):
    logs: list[QueryLogEntry]
    total: int
    page: int
    per_page: int
    total_pages: int


class QueryLogStatsResponse(BaseModel):
    total_queries: int
    today_queries: int
    cached_queries: int
    declined_queries: int
    avg_latency_ms: int


# ── Error schemas ────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
