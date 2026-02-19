from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.review_schemas import EvaluationChecklist


# ── Article schemas ──────────────────────────────────────────

class ArticleCreate(BaseModel):
    id: str = Field(description="Unique slug-style identifier for the article.")
    title: str = Field(description="Human-readable article title.")
    question: str = Field(description="The question this article answers.")
    answer: str = Field(description="Full answer text (HTML or plain text).")
    source_url: str = Field(description="Canonical URL where the article is published.")
    date: str = Field(description="Publication date as YYYY-MM-DD string.")
    author: str = Field(description="Author or publishing organization.")
    categories: list[str] = Field(default_factory=list, description="Topic categories.")
    tags: list[str] = Field(default_factory=list, description="Free-form tags.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "hvad-er-esb",
                "title": "Hvað er Evrópusambandið?",
                "question": "Hvað er Evrópusambandið og hvert er hlutverk þess?",
                "answer": "Evrópusambandið (ESB) er efnahagslegt og pólitískt samband 27 Evrópuríkja...",
                "source_url": "https://evropuvefur.is/hvad-er-esb",
                "date": "2025-03-15",
                "author": "Evrópuvefurinn",
                "categories": ["Grunnupplýsingar", "ESB"],
                "tags": ["esb", "stofnanir"],
            }
        }
    )


class ArticleResponse(BaseModel):
    id: str = Field(description="Article identifier.")
    title: str = Field(description="Article title.")
    source_url: str = Field(description="Canonical URL.")
    created_at: datetime | None = Field(default=None, description="When the article was first created.")
    updated_at: datetime | None = Field(default=None, description="When the article was last updated.")
    vector_indexed: bool = Field(default=True, description="Whether the embedding vector was stored.")


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
    articles: list[ArticleCreate] = Field(..., max_length=100, description="List of articles to create or update (max 100).")


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
    query: str = Field(..., max_length=1000, description="The question to ask, in Icelandic or English.")
    stream: bool = Field(default=True, description="If true, return Server-Sent Events; if false, return JSON.")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of source articles to retrieve (1–20).")
    language: str = Field(default="auto", description="Response language: 'is', 'en', or 'auto' (detect from query).")
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0, description="Minimum relevance score to include a source (0.0–1.0). Omit to use the server default.")
    include_thinking: bool = Field(default=False, description="If true, include the model's chain-of-thought reasoning in the response.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "Hvað er Schengen-samkomulagið?",
                "stream": True,
                "top_k": 5,
                "language": "auto",
            }
        }
    )


class Reference(BaseModel):
    id: str = Field(description="Article ID.")
    title: str = Field(description="Article title.")
    source_url: str = Field(description="Link to the original article.")
    date: str = Field(description="Article publication date.")
    relevance_score: float = Field(description="Cosine similarity score (0.0–1.0). Higher = more relevant.")


class QueryResponse(BaseModel):
    query: str = Field(description="The original query text.")
    answer: str = Field(description="AI-generated answer grounded in the retrieved articles.")
    references: list[Reference] = Field(description="Source articles used to generate the answer, ranked by relevance.")
    model_used: str = Field(description="Gemini model that generated the answer (e.g. 'gemini-3-pro').")
    cached: bool = Field(default=False, description="Whether this answer was served from cache.")
    query_id: str = Field(description="Unique identifier for this query (for logging/debugging).")
    scope_declined: bool = Field(default=False, description="True if the query was outside the EU/Iceland scope and was declined.")


# ── Operational schemas ──────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field(description="Overall status: 'healthy' or 'degraded'.")
    version: str = Field(description="API version string.")
    checks: dict[str, str] = Field(description="Per-service status (postgres, embeddings, gemini).")


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
    review_status: str = "pending"


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


# ── Admin review schemas ────────────────────────────────────


class AdminEvaluationListItem(BaseModel):
    id: int
    query_log_id: int
    query_text: str
    reviewer_username: str
    checklist: EvaluationChecklist
    note: str | None
    review_status: str
    has_article: bool
    evaluation_date: datetime
    evaluation_updated: datetime | None = None
    query_date: datetime


class AdminEvaluationListResponse(BaseModel):
    evaluations: list[AdminEvaluationListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


# ── Error schemas ────────────────────────────────────────────

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
