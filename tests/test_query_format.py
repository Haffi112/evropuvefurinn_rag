"""Unit tests for the `format` (output_format) behaviour of RAGService.

The public `/api/v1/query` endpoint defaults to the Vísindavefur publish
format. These tests pin the wrapper that converts the Markdown answer to VV
without touching the LLM/DB/embedding layers: `_process_query_json` is stubbed
to return a known `QueryResponse`, and we assert how the public
`process_query_json` wrapper renders it.

`asyncio.run` is used directly so the tests don't depend on pytest-asyncio's
mode configuration.
"""
import asyncio

from app.models.schemas import QueryResponse, Reference
from app.services.rag_service import RAGService


def _service() -> RAGService:
    # __init__ only stores its args; the wrapper under test never touches them.
    return RAGService(settings=None, embeddings=None, llm=None)


def _markdown_response() -> QueryResponse:
    return QueryResponse(
        query="Hvað er ESB?",
        answer="Evrópusambandið er samband ríkja[[1]](https://evropuvefur.is/a).",
        references=[
            Reference(
                number=1,
                id="a",
                title="Hvað er ESB?",
                source_url="https://evropuvefur.is/a",
                date="2025-01-01",
                relevance_score=0.91,
            )
        ],
        model_used="google/gemini-3-flash-preview",
        cached=False,
        query_id="q_test",
        query_log_id=None,
    )


def _run_with_stub(output_format: str, *, web_search: bool = False) -> QueryResponse:
    svc = _service()

    async def fake_impl(*args, **kwargs):
        return _markdown_response()

    svc._process_query_json = fake_impl  # type: ignore[assignment]
    return asyncio.run(
        svc.process_query_json(
            "Hvað er ESB?", 5, "is",
            web_search=web_search, output_format=output_format,
        )
    )


def test_vv_converts_answer_to_visindavefur_format():
    resp = _run_with_stub("vv")
    # The [[N]](url) markdown citation becomes a VV footnote template ...
    assert "{{footnote" in resp.answer
    assert "{{footnote_list|}}" in resp.answer
    # No middle-layer "Heimildir" list — the CMS shows sources as "tengd svör".
    assert "Heimildir" not in resp.answer
    # ... and the raw markdown citation marker is gone.
    assert "[[1]]" not in resp.answer
    # References themselves are untouched — only the answer string is rendered.
    assert resp.references[0].source_url == "https://evropuvefur.is/a"


def test_markdown_leaves_answer_untouched():
    resp = _run_with_stub("markdown")
    assert resp.answer == (
        "Evrópusambandið er samband ríkja[[1]](https://evropuvefur.is/a)."
    )
    assert "{{footnote" not in resp.answer


def test_vv_skipped_for_web_search():
    # Web-search answers carry sources inline with empty structured refs; VV's
    # reconstruction would drop them, so the wrapper must leave them as Markdown
    # even when output_format == "vv".
    resp = _run_with_stub("vv", web_search=True)
    assert resp.answer == (
        "Evrópusambandið er samband ríkja[[1]](https://evropuvefur.is/a)."
    )
