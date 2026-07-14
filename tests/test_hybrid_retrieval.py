"""Unit tests for the hybrid-retrieval building blocks: answer chunking,
lexical tsquery construction, and reciprocal-rank fusion. Pure functions —
no DB or embedding API involved."""

from app.services.embedding_service import (
    CHUNK_OVERLAP_WORDS,
    CHUNK_WORDS,
    EmbeddingService,
)


# ── Chunking ─────────────────────────────────────────────────

def test_chunk_empty_answer():
    assert EmbeddingService._chunk_answer("") == []
    assert EmbeddingService._chunk_answer("   \n  ") == []


def test_chunk_short_answer_single_chunk():
    text = "stutt svar um Evrópusambandið"
    assert EmbeddingService._chunk_answer(text) == [text]


def test_chunk_long_answer_overlap_and_coverage():
    words = [f"orð{i}" for i in range(500)]
    chunks = EmbeddingService._chunk_answer(" ".join(words))

    assert len(chunks) > 1
    # Every chunk fits the window
    for c in chunks:
        assert len(c.split()) <= CHUNK_WORDS
    # Consecutive chunks share exactly the overlap
    first, second = chunks[0].split(), chunks[1].split()
    assert first[-CHUNK_OVERLAP_WORDS:] == second[:CHUNK_OVERLAP_WORDS]
    # No word is lost: the last word of the input ends the last chunk
    assert chunks[-1].split()[-1] == "orð499"


def test_chunk_texts_carry_title_and_question_prefix():
    texts = EmbeddingService._build_chunk_texts(
        "Titill", "Spurningin?", " ".join(f"orð{i}" for i in range(400))
    )
    assert len(texts) > 1
    for t in texts:
        assert t.startswith("Titill\nSpurningin?\n")


# ── Lexical tsquery ──────────────────────────────────────────

def test_lexical_tsquery_drops_stopwords_and_dedupes():
    q = EmbeddingService._lexical_tsquery("Er ESB með her og er her í ESB?")
    assert q == "esb | með | her"


def test_lexical_tsquery_keeps_rare_abbreviations():
    q = EmbeddingService._lexical_tsquery("Má ríkið reka átvr ef ísland er í esb?")
    assert "átvr" in q.split(" | ")
    assert "esb" in q.split(" | ")


def test_lexical_tsquery_all_stopwords_returns_none():
    assert EmbeddingService._lexical_tsquery("er og að í á") is None
    assert EmbeddingService._lexical_tsquery("") is None


def test_lexical_tsquery_caps_terms():
    text = " ".join(f"einstakt{i}" for i in range(30))
    q = EmbeddingService._lexical_tsquery(text)
    assert len(q.split(" | ")) == 12


# ── RRF fusion ───────────────────────────────────────────────

def _vec(id_, score, **extra):
    return {"id": id_, "title": f"t-{id_}", "question": "q", "source_url": "u",
            "date": "2026-01-01", "vector_score": score, **extra}


def _lex(id_, score):
    return {"id": id_, "title": f"t-{id_}", "question": "q", "source_url": "u",
            "date": "2026-01-01", "lexical_score": score}


def test_rrf_article_in_many_lists_beats_single_list_winner():
    # "b" is mid-ranked everywhere; "a" wins one list but appears nowhere else.
    lists = [
        [_vec("a", 0.9), _vec("b", 0.8)],
        [_vec("c", 0.7), _vec("b", 0.6)],
        [_lex("b", 1.5), _lex("d", 1.0)],
    ]
    fused = EmbeddingService._rrf_fuse(lists)
    assert fused[0]["id"] == "b"
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]


def test_rrf_tracks_best_vector_score_and_ranks():
    lists = [
        [_vec("a", 0.5), _vec("b", 0.4)],
        [_vec("b", 0.75)],
        [_lex("b", 2.0)],
    ]
    fused = EmbeddingService._rrf_fuse(lists)
    b = next(e for e in fused if e["id"] == "b")
    assert b["vector_score"] == 0.75  # best across vector lists
    assert b["vector_rank"] == 1      # best position in any vector list
    assert b["lexical_rank"] == 1
    a = next(e for e in fused if e["id"] == "a")
    assert a["lexical_rank"] is None  # never matched lexically
    assert a["vector_score"] == 0.5


def test_rrf_lexical_only_candidate_has_no_vector_score():
    fused = EmbeddingService._rrf_fuse([[_lex("x", 1.0)]])
    assert fused[0]["vector_score"] is None
    assert fused[0]["lexical_rank"] == 1


def test_rrf_empty_lists():
    assert EmbeddingService._rrf_fuse([]) == []
    assert EmbeddingService._rrf_fuse([[], []]) == []
