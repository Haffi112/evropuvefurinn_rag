"""Multi-annotator support tests.

These exercise the pieces of the multi-annotator change that don't require a
live Postgres connection:

  - the pure status-derivation rule (`derive_review_status`)
  - the pydantic schemas (new fields, defaults, round-trip)
  - the CSV header includes the seventh checklist key (`publishable_minor_edits`)
    and a `failed_checks` column

A full DB-level integration test (two reviewers persisting separate rows for
the same query) would need a real Postgres test instance, which the existing
test infra doesn't spin up. The pure tests below cover the algorithmic risk
(status promotion semantics) and the schema/API contract.
"""

from app.db.queries import derive_review_status
from app.models.review_schemas import (
    EvaluationChecklist,
    EvaluationResponse,
    ReviewQueryDetail,
    ReviewQueryListItem,
)
from datetime import datetime, timezone


# ── derive_review_status ────────────────────────────────────


def _all_true_checklist() -> dict:
    return {k: True for k in EvaluationChecklist.model_fields}


def _all_false_checklist() -> dict:
    return {k: False for k in EvaluationChecklist.model_fields}


def test_status_pending_when_no_evaluations():
    assert derive_review_status("pending", []) == "pending"
    assert derive_review_status("pending", None) == "pending"


def test_status_reviewed_when_any_failing():
    cl_pass = _all_true_checklist()
    cl_fail = _all_false_checklist()
    # Mix of passing and failing → reviewed (not approved)
    assert derive_review_status("pending", [cl_pass, cl_fail]) == "reviewed"
    # Single failing → reviewed
    assert derive_review_status("pending", [cl_fail]) == "reviewed"


def test_status_approved_only_when_every_evaluation_all_true():
    cl_pass = _all_true_checklist()
    assert derive_review_status("pending", [cl_pass]) == "approved"
    assert derive_review_status("pending", [cl_pass, cl_pass]) == "approved"


def test_excluded_is_terminal():
    """A reviewer adding an evaluation to an excluded query must not promote it."""
    cl_pass = _all_true_checklist()
    assert derive_review_status("excluded", []) == "excluded"
    assert derive_review_status("excluded", [cl_pass]) == "excluded"


def test_dissenting_reviewer_downgrades_approved_to_reviewed():
    """A second reviewer disagreeing must drop the status off approved."""
    cl_pass = _all_true_checklist()
    cl_partial = {**_all_true_checklist(), "factually_accurate": False}
    # One all-true + one partial = reviewed, not approved
    assert derive_review_status("approved", [cl_pass, cl_partial]) == "reviewed"


def test_empty_checklist_is_not_all_true():
    """A row with an empty checklist dict shouldn't count as all-true."""
    assert derive_review_status("pending", [{}]) == "reviewed"


# ── Schemas: new fields ─────────────────────────────────────


def test_review_query_list_item_accepts_multi_annotator_fields():
    item = ReviewQueryListItem(
        id=1,
        query_text="hvað er ESB?",
        model_used="flash",
        review_status="reviewed",
        cached=False,
        created_at=datetime.now(timezone.utc),
        reviewer_username="alice, bob",
        reviewer_usernames=["alice", "bob"],
        evaluation_count=2,
        i_evaluated=True,
    )
    assert item.evaluation_count == 2
    assert item.reviewer_usernames == ["alice", "bob"]
    assert item.i_evaluated is True


def test_review_query_list_item_defaults_for_legacy_callers():
    item = ReviewQueryListItem(
        id=1,
        query_text="hvað er ESB?",
        model_used="flash",
        review_status="pending",
        cached=False,
        created_at=datetime.now(timezone.utc),
        reviewer_username=None,
    )
    assert item.evaluation_count == 0
    assert item.reviewer_usernames == []
    assert item.i_evaluated is False


