"""Offline tests for edition-wide importance ranking (ranking.py) - no
network access. Gemini calls are monkeypatched at the module-private
_call_gemini_for_ranking seam so retry/validation logic can be exercised
without touching the API or the response cache."""
from __future__ import annotations

import json

import pytest

from hindu_extract import ranking
from hindu_extract.config import load_config


@pytest.fixture
def config(tmp_path):
    cfg = load_config()
    gold_root = tmp_path / "gold"
    return cfg.__class__(
        **{
            **cfg.__dict__,
            "paths": cfg.paths.__class__(**{**cfg.paths.__dict__, "gold_root": gold_root}),
        }
    )


def _write_gold_page(config, edition, date, page_num, articles):
    page_dir = config.gold_root / edition / date / f"page_{page_num:02d}"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "articles.json").write_text(
        json.dumps({"page_num": page_num, "articles": articles}), encoding="utf-8"
    )


def _article(article_id, headline="A headline", deck=None, body="word " * 150, continues_on_page=None):
    return {
        "article_id": article_id,
        "headline": headline,
        "deck": deck or ["a deck line"],
        "body": body,
        "continues_on_page": continues_on_page,
    }


def test_build_corpus_assigns_globally_unique_composite_ids_and_truncates_preview(config):
    _write_gold_page(config, "delhi", "2025-09-13", 1, [_article("1"), _article("2")])
    _write_gold_page(config, "delhi", "2025-09-13", 8, [_article("1")])  # same original id, different page

    corpus = ranking.build_corpus(config, "delhi", "2025-09-13")

    ids = {c.article_id for c in corpus}
    assert ids == {"p01-1", "p01-2", "p08-1"}
    # body_preview_words defaults from config/default.yaml (100)
    assert len(corpus[0].body_preview.split()) == config.ranking.body_preview_words


def test_build_corpus_skips_pages_with_no_gold_json(config):
    _write_gold_page(config, "delhi", "2025-09-13", 1, [_article("1")])
    (config.gold_root / "delhi" / "2025-09-13" / "page_05").mkdir(parents=True)  # no articles.json
    corpus = ranking.build_corpus(config, "delhi", "2025-09-13")
    assert len(corpus) == 1


CORPUS_BY_ID = {
    "p01-1": ranking.CandidateArticle("p01-1", 1, "Karki is Nepal's first woman PM", "deck", 8, "preview"),
    # A real continuation gets its own "jump head" repeating the story's
    # headline (verified live: page 8's jump head for this exact story is
    # "Karki is Nepal's first woman Prime Minister") - not an empty
    # headline, which is why the duplicate check keys off word overlap
    # rather than "headline is blank".
    "p08-1": ranking.CandidateArticle("p08-1", 8, "Karki is Nepal's first woman Prime Minister", "", None, "continuation text"),
    "p08-4": ranking.CandidateArticle("p08-4", 8, "Centre to hold meeting on special courts", "deck", None, "preview"),
    "p02-1": ranking.CandidateArticle("p02-1", 2, "Budget announced", "deck", None, "preview"),
    "p03-1": ranking.CandidateArticle("p03-1", 3, "Court ruling", "deck", None, "preview"),
}


def _valid_entry(article_id, rank, score, category="POLITY_GOVERNANCE"):
    return {
        "article_id": article_id,
        "rank": rank,
        "importance_score": score,
        "category": category,
        "why_it_matters": "It matters for real reasons.",
        "exclusion_risk": "none",
    }


def test_validate_passes_for_well_formed_response():
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p02-1", 2, 80)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert result.ok
    assert not result.issues
    assert not result.duplicate_continuations


def test_validate_flags_unknown_article_id():
    parsed = {"ranked": [_valid_entry("does-not-exist", 1, 90)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("does not exist" in i for i in result.issues)


def test_validate_flags_duplicate_article_id():
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p01-1", 2, 80)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("duplicate article_id" in i for i in result.issues)


def test_validate_flags_invalid_category():
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90, category="POLITICS")]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("not in the fixed enum" in i for i in result.issues)


