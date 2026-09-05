"""Offline tests for durable page-failure persistence (page_errors.py +
editions.get_page_status/get_page_error), run-level partial-failure status
(trace.get_failed_pages_for_run), and edition deletion (delete_edition.py) -
no network access, no real Gemini calls."""
from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from hindu_extract import page_errors, storage
from hindu_extract.api import editions, jobs
from hindu_extract.api.main import app
from hindu_extract.articles_pipeline import gold_edition_dir
from hindu_extract.delete_edition import UnsafePathError, delete_edition
from hindu_extract.trace import RunTracer, get_failed_pages_for_run, new_run_id


@pytest.fixture
def tmp_config(config, tmp_path):
    return dataclasses.replace(config, project_root=tmp_path)


@pytest.fixture(autouse=True)
def clean_jobs():
    jobs._JOBS.clear()
    yield
    jobs._JOBS.clear()


def _write_gold_article(config, edition, date, page_num, article_count=1):
    gold_dir = gold_edition_dir(config, edition, date) / f"page_{page_num:02d}"
    gold_dir.mkdir(parents=True, exist_ok=True)
    (gold_dir / "articles.json").write_text(
        json.dumps({"articles": [{}] * article_count, "validation_ok": True}), encoding="utf-8"
    )


def test_page_error_write_read_clear_roundtrip(tmp_config):
    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is None

    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="high demand", attempt_count=4
    )
    error = page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1)
    assert error is not None
    assert error.stage == "gemini_call"
    assert error.code == 503
    assert error.attempt_count == 4

    page_errors.clear_page_error(tmp_config, "delhi", "2025-09-13", 1)
    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is None


def test_get_page_status_returns_failed_after_the_job_that_recorded_it_is_gone(tmp_config):
    """This is the exact bug from the 2026-09-04 incident: a page's failed
    status must survive past the job's own lifetime, not just exist while
    jobs.get_active_job_for_edition can still find it."""
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 2})
    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="high demand", attempt_count=4
    )
    _write_gold_article(tmp_config, "delhi", "2025-09-13", 2)

    # No active job in jobs._JOBS at all - simulates the run having already
    # finished (status flipped to completed_with_errors/done) some time ago.
    assert jobs.get_active_job_for_edition("delhi", "2025-09-13") is None
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 1) == "failed"
    assert editions.get_page_status(tmp_config, "delhi", "2025-09-13", 2) == "done"

    error = editions.get_page_error(tmp_config, "delhi", "2025-09-13", 1)
    assert error is not None
    assert error.stage == "gemini_call"
    assert error.code == 503


def test_edition_detail_reports_failed_pages_and_stays_listed_when_all_pages_fail(tmp_config):
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 1})
    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="phase1_extraction", code=None, message="corrupt page", attempt_count=1
    )

    # Zero pages of gold ever existed - the pre-fix behavior treated this as
    # "edition doesn't exist" (404/absent from the list). It must still show
    # up so the failure is visible and retryable.
    summaries = editions.list_editions(tmp_config)
    assert len(summaries) == 1
    assert summaries[0].failed_pages == [1]
    assert summaries[0].article_count == 0

    detail = editions.get_edition_detail(tmp_config, "delhi", "2025-09-13")
    assert detail is not None
    assert detail.failed_pages == [1]
    assert detail.pages == [editions.PageStatusOut(page_num=1, status="failed")]


def test_page_route_exposes_structured_error_for_a_failed_page(tmp_config, monkeypatch):
    import hindu_extract.api.main as main_module

    monkeypatch.setattr(main_module, "config", tmp_config)

    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 1})
    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="high demand",
        attempt_count=4, retryable=True,
    )

    client = TestClient(app)
    r = client.get("/api/editions/delhi__2025-09-13/pages/1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"]["stage"] == "gemini_call"
    assert body["error"]["code"] == 503
    assert body["error"]["attempt_count"] == 4
    assert body["error"]["retryable"] is True