def test_review_query_detail_carries_all_evaluations():
    now = datetime.now(timezone.utc)
    e1 = EvaluationResponse(
        id=10, query_log_id=1, reviewer_id=1,
        reviewer_username="alice",
        checklist=EvaluationChecklist(**_all_true_checklist()),
        note=None, duration_seconds=42,
        created_at=now, updated_at=None,
    )
    e2 = EvaluationResponse(
        id=11, query_log_id=1, reviewer_id=2,
        reviewer_username="bob",
        checklist=EvaluationChecklist(**_all_false_checklist()),
        note="disagree", duration_seconds=30,
        created_at=now, updated_at=None,
    )
    detail = ReviewQueryDetail(
        id=1, query_text="?", response_text="…",
        model_used="flash", references=[], scope_declined=False,
        cached=False, latency_ms=100, ip_address=None,
        created_at=now, review_status="reviewed",
        evaluation=e1, all_evaluations=[e1, e2],
    )
    assert len(detail.all_evaluations) == 2
    # Each evaluation carries its own reviewer
    assert detail.all_evaluations[0].reviewer_id != detail.all_evaluations[1].reviewer_id


def test_review_query_detail_defaults_all_evaluations_to_empty():
    now = datetime.now(timezone.utc)
    detail = ReviewQueryDetail(
        id=1, query_text="?", response_text="…",
        model_used="flash", references=[], scope_declined=False,
        cached=False, latency_ms=None, ip_address=None,
        created_at=now, review_status="pending",
    )
    assert detail.all_evaluations == []
    assert detail.evaluation is None


# ── CSV export: 7-key checklist + failed_checks column ─────


# ── Reviewer-facing list endpoint: sanitization + filter ──


def test_list_queries_sanitizes_cross_reviewer_fields(monkeypatch):
    """The /api/v1/review/queries endpoint must strip reviewer_username,
    reviewer_usernames, evaluation_count, and replace review_status with
    a per-reviewer label so reviewers cannot see peer evaluations."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import review as review_router
    from app.middleware.review_auth import ReviewUser

    captured: dict = {}

    async def fake_list(**kwargs):
        captured.update(kwargs)
        rows = [
            {
                "id": 1, "query_text": "q1", "model_used": "flash",
                "review_status": "approved",  # cross-reviewer aggregate (should be replaced)
                "cached": False,
                "created_at": "2026-05-15T10:00:00+00:00",
                "mode": "rag",
                "submitted_by": None,
                "evaluation_count": 3,
                "reviewer_usernames": ["alice", "bob", "carol"],
                "reviewer_username": "alice, bob, carol",
                "i_evaluated": True,
            },
            {
                "id": 2, "query_text": "q2", "model_used": "flash",
                "review_status": "reviewed",
                "cached": False,
                "created_at": "2026-05-15T11:00:00+00:00",
                "mode": "websearch",
                "submitted_by": None,
                "evaluation_count": 1,
                "reviewer_usernames": ["alice"],
                "reviewer_username": "alice",
                "i_evaluated": False,
            },
        ]
        return rows, len(rows)

    async def fake_verify():
        return ReviewUser(id=42, username="bob")

    monkeypatch.setattr(review_router.db, "list_query_logs_for_review", fake_list)
    app = FastAPI()
    app.include_router(review_router.router)
    app.dependency_overrides[review_router.verify_review_token] = fake_verify

    with TestClient(app) as client:
        r = client.get("/api/v1/review/queries?mine_filter=pending")

    assert r.status_code == 200, r.text
    # The filter was threaded through to the DB layer
    assert captured["mine_filter"] == "pending"
    assert captured["viewer_id"] == 42

    body = r.json()
    for q in body["queries"]:
        # Cross-reviewer fields stripped
        assert q["reviewer_username"] is None
        assert q["reviewer_usernames"] == []
        assert q["evaluation_count"] == 0
        # Status replaced with per-reviewer label
        assert q["review_status"] in ("done", "pending")

    # i_evaluated → done; otherwise → pending
    assert body["queries"][0]["review_status"] == "done"
    assert body["queries"][1]["review_status"] == "pending"


def test_list_queries_default_filter_is_pending(monkeypatch):
    """Default mine_filter for /api/v1/review/queries is 'pending' so
    reviewers see their to-do queue, not everything."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import review as review_router
    from app.middleware.review_auth import ReviewUser

    captured: dict = {}

    async def fake_list(**kwargs):
        captured.update(kwargs)
        return [], 0

    async def fake_verify():
        return ReviewUser(id=1, username="alice")

    monkeypatch.setattr(review_router.db, "list_query_logs_for_review", fake_list)
    app = FastAPI()
    app.include_router(review_router.router)
    app.dependency_overrides[review_router.verify_review_token] = fake_verify

    with TestClient(app) as client:
        r = client.get("/api/v1/review/queries")

    assert r.status_code == 200
    assert captured["mine_filter"] == "pending"


