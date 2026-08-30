"""Live acceptance tests against the real Gemini API and the real PDF.

Skipped entirely unless RUN_LIVE_TESTS=1 is set (not gated on API-key
presence alone - a key can exist without the user wanting a test run to
spend real quota), so the rest of the suite stays runnable without
touching the network. These are the acceptance tests from the Phase 2/3
spec - see design/DESIGN.md "Stream-order rebuild".

Each of these makes at most one real API call per page per pytest session
(the Gemini response cache means a second run of the same page, prompt, and
model is free - see hindu_extract.gemini_client).
"""
from __future__ import annotations

import json
import os

import pytest
from dotenv import load_dotenv

from hindu_extract.articles_pipeline import process_page_articles
from hindu_extract.storage import bronze_page_dir

load_dotenv()

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live Phase 2/3 tests against the real API",
)


def _line_text_by_no(config, edition, date, page_num):
    page_data = json.loads(
        (bronze_page_dir(config, edition, date, page_num) / "page.json").read_text(encoding="utf-8")
    )
    return {d["line_no"]: d["text"] for d in page_data["lines"]}


def test_page1_articles(config):
    outcome = process_page_articles(config, "delhi", "2025-09-13", 1)

    assert outcome.validation_ok, "boundary validation failed on page 1"

    line_text = _line_text_by_no(config, "delhi", "2025-09-13", 1)

    # The KMCH and Drishti IAS ads on page 1 are image/vector art with no
    # extractable text layer at all (verified: zero matching lines exist
    # in Phase 1's output, not just zero in excluded_line_nos) - there is
    # genuinely nothing for Phase 2 to exclude here. This is NOT a general
    # guarantee that ad text is always absent - a future edition's ad with
    # a real text layer would need to actually be excluded, which this
    # page can't test.
    all_text = list(line_text.values())
    assert not any("KMCH" in t or "Kovai Medical" in t for t in all_text)
    assert not any("Drishti" in t for t in all_text)

    headlines = [a.headline for a in outcome.articles]
    nepal_articles = [a for a in outcome.articles if "Karki" in a.headline or "Nepal" in a.headline]
    inflation_articles = [a for a in outcome.articles if "inflation" in a.headline.lower() or "Retail" in a.headline]

    print(f"\n--- page 1: {len(outcome.articles)} articles found ---")
    for a in outcome.articles:
        print(f"  [{a.confidence}] {a.headline!r} truncated={a.is_truncated} continues_on={a.continues_on_page}")
    print(f"headlines: {headlines}")

    assert nepal_articles, "expected a Nepal/Karki article on page 1"
    assert inflation_articles, "expected a retail-inflation article on page 1"

    nepal = nepal_articles[0]
    assert "meetings" in nepal.body.lower()
    assert "meetNings" not in nepal.body
    assert "meetNings" not in nepal.body_raw

    # drop-cap N must be the first character of the body, not mid-word
    assert nepal.body.lstrip()[0] == "N"
    assert not nepal.body.lstrip().startswith("N ")  # not floating alone with a space after it

    for a in outcome.articles:
        assert a.is_truncated, f"expected is_truncated=true on page 1, got False for {a.headline!r}"
        assert a.continues_on_page == 8, f"expected continues_on_page=8, got {a.continues_on_page}"


def test_page5_full_page_ad_produces_no_articles(config):
    outcome = process_page_articles(config, "delhi", "2025-09-13", 5)
    assert outcome.validation_ok
    assert len(outcome.articles) == 0
    assert len(outcome.excluded_line_nos) > 0


def test_page11_stock_ticker_excluded_and_verbatim_in_bronze(config):
    outcome = process_page_articles(config, "delhi", "2025-09-13", 11)
    assert outcome.validation_ok

    line_text = _line_text_by_no(config, "delhi", "2025-09-13", 11)
    excluded = set(outcome.excluded_line_nos)

    ticker_line_nos = [n for n, text in line_text.items() if text.count("d") >= 5]
    assert ticker_line_nos, "expected to find the stock-ticker leader-dot lines on page 11"
    for n in ticker_line_nos:
        assert n in excluded, f"ticker line L{n} must be excluded, not in an article"

    for article in outcome.articles:
        for n in article.line_nos:
            assert n not in ticker_line_nos, f"ticker line L{n} leaked into article {article.article_id}"

    # verbatim check against the Phase 1 bronze layer directly (not just the gold layer)
    page_data = json.loads(
        (bronze_page_dir(config, "delhi", "2025-09-13", 11) / "page.json").read_text(encoding="utf-8")
    )
    d_lines = [l for l in page_data["lines"] if l["text"].count("d") >= 5]
    assert d_lines, "expected literal 'd' leader-dot lines preserved verbatim in the bronze layer"


def test_all_pages_validation_passes(config, all_page_nums):
    failures = []
    for page_num in all_page_nums:
        outcome = process_page_articles(config, "delhi", "2025-09-13", page_num)
        if not outcome.validation_ok:
            failures.append(page_num)
    assert not failures, f"boundary validation failed on pages: {failures}"
