"""In-memory background job registry for edition extraction.

Extraction is a background job, not a blocking request, because 18 pages
at Gemini's thinking_level=HIGH can take minutes (see design/DESIGN.md).
This module only orchestrates - it reuses pipeline.extract_pages (Phase 1)
and articles_pipeline.process_page_articles (Phase 2+3) exactly as the CLI
does, with no extraction logic duplicated here.

In-memory is a deliberate v1 choice: this is a local-dev single-process
app, and job state doesn't need to survive a restart yet (bronze/Gemini
caches already make a restart-and-reupload cheap regardless). Revisit if
this moves to a multi-process/hosted deployment.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from hindu_extract import cache as cache_module
from hindu_extract.articles_pipeline import process_edition_articles, write_edition_markdown
from hindu_extract.config import Config
from hindu_extract.pipeline import PageOutcome, extract_pages
from hindu_extract.trace import RunTracer, new_run_id

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extraction-job")
_LOCK = threading.Lock()

# Which of trace.STAGE_NAMES belong to which phase, for the on_stage_start
# hook below - Phase 1 (pipeline.extract_pages) is strictly sequential, one
# page at a time; Phase 2 (articles_pipeline.process_edition_articles) runs
# up to config.concurrency.max_concurrent pages at once. "ranking" is
# edition-wide (page_num=0 sentinel, see trace.py) and never fires within a
# job's tracer - jobs.py never calls ranking.py.
_PHASE1_STAGES = {"char_extraction", "line_building", "ligature_canary"}
_PHASE2_STAGES = {"gemini_call", "validation", "assembly"}
ACTIVE_JOB_STATUSES = ("queued", "running")


@dataclass
class PagePhase:
    page_num: int
    status: str = "pending"  # pending|extracting|grouping|done|failed
    current_stage: str | None = None
    articles_found: int | None = None
    validation_ok: bool | None = None
    needs_review: bool | None = None
    cached: bool | None = None
    error: str | None = None


@dataclass
class JobRecord:
    job_id: str
    edition: str
    date: str
    status: str = "queued"  # queued|running|done|failed
    pages_done: int = 0
    pages_total: int = 0
    per_page: list[PagePhase] = field(default_factory=list)
    error: str | None = None
    run_id: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def all_cached(self) -> bool:
        return bool(self.per_page) and all(p.cached for p in self.per_page)


_JOBS: dict[str, JobRecord] = {}


def get_job(job_id: str) -> JobRecord | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_active_job_for_edition(edition: str, date: str) -> JobRecord | None:
    """Most relevant for a currently-in-flight extraction of this exact
    edition/date - used by editions.get_page_status to prefer live job
    state over an on-disk snapshot. Linear scan over _JOBS, which stays
    small by construction (in-memory, one entry per job started this
    process's lifetime - see module docstring)."""
    with _LOCK:
        candidates = [
            j
            for j in _JOBS.values()
            if j.edition == edition and j.date == date and j.status in ACTIVE_JOB_STATUSES
        ]
        return candidates[-1] if candidates else None


def list_active_jobs() -> list[JobRecord]:
    """Backs GET /api/jobs/active - lets the Dashboard reconnect to a job
    that's still running after a page reload without already knowing its
    job_id (see design/DESIGN.md and the plan for Enhancement 4)."""
    with _LOCK:
        return [j for j in _JOBS.values() if j.status in ACTIVE_JOB_STATUSES]


def start_extraction_job(pdf_path: Path, edition: str, date: str, config: Config) -> str:
    job_id = uuid.uuid4().hex[:12]
    run_id = new_run_id()
    with _LOCK:
        _JOBS[job_id] = JobRecord(job_id=job_id, edition=edition, date=date, run_id=run_id)
    _EXECUTOR.submit(_run_job, job_id, pdf_path, edition, date, config, run_id)
    return job_id


def _update_page(job_id: str, page_num: int, **kwargs) -> None:
    with _LOCK:
        page = next((p for p in _JOBS[job_id].per_page if p.page_num == page_num), None)
        if page is None:
            # page_num=0 (ranking's edition-wide sentinel) or a stale
            # job_id - never expected from this job's own tracer, but the
            # hook must not raise regardless (see trace.py's on_stage_start).
            return
        for key, value in kwargs.items():
            setattr(page, key, value)


def _on_stage_start(job_id: str, page_num: int, stage_name: str) -> None:
    if stage_name in _PHASE1_STAGES:
        _update_page(job_id, page_num, status="extracting", current_stage=stage_name)
    elif stage_name in _PHASE2_STAGES:
        _update_page(job_id, page_num, status="grouping", current_stage=stage_name)


def _run_job(job_id: str, pdf_path: Path, edition: str, date: str, config: Config, run_id: str) -> None:
    tracer = RunTracer(
        db_path=config.trace_db,
        run_id=run_id,
        on_stage_start=lambda page_num, stage_name: _on_stage_start(job_id, page_num, stage_name),
    )
    try:
        with _LOCK:
            _JOBS[job_id].status = "running"
            _JOBS[job_id].started_at = time.time()

        with pdfplumber.open(pdf_path) as pdf:
            page_nums = list(range(1, len(pdf.pages) + 1))
        pdf_hash = cache_module.hash_bytes(Path(pdf_path).read_bytes())
        tracer.start_run(edition, date, pdf_hash, len(page_nums))
        with _LOCK:
            job = _JOBS[job_id]
            job.pages_total = len(page_nums)
            job.per_page = [PagePhase(page_num=n) for n in page_nums]

        # Pages start "pending" and are only flipped to "extracting" by
        # on_stage_start as the (strictly sequential) Phase 1 loop actually
        # reaches each one - marking every page "extracting" up front here,
        # as before, was misleading: it implied all pages were in flight
        # when at most one Phase-1 page ever is.

        def on_page_extracted(outcome: PageOutcome) -> None:
            # Phase 1 (fast) done for this page; Phase 2 (slow, the real
            # source of "minutes") is next.
            _update_page(job_id, outcome.page_num, status="grouping", cached=outcome.from_cache)

        extract_pages(
            pdf_path, edition, date, page_nums, config, progress_callback=on_page_extracted, tracer=tracer
        )

        def on_page_done(outcome) -> None:
            with _LOCK:
                phase1_cached = next(
                    p for p in _JOBS[job_id].per_page if p.page_num == outcome.page_num
                ).cached
            _update_page(
                job_id,
                outcome.page_num,
                status="done",
                articles_found=len(outcome.articles),
                validation_ok=outcome.validation_ok,
                needs_review=outcome.needs_review,
                cached=bool(phase1_cached) and outcome.all_cached,
            )
            with _LOCK:
                _JOBS[job_id].pages_done += 1

        def on_page_error(page_num: int, error: Exception) -> None:
            _update_page(job_id, page_num, status="failed", error=str(error))
            with _LOCK:
                _JOBS[job_id].pages_done += 1

        # Concurrent across pages (see rate_limit.TokenAwareLimiter /
        # config/default.yaml "concurrency") rather than the old strictly
        # sequential loop - one page's failure is reported via
        # on_page_error and doesn't stop the rest.
        process_edition_articles(
            config,
            edition,
            date,
            page_nums,
            use_cache=True,
            progress_callback=on_page_done,
            error_callback=on_page_error,
            tracer=tracer,
        )

        write_edition_markdown(config, edition, date, page_nums)
        with _LOCK:
            _JOBS[job_id].status = "done"
            _JOBS[job_id].finished_at = time.time()
        tracer.finish_run("done")
    except Exception as e:  # noqa: BLE001 - worker-thread boundary: must never crash silently
        with _LOCK:
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error = str(e)
            _JOBS[job_id].finished_at = time.time()
        tracer.finish_run("failed")