def test_non_retryable_flag_surfaces_through_the_page_api(tmp_config, monkeypatch):
    """A deterministic failure (e.g. a 400 INVALID_ARGUMENT, or a Phase 1
    hard-fail) must reach the frontend as retryable: false, so the Page
    Reader can hide "Retry this page" instead of offering an action that
    can't succeed."""
    import hindu_extract.api.main as main_module

    monkeypatch.setattr(main_module, "config", tmp_config)

    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 1})
    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="phase1_extraction", code=None,
        message="page 1 produced zero lines", attempt_count=1, retryable=False,
    )

    client = TestClient(app)
    r = client.get("/api/editions/delhi__2025-09-13/pages/1")
    assert r.status_code == 200
    assert r.json()["error"]["retryable"] is False


def test_retry_page_sync_clears_the_stale_error_only_after_success(tmp_config, monkeypatch):
    """The ordering that matters: error.json must not be cleared up front,
    before the retry is even attempted - only after both Phase 1 and
    Phase 2 have actually succeeded. Mocks retry_single_page/
    process_page_articles directly (rather than the Gemini SDK) so this
    tests jobs.py's own sequencing, not the pipeline's."""
    import hindu_extract.api.jobs as jobs_module

    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="high demand",
        attempt_count=2, retryable=True,
    )
    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is not None

    clear_calls: list[int] = []
    real_clear = page_errors.clear_page_error

    def _tracking_clear(config, edition, date, page_num):
        clear_calls.append(page_num)
        real_clear(config, edition, date, page_num)

    monkeypatch.setattr(jobs_module, "retry_single_page", lambda *a, **kw: None)
    monkeypatch.setattr(jobs_module, "process_page_articles", lambda *a, **kw: None)
    monkeypatch.setattr(page_errors, "clear_page_error", _tracking_clear)

    jobs_module.retry_page_sync(tmp_config, "delhi", "2025-09-13", 1)

    assert clear_calls == [1]
    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is None


def test_retry_page_sync_overwrites_rather_than_clears_on_a_repeat_failure(tmp_config, monkeypatch):
    """If the retry fails again, the old error is replaced by the new one
    directly (write_page_error overwrites the file) - there is never a
    moment where the page has no error recorded at all while still being
    broken."""
    import hindu_extract.api.jobs as jobs_module
    from hindu_extract.pipeline import EmptyPageError

    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="first failure",
        attempt_count=2, retryable=True,
    )

    def _raise(*args, **kwargs):
        raise EmptyPageError("page 1 produced zero lines")

    monkeypatch.setattr(jobs_module, "retry_single_page", _raise)

    jobs_module.retry_page_sync(tmp_config, "delhi", "2025-09-13", 1)

    error = page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1)
    assert error is not None
    assert error.message == "page 1 produced zero lines"
    assert error.retryable is False


def test_delete_edition_removes_error_json_for_a_failed_page(tmp_config):
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 1})
    page_errors.write_page_error(
        tmp_config, "delhi", "2025-09-13", 1, stage="gemini_call", code=503, message="high demand", attempt_count=1
    )
    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is not None

    delete_edition(tmp_config, "delhi", "2025-09-13")

    assert page_errors.read_page_error(tmp_config, "delhi", "2025-09-13", 1) is None


def test_run_summary_reports_failed_pages_derived_from_stage_events(tmp_config):
    from hindu_extract.api import runs as runs_lib

    tracer = RunTracer(db_path=tmp_config.trace_db, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 2)
    with tracer.stage(1, "char_extraction"):
        pass
    with pytest.raises(RuntimeError):
        with tracer.stage(1, "gemini_call"):
            raise RuntimeError("503 UNAVAILABLE")
    with tracer.stage(2, "gemini_call"):
        pass
    tracer.finish_run("completed_with_errors")

    assert get_failed_pages_for_run(tmp_config.trace_db, tracer.run_id) == [1]

    run_detail = runs_lib.get_run(tmp_config, tracer.run_id)
    assert run_detail.status == "completed_with_errors"
    assert run_detail.failed_pages == [1]