def test_validate_flags_invalid_exclusion_risk():
    entry = _valid_entry("p01-1", 1, 90)
    entry["exclusion_risk"] = "maybe"
    result = ranking._validate({"ranked": [entry]}, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("exclusion_risk" in i for i in result.issues)


def test_validate_flags_score_out_of_range():
    result = ranking._validate({"ranked": [_valid_entry("p01-1", 1, 150)]}, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("importance_score" in i for i in result.issues)


def test_validate_flags_rank_score_inconsistency():
    # rank 2 scores higher than rank 1 - inconsistent
    parsed = {"ranked": [_valid_entry("p01-1", 1, 50), _valid_entry("p02-1", 2, 90)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("scores higher than a lower rank" in i for i in result.issues)


def test_validate_flags_too_many_entries():
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p02-1", 2, 80), _valid_entry("p03-1", 3, 70)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=2)
    assert not result.ok
    assert any("expected at most 2" in i for i in result.issues)


def _excluded_entry(article_id, reason_code="OPINION_WITHOUT_ANALYSIS", note="Asserts a view without analysis."):
    return {"article_id": article_id, "reason_code": reason_code, "note": note}


def test_validate_passes_with_a_well_formed_excluded_list():
    parsed = {
        "ranked": [_valid_entry("p01-1", 1, 90)],
        "excluded": [_excluded_entry("p02-1")],
    }
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert result.ok


def test_validate_flags_unknown_excluded_article_id():
    parsed = {"ranked": [], "excluded": [_excluded_entry("does-not-exist")]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("excluded article_id" in i and "does not exist" in i for i in result.issues)


def test_validate_flags_invalid_excluded_reason_code():
    parsed = {"ranked": [], "excluded": [_excluded_entry("p02-1", reason_code="BECAUSE_I_SAID_SO")]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("reason_code" in i and "not in the fixed enum" in i for i in result.issues)


def test_validate_flags_article_in_both_ranked_and_excluded():
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90)], "excluded": [_excluded_entry("p01-1")]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert any("both ranked and excluded" in i for i in result.issues)


def test_validate_flags_too_many_excluded_entries():
    from hindu_extract.ranking_prompt import MAX_EXCLUDED_ENTRIES

    big_corpus = {
        f"p99-{i}": ranking.CandidateArticle(f"p99-{i}", 99, f"Headline {i}", "deck", None, "preview")
        for i in range(MAX_EXCLUDED_ENTRIES + 1)
    }
    parsed = {"ranked": [], "excluded": [_excluded_entry(aid) for aid in big_corpus]}
    result = ranking._validate(parsed, big_corpus, top_n=20)
    assert not result.ok
    assert any(f"expected at most {MAX_EXCLUDED_ENTRIES}" in i for i in result.issues)


def test_to_excluded_articles_fills_page_and_headline_from_corpus():
    parsed = {"excluded": [_excluded_entry("p02-1", note="A real reason.")]}
    out = ranking._to_excluded_articles(parsed, CORPUS_BY_ID)
    assert len(out) == 1
    assert out[0].page == 2
    assert out[0].headline == "Budget announced"
    assert out[0].note == "A real reason."


def test_validate_detects_duplicate_continuation_via_cross_reference():
    # p01-1 continues_on_page=8, and p08-1 (on page 8) is also ranked - a
    # real cross-reference against the corpus, not trusting self-report.
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p08-1", 2, 80)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert not result.ok
    assert len(result.duplicate_continuations) == 1
    dup = result.duplicate_continuations[0]
    assert dup.first_part_id == "p01-1"
    assert dup.conflicting_id == "p08-1"


def test_validate_ok_when_continuation_not_ranked():
    # p01-1 continues on page 8, but nothing on page 8 is actually ranked -
    # no duplicate.
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p03-1", 2, 80)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert result.ok
    assert not result.duplicate_continuations


def test_validate_does_not_flag_an_unrelated_article_that_merely_shares_the_target_page():
    # Verified live: page 8 legitimately has 10 unrelated articles, and two
    # of them (page number only, no headline relation) were false-positived
    # by an earlier version of this check that keyed on page number alone.
    # p08-4 is NOT the continuation of p01-1 - just another real story that
    # happens to also be on page 8.
    parsed = {"ranked": [_valid_entry("p01-1", 1, 90), _valid_entry("p08-4", 2, 80)]}
    result = ranking._validate(parsed, CORPUS_BY_ID, top_n=20)
    assert result.ok
    assert not result.duplicate_continuations


def test_to_ranked_articles_sorts_by_rank_and_fills_from_corpus():
    parsed = {"ranked": [_valid_entry("p02-1", 2, 80), _valid_entry("p01-1", 1, 90)]}
    out = ranking._to_ranked_articles(parsed, CORPUS_BY_ID)
    assert [r.article_id for r in out] == ["p01-1", "p02-1"]
    assert out[0].page == 1
    assert out[0].headline == "Karki is Nepal's first woman PM"


def test_rank_edition_retries_once_with_corrective_instruction_on_validation_failure(config, monkeypatch):
    _write_gold_page(config, "delhi", "2025-09-13", 1, [_article("1")])
    monkeypatch.setattr(ranking, "build_corpus", lambda cfg, e, d: list(CORPUS_BY_ID.values()))

    calls = []

    def fake_call(corpus, cfg, use_cache, tracer, extra_instruction=None):
        calls.append(extra_instruction)
        if len(calls) == 1:
            # first attempt: invalid category
            return {"ranked": [_valid_entry("p01-1", 1, 90, category="POLITICS")]}, {"cache_hit": False, "total_token_count": 100}
        return {"ranked": [_valid_entry("p01-1", 1, 90)]}, {"cache_hit": False, "total_token_count": 100}

    monkeypatch.setattr(ranking, "_call_gemini_for_ranking", fake_call)

    outcome = ranking.rank_edition(config, "delhi", "2025-09-13")

    assert len(calls) == 2
    assert calls[0] is None  # first call has no corrective instruction
    assert calls[1] is not None and "POLITICS" not in calls[1] or "problems" in calls[1]  # second call carries correction
    assert outcome.retried is True
    assert outcome.validation.ok is True
    assert outcome.ranked[0].article_id == "p01-1"


def test_rank_edition_reports_fewer_than_top_n_without_treating_it_as_failure(config, monkeypatch):
    monkeypatch.setattr(ranking, "build_corpus", lambda cfg, e, d: list(CORPUS_BY_ID.values()))
    monkeypatch.setattr(
        ranking,
        "_call_gemini_for_ranking",
        lambda corpus, cfg, use_cache, tracer, extra_instruction=None: (
            {"ranked": [_valid_entry("p01-1", 1, 90)]},
            {"cache_hit": False, "total_token_count": 50},
        ),
    )
    outcome = ranking.rank_edition(config, "delhi", "2025-09-13")
    assert outcome.validation.ok
    assert outcome.eligible_count_note is not None
    assert "1" in outcome.eligible_count_note


def test_process_edition_ranking_persists_and_read_ranking_round_trips(config, monkeypatch):
    monkeypatch.setattr(ranking, "build_corpus", lambda cfg, e, d: list(CORPUS_BY_ID.values()))
    monkeypatch.setattr(
        ranking,
        "_call_gemini_for_ranking",
        lambda corpus, cfg, use_cache, tracer, extra_instruction=None: (
            {"ranked": [_valid_entry("p01-1", 1, 90)], "excluded": [_excluded_entry("p02-1")]},
            {"cache_hit": True, "total_token_count": 42},
        ),
    )

    assert ranking.read_ranking(config, "delhi", "2025-09-13") is None

    ranking.process_edition_ranking(config, "delhi", "2025-09-13")

    result = ranking.read_ranking(config, "delhi", "2025-09-13")
    assert result is not None
    assert result["validation_ok"] is True
    assert result["ranked"][0]["article_id"] == "p01-1"
    assert result["excluded"][0]["article_id"] == "p02-1"
    assert result["excluded"][0]["page"] == 2
    assert result["all_cached"] is True
    assert result["total_tokens"] == 42
