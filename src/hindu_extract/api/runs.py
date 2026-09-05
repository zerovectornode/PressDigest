"""Read-only helpers over trace.py's SQLite store for the Step D "Pipeline"
monitoring endpoints. Thin adapters from trace.py's plain dicts to the
API's typed schemas - trace.py itself has no FastAPI/Pydantic dependency.
"""
from __future__ import annotations

from hindu_extract.api.schemas import PageRawOut, QuotaOut, RunDetailOut, RunSummaryOut, StageEventOut
from hindu_extract.config import Config
from hindu_extract import trace


def _to_run_summary(config: Config, row: dict) -> RunSummaryOut:
    return RunSummaryOut(
        run_id=row["run_id"],
        edition=row["edition"],
        date=row["date"],
        pdf_hash=row["pdf_hash"],
        page_count=row["page_count"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        total_wall_clock_s=row["total_wall_clock_s"],
        total_tokens=row["total_tokens"],
        cache_hit_ratio=row["cache_hit_ratio"],
        status=row["status"],
        failed_pages=trace.get_failed_pages_for_run(config.trace_db, row["run_id"]),
    )


def list_runs(config: Config) -> list[RunSummaryOut]:
    return [_to_run_summary(config, r) for r in trace.list_runs(config.trace_db)]


def get_run(config: Config, run_id: str) -> RunDetailOut | None:
    row = trace.get_run(config.trace_db, run_id)
    if row is None:
        return None
    pages = trace.get_run_pages(config.trace_db, run_id)
    return RunDetailOut(**_to_run_summary(config, row).model_dump(), pages=pages)


def get_page_stages(config: Config, run_id: str, page_num: int) -> list[StageEventOut]:
    return [
        StageEventOut(
            page_num=e["page_num"],
            stage=e["stage"],
            started_at=e["started_at"],
            ended_at=e["ended_at"],
            duration_s=e["duration_s"],
            detail=e["detail"],
            error=e["error"],
        )
        for e in trace.get_page_stages(config.trace_db, run_id, page_num)
    ]


def get_page_raw(config: Config, run_id: str, page_num: int) -> PageRawOut | None:
    row = trace.get_page_raw(config.trace_db, run_id, page_num)
    if row is None:
        return None
    return PageRawOut(**row)


def get_quota(config: Config) -> QuotaOut:
    usage = trace.get_quota_usage(
        config.trace_db,
        requests_per_day_limit=config.concurrency.requests_per_day,
        tokens_per_minute_limit=config.concurrency.tokens_per_minute,
    )
    return QuotaOut(**usage)
