"""Golden tests for the Vísindavefur export converter.

These tests pin down the exact output shape so the CMS-side renderer does
not break when the converter is changed. Each test exercises one rule in
isolation; the final ``test_full_example`` test combines several rules to
catch interaction bugs.
"""

from app.services.visindavefur_export import to_vv_html


REF_EU = {
    "title": "EFTA-dómstóllinn",
    "source_url": "https://evropuvefur.is/eftadomur",
}
REF_NYT = {
    "title": "What to Know About the Hantavirus Outbreak on a Cruise Ship",
    "source_url": "https://www.nytimes.com/article/hantavirus.html",
}


def test_empty_input_returns_empty():
    assert to_vv_html("", []) == "\n"
    assert to_vv_html("", None) == "\n"


def test_heading_becomes_strong():
    out = to_vv_html("### Stutta svarið", [])
    assert "<strong>Stutta svarið</strong>" in out
    # No literal `###` should remain.
    assert "###" not in out


def test_all_heading_levels_collapse_to_strong():
    out = to_vv_html("# H1\n\n## H2\n\n### H3", [])
    assert out.count("<strong>") == 3
    assert "<strong>H1</strong>" in out
    assert "<strong>H2</strong>" in out
    assert "<strong>H3</strong>" in out


def test_bold_becomes_b_tag():
    out = to_vv_html("Þetta er **feitletrað** orð.", [])
    assert "<b>feitletrað</b>" in out
    assert "**" not in out


def test_italic_becomes_em_tag():
    # Use Icelandic prose so the underscore-in-identifier guard doesn't trip.
    out = to_vv_html("Eins og *Guðmundur* sagði.", [])
    assert "<em>Guðmundur</em>" in out


def test_italic_underscore_form():
    out = to_vv_html("Eins og _Guðmundur_ sagði.", [])
    assert "<em>Guðmundur</em>" in out


def test_unordered_list():
    md = "Listi:\n- hér\n- og hér"
    out = to_vv_html(md, [])
    assert "<ul>" in out and "</ul>" in out
    assert "<li>hér</li>" in out
    assert "<li>og hér</li>" in out


def test_ordered_list_has_no_blank_lines_between_items():
    md = "1. hér\n2. og hér"
    out = to_vv_html(md, [])
    # The whole <ol>…</ol> block should be on a single line — no newline
    # between </li> and <li> (CMS rendering quirk).
    assert "<ol><li>hér</li><li>og hér</li></ol>" in out


def test_citation_becomes_footnote_with_period_moved_before():
    md = "Þetta er staðreynd[[1]](https://evropuvefur.is/eftadomur)."
    out = to_vv_html(md, [REF_EU])
    # Period moves to BEFORE the footnote.
    assert (
        "Þetta er staðreynd.{{footnote|text=EFTA-dómstóllinn — "
        "https://evropuvefur.is/eftadomur}}"
    ) in out


def test_citation_without_trailing_period():
    md = "Sjá[[1]](https://evropuvefur.is/eftadomur) hér."
    out = to_vv_html(md, [REF_EU])
    assert "{{footnote|text=EFTA-dómstóllinn — https://evropuvefur.is/eftadomur}}" in out
    # Nothing inserted where there was no period.
    assert "Sjá{{footnote" in out


def test_multi_citation_emits_two_footnotes():
    md = "Tvær heimildir[[1]](a)[[2]](b)."
    out = to_vv_html(
        md,
        [
            {"title": "First", "source_url": "https://a.example"},
            {"title": "Second", "source_url": "https://b.example"},
        ],
    )
    # Both footnotes present; the trailing period sits before the first one.
    assert "Tvær heimildir.{{footnote|text=First — https://a.example}}" in out
    assert "{{footnote|text=Second — https://b.example}}" in out


def test_citation_with_unknown_number_falls_back_to_url():
    md = "Vísun[[7]](https://example.com/x)."
    out = to_vv_html(md, [REF_EU])
    # Number 7 has no matching reference — fall back to the URL.
    assert "{{footnote|text=https://example.com/x}}" in out