def test_list_queries_mine_filter_all_disables_per_viewer_filter(monkeypatch):
    """mine_filter=all maps to None in the DB call so legacy callers can opt out."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import review as review_router
    from app.middleware.review_auth import ReviewUser

    captured: dict = {}

    async def fake_list(**kwargs):
        captured.update(kwargs)
        return [], 0

    async def fake_verify():
        return ReviewUser(id=1, username="alice")

    monkeypatch.setattr(review_router.db, "list_query_logs_for_review", fake_list)
    app = FastAPI()
    app.include_router(review_router.router)
    app.dependency_overrides[review_router.verify_review_token] = fake_verify

    with TestClient(app) as client:
        r = client.get("/api/v1/review/queries?mine_filter=all")

    assert r.status_code == 200
    assert captured["mine_filter"] is None


def test_query_detail_strips_peer_evaluations(monkeypatch):
    """The reviewer detail endpoint must omit peer evaluations and replace
    review_status with a per-reviewer label."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import review as review_router
    from app.middleware.review_auth import ReviewUser

    async def fake_get_detail(qid):
        return {
            "id": qid,
            "query_text": "q",
            "response_text": "a",
            "model_used": "flash",
            "references": [],
            "scope_declined": False,
            "cached": False,
            "latency_ms": 100,
            "ip_address": None,
            "created_at": datetime.now(timezone.utc),
            # Aggregate status (would leak peer agreement)
            "review_status": "approved",
            "mode": "rag",
        }

    async def fake_get_eval_for_reviewer(qid, rid):
        return None  # this reviewer hasn't evaluated yet

    async def fake_get_all_evals(qid):
        # Peers exist — but the endpoint must not return this
        return [{"id": 99, "reviewer_id": 7, "reviewer_username": "peer"}]

    async def fake_get_article(qid):
        return None

    async def fake_verify():
        return ReviewUser(id=42, username="bob")

    monkeypatch.setattr(review_router.db, "get_query_log_detail", fake_get_detail)
    monkeypatch.setattr(review_router.db, "get_evaluation_for_reviewer", fake_get_eval_for_reviewer)
    monkeypatch.setattr(review_router.db, "get_all_evaluations_for_query", fake_get_all_evals)
    monkeypatch.setattr(review_router.db, "get_latest_reviewed_article", fake_get_article)

    app = FastAPI()
    app.include_router(review_router.router)
    app.dependency_overrides[review_router.verify_review_token] = fake_verify

    with TestClient(app) as client:
        r = client.get("/api/v1/review/queries/123")

    assert r.status_code == 200, r.text
    body = r.json()
    # Aggregate status replaced with personal label
    assert body["review_status"] == "pending"
    # Peer evaluations not exposed
    assert body["all_evaluations"] == []


def test_csv_export_includes_seventh_checklist_key_and_failed_checks():
    from app.routers.admin import _write_evaluations_csv

    rows = [
        {
            "id": 1,
            "query_log_id": 100,
            "query_text": "q",
            "reviewer_username": "alice",
            "checklist": {**_all_true_checklist(), "factually_accurate": False},
            "note": "",
            "review_status": "reviewed",
            "evaluation_date": datetime(2026, 5, 15, tzinfo=timezone.utc),
        },
    ]
    csv_text = _write_evaluations_csv(rows)
    header_line = csv_text.splitlines()[0]
    # All seven checklist keys must be present in the header
    for key in EvaluationChecklist.model_fields:
        assert key in header_line, f"CSV header missing checklist column: {key}"
    assert "failed_checks" in header_line

    # The failing key shows up in the failed_checks cell
    data_line = csv_text.splitlines()[1]
    assert "Factually accurate" in data_line
