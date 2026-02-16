from datetime import datetime

from pydantic import BaseModel


# ── Auth ────────────────────────────────────────────────────

class ReviewLoginRequest(BaseModel):
    username: str
    password: str


class ReviewLoginResponse(BaseModel):
    token: str
    username: str


# ── Reviewer management (admin-facing) ─────────────────────

class ReviewUserCreate(BaseModel):
    username: str
    password: str


class ReviewUserResponse(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime


class ReviewPasswordReset(BaseModel):
    password: str


# ── Evaluation ──────────────────────────────────────────────

class EvaluationChecklist(BaseModel):
    answers_question: bool = False
    factually_accurate: bool = False
    sources_relevant: bool = False
    no_hallucinations: bool = False
    appropriate_scope: bool = False
    language_quality: bool = False


class EvaluationCreate(BaseModel):
    checklist: EvaluationChecklist
    note: str | None = None


class EvaluationResponse(BaseModel):
    id: int
    query_log_id: int
    reviewer_id: int
    checklist: EvaluationChecklist
    note: str | None
    created_at: datetime
    updated_at: datetime | None


# ── Reviewed articles ───────────────────────────────────────

class ReviewedArticleCreate(BaseModel):
    title: str
    edited_response: str


class ReviewedArticleResponse(BaseModel):
    id: int
    query_log_id: int
    reviewer_id: int
    version: int
    title: str
    edited_response: str
    status: str
    created_at: datetime
    updated_at: datetime | None


# ── Query list / detail for review ──────────────────────────

class ReviewQueryListItem(BaseModel):
    id: int
    query_text: str
    model_used: str | None
    review_status: str
    cached: bool
    created_at: datetime
    reviewer_username: str | None


class ReviewQueryListResponse(BaseModel):
    queries: list[ReviewQueryListItem]
    total: int
    page: int
    per_page: int
    total_pages: int


class ReviewQueryDetail(BaseModel):
    id: int
    query_text: str
    response_text: str | None
    model_used: str | None
    references: list[dict]
    scope_declined: bool
    cached: bool
    latency_ms: int | None
    ip_address: str | None
    created_at: datetime
    review_status: str
    evaluation: EvaluationResponse | None = None
    latest_article: ReviewedArticleResponse | None = None