def test_trailing_heimildir_section_is_stripped_from_body():
    md = "Svar hér.\n\n## Heimildir\n\n- [1] foo — https://x"
    out = to_vv_html(md, [REF_EU])
    # The literal `## Heimildir` line and the bullet beneath it must be gone
    # from the body — we reconstruct the Heimildir section from `references`.
    assert "## Heimildir" not in out
    assert "[1] foo" not in out


def test_trailing_references_section_english_is_stripped():
    md = "Answer text.\n\n## References\n\n- [1] foo"
    out = to_vv_html(md, [REF_EU])
    assert "## References" not in out
    assert "[1] foo" not in out


def test_heimildir_block_appended_when_refs_present():
    out = to_vv_html("Svar.", [REF_EU])
    assert "{{footnote_list|}}" in out
    assert "<strong>Heimildir:</strong>" in out
    assert 'href="https://evropuvefur.is/eftadomur"' in out
    # The Heimildir block should come AFTER the footnote_list marker.
    assert out.index("{{footnote_list|}}") < out.index("<strong>Heimildir:</strong>")


def test_no_heimildir_block_when_refs_empty():
    out = to_vv_html("Svar án heimilda.", [])
    assert "{{footnote_list|}}" not in out
    assert "Heimildir" not in out


def test_html_in_body_is_escaped():
    # If the LLM emits a stray tag, it must be escaped, not pass through.
    out = to_vv_html("Þetta er <script>alert(1)</script>.", [])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_footnote_text_with_pipe_is_sanitized():
    # `|` is the template delimiter — if a title contains it, it must be
    # replaced so the CMS doesn't mis-parse the footnote.
    ref = {"title": "Title | with pipe", "source_url": "https://x"}
    out = to_vv_html("Vísun[[1]](https://x).", [ref])
    assert "{{footnote|text=Title / with pipe — https://x}}" in out


def test_single_export_endpoint_uses_reviewed_article_when_present(monkeypatch):
    """The per-query export endpoint should prefer reviewed_articles.edited_response
    over query_log.response_text when one exists, and render with refs from
    query_log."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import admin as admin_router

    async def fake_query_detail(query_log_id: int):
        return {
            "id": query_log_id,
            "query_text": "Hvað er EFTA?",
            "response_text": "raw LLM output (should be ignored)",
            "references": [REF_EU],
        }

    async def fake_latest_reviewed(query_log_id: int):
        return {
            "title": "Hvað er EFTA-dómstóllinn?",
            "edited_response": (
                "### Stutta svarið\n\n"
                "EFTA-dómstóllinn dæmir EES-mál[[1]](https://evropuvefur.is/eftadomur)."
            ),
        }

    monkeypatch.setattr(admin_router.db, "get_query_log_detail", fake_query_detail)
    monkeypatch.setattr(admin_router.db, "get_latest_reviewed_article", fake_latest_reviewed)

    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[admin_router.verify_api_key] = lambda: "ok"

    with TestClient(app) as client:
        r = client.get("/api/v1/admin/reviews/42/export/visindavefur")

    assert r.status_code == 200, r.text
    body = r.text
    # Editor's body was used (not raw response_text).
    assert "EFTA-dómstóllinn dæmir EES-mál" in body
    assert "raw LLM output" not in body
    # VV markers present.
    assert "<strong>Stutta svarið</strong>" in body
    assert "{{footnote|text=EFTA-dómstóllinn — https://evropuvefur.is/eftadomur}}" in body
    assert "{{footnote_list|}}" in body
    assert "<strong>Heimildir:</strong>" in body
    # Filename slug includes query_log_id and ASCII-folds Icelandic letters
    # for the Content-Disposition header.
    cd = r.headers.get("content-disposition", "")
    assert "42_hvad-er-efta-domstollinn" in cd
    assert cd.endswith('.html"')


def test_single_export_falls_back_to_raw_response(monkeypatch):
    """If no reviewed_articles row exists, the endpoint should render the
    raw query_log.response_text so editors can preview before reviewing."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import admin as admin_router

    async def fake_query_detail(query_log_id: int):
        return {
            "id": query_log_id,
            "query_text": "qtext",
            "response_text": "Svar með heimild[[1]](https://evropuvefur.is/eftadomur).",
            "references": [REF_EU],
        }

    async def fake_latest_reviewed(query_log_id: int):
        return None

    monkeypatch.setattr(admin_router.db, "get_query_log_detail", fake_query_detail)
    monkeypatch.setattr(admin_router.db, "get_latest_reviewed_article", fake_latest_reviewed)

    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[admin_router.verify_api_key] = lambda: "ok"

    with TestClient(app) as client:
        r = client.get("/api/v1/admin/reviews/7/export/visindavefur")

    assert r.status_code == 200
    assert "Svar með heimild.{{footnote|text=EFTA-dómstóllinn" in r.text