def test_delete_edition_removes_artifacts_but_preserves_gemini_and_ranking_cache(tmp_config):
    storage.write_manifest(tmp_config, "delhi", "2025-09-13", {"page_count": 1})
    _write_gold_article(tmp_config, "delhi", "2025-09-13", 1)
    raw_path = storage.raw_pdf_path(tmp_config, "delhi", "2025-09-13")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"%PDF-fake")

    gemini_cache_file = tmp_config.gemini_cache_root / "somehash.json"
    gemini_cache_file.parent.mkdir(parents=True, exist_ok=True)
    gemini_cache_file.write_text("{}", encoding="utf-8")
    ranking_cache_file = tmp_config.ranking_cache_root / "otherhash.json"
    ranking_cache_file.parent.mkdir(parents=True, exist_ok=True)
    ranking_cache_file.write_text("{}", encoding="utf-8")

    tracer = RunTracer(db_path=tmp_config.trace_db, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)
    tracer.finish_run("done")

    result = delete_edition(tmp_config, "delhi", "2025-09-13")

    assert result.bytes_freed > 0
    assert not raw_path.exists()
    assert not storage.bronze_edition_dir(tmp_config, "delhi", "2025-09-13").exists()
    assert not gold_edition_dir(tmp_config, "delhi", "2025-09-13").exists()
    assert gemini_cache_file.exists()  # never touched - see delete_edition.py
    assert ranking_cache_file.exists()
    assert editions.get_total_page_count(tmp_config, "delhi", "2025-09-13") is None

    from hindu_extract.trace import get_run

    assert get_run(tmp_config.trace_db, tracer.run_id) is None


def test_delete_edition_refuses_a_path_traversal_edition_id(tmp_config):
    with pytest.raises(UnsafePathError):
        delete_edition(tmp_config, "../../../../etc", "passwd")


def test_retry_single_page_does_not_corrupt_the_edition_manifest_page_count(config, pdf_path, tmp_path):
    """The bug caught while implementing this: retrying just page 1 must
    not overwrite manifest.json's page_count down to 1 - a single-page
    retry must never look like the edition shrank to one page. Only
    extracts 2 pages (not the whole fixture PDF) to keep this fast - the
    manifest-preservation property doesn't depend on the page count."""
    from hindu_extract.pipeline import extract_pages, retry_single_page

    test_config = dataclasses.replace(config, project_root=tmp_path)

    extract_pages(pdf_path, "delhi", "2025-09-13", [1, 2], test_config)
    manifest_before = storage.read_manifest(test_config, "delhi", "2025-09-13")
    assert manifest_before["page_count"] == 2

    retry_single_page(pdf_path, "delhi", "2025-09-13", 1, test_config)

    manifest_after = storage.read_manifest(test_config, "delhi", "2025-09-13")
    assert manifest_after["page_count"] == 2


def test_retry_page_sync_records_a_fresh_failure_in_place_without_touching_other_pages(
    config, pdf_path, tmp_path, monkeypatch
):
    """A retry that fails again must update the same edition/date in
    place - no duplicate edition, and the other (untouched) page's gold
    must survive exactly as it was."""
    from google.genai import errors
    from hindu_extract.pipeline import extract_pages

    test_config = dataclasses.replace(
        config,
        project_root=tmp_path,
        gemini_retry=dataclasses.replace(config.gemini_retry, max_attempts=1),
    )
    extract_pages(pdf_path, "delhi", "2025-09-13", [1, 2], test_config)
    _write_gold_article(test_config, "delhi", "2025-09-13", 2, article_count=3)

    # retry_page_sync locates the PDF via storage.raw_pdf_path(config, ...),
    # not the pdf_path fixture directly - it must exist at that location
    # inside test_config's own tmp_path-rooted data tree.
    import shutil

    raw_path = storage.raw_pdf_path(test_config, "delhi", "2025-09-13")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf_path, raw_path)

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})

    import google.genai as genai_module

    monkeypatch.setattr(genai_module, "Client", lambda api_key=None: type("C", (), {"models": FakeModels()})())

    jobs.retry_page_sync(test_config, "delhi", "2025-09-13", 1)

    assert editions.get_page_status(test_config, "delhi", "2025-09-13", 1) == "failed"
    error = editions.get_page_error(test_config, "delhi", "2025-09-13", 1)
    assert error is not None
    assert error.stage == "gemini_call"
    assert error.code == 503

    # Page 2's gold, untouched by this retry, must be exactly as it was -
    # this edition/date was updated in place, not replaced or duplicated.
    assert editions.get_page_status(test_config, "delhi", "2025-09-13", 2) == "done"
    detail = editions.get_edition_detail(test_config, "delhi", "2025-09-13")
    assert detail.article_count == 3
    # No duplicate edition was created by the retry - still exactly one.
    assert len(editions.list_editions(test_config)) == 1
