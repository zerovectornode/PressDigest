"""Offline tests for the Enhancement 1/2/3/4 additions: live per-page
status via jobs.py's in-memory registry, editions.get_page_status's
disk/job-state merge, and the /api/jobs/active reconnection endpoint. No
network access and no real extraction - job state is built directly via
jobs._JOBS / gold JSON is written directly to disk.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from hindu_extract.api import editions, jobs
from hindu_extract.api.jobs import JobRecord, PagePhase
from hindu_extract.api.main import app
from hindu_extract import storage


@pytest.fixture
def tmp_config(config, tmp_path):
    return dataclasses.replace(config, project_root=tmp_path)


@pytest.fixture(autouse=True)
def clean_jobs():
    """jobs._JOBS is a module-level dict shared across the whole test
    session (see jobs.py's module docstring on why it's in-memory) - clear
    it before and after each test here so these tests don't leak fake job
    records into test_api.py's assertions, and vice versa."""
    jobs._JOBS.clear()
    yield
    jobs._JOBS.clear()


def _write_gold(config, edition, date, page_num, article_count):
    from hindu_extract.articles_pipeline import gold_edition_dir

    gold_dir = gold_edition_dir(config, edition, date) / f"page_{page_num:02d}"
    gold_dir.mkdir(parents=True, exist_ok=True)
    (gold_dir / "articles.json").write_text(
        json.dumps({"articles": [{}] * article_count, "validation_ok": True}), encoding="utf-8"
    )


def test_get_active_job_for_edition_finds_running_job():
    jobs._JOBS["j1"] = JobRecord(job_id="j1", edition="delhi", date="2025-09-13", status="running")
    found = jobs.get_active_job_for_edition("delhi", "2025-09-13")
    assert found is not None
    assert found.job_id == "j1"


def test_get_active_job_for_edition_ignores_done_jobs():
    jobs._JOBS["j1"] = JobRecord(job_id="j1", edition="delhi", date="2025-09-13", status="done")
    assert jobs.get_active_job_for_edition("delhi", "2025-09-13") is None


def test_list_active_jobs_returns_only_queued_and_running():
    jobs._JOBS["j1"] = JobRecord(job_id="j1", edition="delhi", date="2025-09-13", status="queued")
    jobs._JOBS["j2"] = JobRecord(job_id="j2", edition="mumbai", date="2025-09-14", status="running")
    jobs._JOBS["j3"] = JobRecord(job_id="j3", edition="chennai", date="2025-09-15", status="done")
    active_ids = {j.job_id for j in jobs.list_active_jobs()}
    assert active_ids == {"j1", "j2"}


def test_update_page_ignores_page_zero_sentinel_without_raising():
    jobs._JOBS["j1"] = JobRecord(
        job_id="j1", edition="delhi", date="2025-09-13", status="running", pages_total=1,
        per_page=[PagePhase(page_num=1)],
    )
    jobs._update_page("j1", 0, status="extracting")  # page_num=0: ranking's sentinel, not a real page
    assert jobs._JOBS["j1"].per_page[0].status == "pending"  # untouched


def test_on_stage_start_updates_current_stage_and_status():
    jobs._JOBS["j1"] = JobRecord(
        job_id="j1", edition="delhi", date="2025-09-13", status="running", pages_total=1,
        per_page=[PagePhase(page_num=1)],
    )
    jobs._on_stage_start("j1", 1, "char_extraction")
    phase = jobs._JOBS["j1"].per_page[0]
    assert phase.status == "extracting"
    assert phase.current_stage == "char_extraction"

    jobs._on_stage_start("j1", 1, "gemini_call")
    phase = jobs._JOBS["j1"].per_page[0]
    assert phase.status == "grouping"
    assert phase.current_stage == "gemini_call"


def test_get_page_status_out_of_range_with_no_job_and_no_manifest(tmp_config):
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 1) is None


def test_get_page_status_from_manifest_and_gold(tmp_config):
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 3})
    _write_gold(tmp_config, "delhi", "2025-09-13", 1, article_count=2)

    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 1) == "done"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 2) == "pending"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 3) == "pending"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 4) is None  # out of range


def test_get_page_status_prefers_live_job_over_disk(tmp_config):
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 2})
    _write_gold(tmp_config, "delhi", "2025-09-13", 1, article_count=1)  # stale gold from a prior run

    jobs._JOBS["j1"] = JobRecord(
        job_id="j1", edition="delhi", date="2025-09-13", status="running", pages_total=2,
        per_page=[PagePhase(page_num=1, status="extracting"), PagePhase(page_num=2, status="failed")],
    )

    # The active job's live state wins over the (stale) on-disk gold, and
    # a page beyond the job's own pages_total (not the manifest) is out
    # of range while that job is authoritative.
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 1) == "in_progress"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 2) == "failed"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 3) is None


def test_active_jobs_route_returns_empty_list_when_none_running():
    client = TestClient(app)
    r = client.get("/api/jobs/active")
    assert r.status_code == 200
    assert r.json() == []


def test_active_jobs_route_returns_running_job():
    jobs._JOBS["j1"] = JobRecord(job_id="j1", edition="delhi", date="2025-09-13", status="running", pages_total=2)
    client = TestClient(app)
    r = client.get("/api/jobs/active")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["job_id"] == "j1"
    assert body[0]["elapsed_s"] == 0.0  # started_at was never set on this hand-built record