def test_single_export_404_when_query_log_missing(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import admin as admin_router

    async def fake_query_detail(query_log_id: int):
        return None

    monkeypatch.setattr(admin_router.db, "get_query_log_detail", fake_query_detail)
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[admin_router.verify_api_key] = lambda: "ok"
    with TestClient(app) as client:
        r = client.get("/api/v1/admin/reviews/999/export/visindavefur")
    assert r.status_code == 404


def test_bulk_export_returns_zip_with_html_files(monkeypatch):
    """Bulk endpoint produces a ZIP of .html files, one per reviewed article."""
    import zipfile
    import io as _io
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import admin as admin_router

    async def fake_get_all():
        return [
            {
                "query_log_id": 1,
                "title": "First answer",
                "edited_response": "Body 1[[1]](https://a.example).",
                "references": [{"title": "A", "source_url": "https://a.example"}],
            },
            {
                "query_log_id": 2,
                "title": "Second",
                "edited_response": "Body 2.",
                "references": [],
            },
        ]

    monkeypatch.setattr(admin_router.db, "get_all_reviewed_articles_latest", fake_get_all)
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[admin_router.verify_api_key] = lambda: "ok"

    with TestClient(app) as client:
        r = client.get("/api/v1/admin/reviews/export/visindavefur")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(_io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert any(n.startswith("1_") and n.endswith(".html") for n in names)
        assert any(n.startswith("2_") and n.endswith(".html") for n in names)
        first = zf.read(next(n for n in names if n.startswith("1_"))).decode("utf-8")
        assert "{{footnote|text=A — https://a.example}}" in first
        assert "<strong>Heimildir:</strong>" in first


def test_full_example_round_trip():
    md = (
        "### Stutta svarið\n"
        "\n"
        "Þetta er bara stutta svarið.\n"
        "\n"
        "### Lengra svar\n"
        "\n"
        "Við notum oft ótölusettan lista:\n"
        "- hér\n"
        "- og hér\n"
        "\n"
        "Og eins og *Guðmundur Daði* sagði eru tilvísanir svona[[1]](https://evropuvefur.is/eftadomur).\n"
        "\n"
        "Tölusettur listi:\n"
        "1. hér\n"
        "2. og hér\n"
    )
    out = to_vv_html(md, [REF_EU])

    # Structural checks — order matters.
    assert out.index("<strong>Stutta svarið</strong>") < out.index(
        "<strong>Lengra svar</strong>"
    )
    assert "<ul>" in out
    assert "<ol><li>hér</li><li>og hér</li></ol>" in out
    assert "<em>Guðmundur Daði</em>" in out
    assert (
        "tilvísanir svona.{{footnote|text=EFTA-dómstóllinn — "
        "https://evropuvefur.is/eftadomur}}"
    ) in out
    assert out.endswith("</ul>\n")  # Heimildir block is the final element.
