"""Offline tests for the trace/instrumentation layer (trace.py) - no
network access, everything runs against a throwaway SQLite file."""
from __future__ import annotations

import pytest

from hindu_extract.trace import (
    RunTracer,
    get_page_raw,
    get_page_stages,
    get_quota_usage,
    get_run,
    get_run_pages,
    list_runs,
    new_run_id,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "trace.db"


def test_start_and_finish_run_records_expected_fields(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", "abc123", 3)
    tracer.finish_run("done")

    run = get_run(db_path, tracer.run_id)
    assert run["edition"] == "delhi"
    assert run["date"] == "2025-09-13"
    assert run["pdf_hash"] == "abc123"
    assert run["page_count"] == 3
    assert run["status"] == "done"
    assert run["total_wall_clock_s"] >= 0
    assert run["finished_at"] is not None


def test_list_runs_returns_most_recent_first(db_path):
    t1 = RunTracer(db_path=db_path, run_id=new_run_id())
    t1.start_run("delhi", "2025-09-13", None, 1)
    t1.finish_run("done")
    t2 = RunTracer(db_path=db_path, run_id=new_run_id())
    t2.start_run("delhi", "2025-09-14", None, 1)
    t2.finish_run("done")

    runs = list_runs(db_path)
    run_ids = [r["run_id"] for r in runs]
    assert run_ids.index(t2.run_id) < run_ids.index(t1.run_id)


def test_stage_records_start_end_duration_and_detail(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)

    with tracer.stage(1, "char_extraction") as detail:
        detail["char_count"] = 4365

    events = get_page_stages(db_path, tracer.run_id, 1)
    assert len(events) == 1
    e = events[0]
    assert e["stage"] == "char_extraction"
    assert e["detail"]["char_count"] == 4365
    assert e["duration_s"] >= 0
    assert e["error"] is None


def test_failed_stage_still_writes_its_trace_event(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)

    with pytest.raises(RuntimeError):
        with tracer.stage(5, "gemini_call") as detail:
            detail["model"] = "gemini-3.1-flash-lite"
            raise RuntimeError("MAX_TOKENS truncation")

    events = get_page_stages(db_path, tracer.run_id, 5)
    assert len(events) == 1
    assert events[0]["stage"] == "gemini_call"
    assert events[0]["error"] == "MAX_TOKENS truncation"
    assert events[0]["detail"]["model"] == "gemini-3.1-flash-lite"


def test_stage_rejects_unknown_stage_name(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)
    with pytest.raises(ValueError):
        with tracer.stage(1, "not_a_real_stage"):
            pass


def test_finish_run_aggregates_tokens_and_cache_hit_ratio_from_gemini_call_events(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 2)

    with tracer.stage(1, "gemini_call") as detail:
        detail["total_token_count"] = 7147
        detail["cache_hit"] = False
    with tracer.stage(2, "gemini_call") as detail:
        detail["total_token_count"] = 100
        detail["cache_hit"] = True

    tracer.finish_run("done")
    run = get_run(db_path, tracer.run_id)
    assert run["total_tokens"] == 7247
    assert run["cache_hit_ratio"] == pytest.approx(0.5)


def test_record_and_fetch_gemini_raw(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)
    tracer.record_gemini_raw(1, "L0001|9.0|hello", '{"articles": []}')

    raw = get_page_raw(db_path, tracer.run_id, 1)
    assert raw["prompt"] == "L0001|9.0|hello"
    assert raw["raw_response"] == '{"articles": []}'


def test_get_run_pages_returns_distinct_page_numbers_in_order(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 2)
    with tracer.stage(2, "render"):
        pass
    with tracer.stage(1, "render"):
        pass
    with tracer.stage(1, "char_extraction"):
        pass

    assert get_run_pages(db_path, tracer.run_id) == [1, 2]


def test_quota_usage_counts_gemini_calls_today_and_tokens_last_minute(db_path):
    tracer = RunTracer(db_path=db_path, run_id=new_run_id())
    tracer.start_run("delhi", "2025-09-13", None, 1)
    with tracer.stage(1, "gemini_call") as detail:
        detail["total_token_count"] = 7147

    quota = get_quota_usage(db_path, requests_per_day_limit=500, tokens_per_minute_limit=250_000)
    assert quota["requests_today"] == 1
    assert quota["tokens_last_minute"] == 7147
    assert quota["requests_per_day_limit"] == 500
    assert quota["tokens_per_minute_limit"] == 250_000
