"""Backend API tests. The endpoint test that requires a completed Phase 2
run (a full 18-page live job) is gated on RUN_LIVE_TESTS=1 like
test_articles_live.py - not on API-key presence alone, since a key can
exist without the user wanting a test run to spend real quota - and
deliberately targets the same real docs/Newspaper.pdf edition rather than a
synthetic fixture, per the spec: "each endpoint against the real ingested
edition."
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from hindu_extract.api.main import app

load_dotenv()

client = TestClient(app)

PDF_PATH = "docs/Newspaper.pdf"
EDITION = "delhi"
DATE = "2025-09-13"


@pytest.mark.skipif(
    not Path(PDF_PATH).exists(),
    reason=f"{PDF_PATH} not found - this repo doesn't include the source PDF (copyrighted "
    f"newspaper content); supply your own licensed copy to run this test, see README.md",
)
def test_parse_metadata_reads_real_masthead():
    with open(PDF_PATH, "rb") as f:
        r = client.post(
            "/api/editions/parse-metadata", files={"file": ("Newspaper.pdf", f, "application/pdf")}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["edition"] == "delhi"
    assert body["date"] == "2025-09-13"


def test_missing_job_returns_404():
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404


def test_missing_edition_returns_404():
    r = client.get("/api/editions/nonexistent__2000-01-01")
    assert r.status_code == 404


def test_malformed_edition_id_returns_400():
    r = client.get("/api/editions/malformed-no-separator")
    assert r.status_code == 400


def test_list_editions_returns_well_formed_list():
    r = client.get("/api/editions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_ranking_returns_404_before_it_has_ever_been_computed():
    r = client.get("/api/editions/nonexistent__2000-01-01/ranking")
    assert r.status_code == 404


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run the live upload/job test (a full real Phase 2 run)",
)
@pytest.mark.skipif(
    not Path(PDF_PATH).exists(),
    reason=f"{PDF_PATH} not found - supply your own licensed copy to run this test, see README.md",
)
def test_full_upload_job_completes_and_edition_is_listed():
    with open(PDF_PATH, "rb") as f:
        r = client.post(
            "/api/editions",
            params={"edition": EDITION, "date": DATE},
            files={"file": ("Newspaper.pdf", f, "application/pdf")},
        )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    deadline = time.time() + 600
    status = None
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        status = data["status"]
        if status in ("done", "failed"):
            break
        time.sleep(2)

    assert status == "done", f"job did not complete cleanly: {data}"
    assert data["pages_done"] == data["pages_total"]
    assert any(p["articles_found"] and p["articles_found"] > 0 for p in data["per_page"])

    edition_id = f"{EDITION}__{DATE}"
    r = client.get(f"/api/editions/{edition_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["page_count"] == data["pages_total"]
    assert detail["article_count"] > 0

    r = client.get(f"/api/editions/{edition_id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"

    # Step C: page + article endpoints against this same real gold JSON.
    page_with_articles = next(p["page_num"] for p in data["per_page"] if p["articles_found"])
    r = client.get(f"/api/editions/{edition_id}/pages/{page_with_articles}")
    assert r.status_code == 200
    page_out = r.json()
    assert page_out["page_num"] == page_with_articles
    assert page_out["width"] > 0 and page_out["height"] > 0
    assert page_out["article_count"] > 0

    r = client.get(f"/api/editions/{edition_id}/pages/{page_with_articles}/articles")
    assert r.status_code == 200
    articles = r.json()
    assert len(articles) == page_out["article_count"]
    article = articles[0]
    assert isinstance(article["deck"], list)
    assert isinstance(article["captions"], list)
    assert isinstance(article["rects"], list)
    assert article["headline"] or article["body"]

    zero_article_page = next((p["page_num"] for p in data["per_page"] if p["articles_found"] == 0), None)
    if zero_article_page is not None:
        r = client.get(f"/api/editions/{edition_id}/pages/{zero_article_page}/articles")
        assert r.status_code == 200
        assert r.json() == []

    r = client.get(f"/api/editions/{edition_id}/pages/9999")
    assert r.status_code == 404

    # Step D: monitoring endpoints against this same real run.
    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()
    assert any(run["edition"] == EDITION and run["date"] == DATE for run in runs)
    run_id = next(run["run_id"] for run in runs if run["edition"] == EDITION and run["date"] == DATE)

    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    run_detail = r.json()
    assert run_detail["status"] == "done"
    assert run_detail["page_count"] == data["pages_total"]
    assert run_detail["total_tokens"] and run_detail["total_tokens"] > 0
    assert page_with_articles in run_detail["pages"]

    r = client.get(f"/api/runs/{run_id}/pages/{page_with_articles}")
    assert r.status_code == 200
    stage_events = r.json()
    stage_names = {e["stage"] for e in stage_events}
    assert {"char_extraction", "line_building", "ligature_canary", "gemini_call", "validation", "assembly", "render"} <= stage_names

    r = client.get(f"/api/runs/{run_id}/pages/{page_with_articles}/raw")
    assert r.status_code == 200
    raw = r.json()
    assert raw["prompt"]
    assert raw["raw_response"]

    r = client.get("/api/quota")
    assert r.status_code == 200
    quota = r.json()
    assert quota["requests_today"] > 0
    assert quota["requests_per_day_limit"] == 500
    assert quota["tokens_per_minute_limit"] == 250_000

    # Summaries: edition-wide ranking, triggered against this same real edition.
    r = client.get(f"/api/editions/{edition_id}/ranking")
    assert r.status_code == 404  # not computed yet

    r = client.post(f"/api/editions/{edition_id}/ranking")
    assert r.status_code == 200
    ranking = r.json()
    assert 0 < len(ranking["ranked"]) <= 20
    assert all(1 <= a["rank"] <= 20 for a in ranking["ranked"])
    assert all(0 <= a["importance_score"] <= 100 for a in ranking["ranked"])
    valid_categories = {
        "POLITY_GOVERNANCE", "ECONOMY", "INTERNATIONAL", "ENVIRONMENT", "SCIENCE_TECH",
        "SOCIAL_ISSUES", "JUDICIARY", "SECURITY_DEFENCE", "AGRICULTURE", "HEALTH",
        "EDUCATION", "OTHER",
    }
    assert all(a["category"] in valid_categories for a in ranking["ranked"])
    assert len({a["article_id"] for a in ranking["ranked"]}) == len(ranking["ranked"])  # no duplicate ids

    r = client.get(f"/api/editions/{edition_id}/ranking")
    assert r.status_code == 200
    assert r.json()["ranked"] == ranking["ranked"]
