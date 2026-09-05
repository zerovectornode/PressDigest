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
from hindu_extract import page_errors, storage
from hindu_extract.articles_pipeline import process_edition_articles, process_page_articles, write_edition_markdown
from hindu_extract.config import Config
from hindu_extract.gemini_client import GeminiError, is_retryable
from hindu_extract.pipeline import PageOutcome, extract_pages, retry_single_page
from hindu_extract.trace import RunTracer, get_page_stages, new_run_id

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
    error: page_errors.PageError | None = None


@dataclass
class JobRecord:
    job_id: str
    edition: str
    date: str
    status: str = "queued"  # queued|running|done|completed_with_errors|failed
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


def start_retry_job(edition: str, date: str, page_nums: list[int], config: Config) -> str:
    """Like start_extraction_job, but for re-running a known subset of an
    existing edition's pages (POST /api/editions/{id}/retry-failed) -
    Phase 1 is skipped entirely (those pages already have bronze data, or
    this wouldn't be a Phase 2 retry), so this reuses the Dashboard's
    existing job-progress polling for free without re-touching the PDF at
    all."""
    job_id = uuid.uuid4().hex[:12]
    run_id = new_run_id()
    with _LOCK:
        _JOBS[job_id] = JobRecord(job_id=job_id, edition=edition, date=date, run_id=run_id)
    _EXECUTOR.submit(_run_retry_job, job_id, edition, date, page_nums, config, run_id)
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


def _phase2_failure_stage(error: Exception) -> str:
    """Best-effort attribution, from the actual call structure in
    articles_pipeline.process_page_articles (group_page -> call_gemini,
    then assemble_articles): a GeminiError (our own MAX_TOKENS/empty-
    response/quota checks) or the SDK's own APIError (a non-retryable
    ClientError, or a ServerError that exhausted the retry ladder - see
    gemini_client._generate_with_retry, which re-raises the original SDK
    exception rather than wrapping it) can only originate from the Gemini
    call itself. validate_page never raises (checksum/contiguity issues are
    flagged via needs_review, not exceptions - see grouping.py) - so
    anything else raising in this path is assembly."""
    from google.genai import errors

    if isinstance(error, (GeminiError, errors.APIError)):
        return "gemini_call"
    return "assembly"


def _lookup_attempt_count(config: Config, run_id: str, page_num: int) -> int:
    """The real attempt count lives in the gemini_call trace event's detail
    (written by gemini_client._generate_with_retry), not on the exception
    object itself - looked up here rather than threaded through
    error_callback so gemini_client.py doesn't need to know about
    page_errors.py at all."""
    events = [e for e in get_page_stages(config.trace_db, run_id, page_num) if e["stage"] == "gemini_call"]
    if not events:
        return 1
    return events[-1]["detail"].get("attempt_count") or 1


def _record_phase1_failure(config: Config, job_id: str, edition: str, date: str, page_num: int, error: Exception) -> None:
    # Reuses gemini_client.is_retryable rather than hardcoding False here -
    # it happens to always be False for a Phase 1 exception today (none of
    # them are a recognized transient transport/server error), but this
    # keeps there being exactly one place that judges retryability, per
    # the "don't build a second severity taxonomy" instruction.
    page_errors.write_page_error(
        config, edition, date, page_num, stage="phase1_extraction", code=None, message=str(error),
        attempt_count=1, retryable=is_retryable(error),
    )
    _update_page(
        job_id, page_num, status="failed",
        error=page_errors.read_page_error(config, edition, date, page_num),
    )
    with _LOCK:
        _JOBS[job_id].pages_done += 1


def _record_phase2_failure(config: Config, job_id: str, run_id: str, edition: str, date: str, page_num: int, error: Exception) -> None:
    stage = _phase2_failure_stage(error)
    # .code (an int HTTP-ish status, e.g. 503/429) only exists on the SDK's
    # own APIError - our own GeminiError (MAX_TOKENS/empty-response/quota
    # checks) has no such code, since it isn't a transport-layer failure.
    code = getattr(error, "code", None)
    attempt_count = _lookup_attempt_count(config, run_id, page_num) if stage == "gemini_call" else 1
    page_errors.write_page_error(
        config, edition, date, page_num, stage=stage, code=code, message=str(error),
        attempt_count=attempt_count, retryable=is_retryable(error),
    )
    _update_page(
        job_id, page_num, status="failed",
        error=page_errors.read_page_error(config, edition, date, page_num),
    )
    with _LOCK:
        _JOBS[job_id].pages_done += 1


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

        extracted_page_nums: list[int] = []

        def on_page_extracted(outcome: PageOutcome) -> None:
            # Phase 1 (fast) done for this page; Phase 2 (slow, the real
            # source of "minutes") is next.
            extracted_page_nums.append(outcome.page_num)
            _update_page(job_id, outcome.page_num, status="grouping", cached=outcome.from_cache)

        def on_phase1_error(page_num: int, error: Exception) -> None:
            # Isolated per-page (extract_pages now supports this, mirroring
            # Phase 2's existing isolation) - a Phase 1 hard-fail must not
            # take down the other 19 pages of an otherwise-fine edition.
            _record_phase1_failure(config, job_id, edition, date, page_num, error)

        extract_pages(
            pdf_path, edition, date, page_nums, config,
            progress_callback=on_page_extracted, error_callback=on_phase1_error, tracer=tracer,
        )

        def on_page_done(outcome) -> None:
            # Clears a stale error.json from an earlier failed attempt at
            # this same edition/date (e.g. a full re-extraction after a
            # prior partial failure) - a fresh success must not leave a
            # leftover "failed" status behind for a page that just worked.
            page_errors.clear_page_error(config, edition, date, outcome.page_num)
            with _LOCK:
                phase1_cached = next(
                    p for p in _JOBS[job_id].per_page if p.page_num == outcome.page_num
                ).cached
            _update_page(
                job_id,
                outcome.page_num,
                status="done",
                error=None,
                articles_found=len(outcome.articles),
                validation_ok=outcome.validation_ok,
                needs_review=outcome.needs_review,
                cached=bool(phase1_cached) and outcome.all_cached,
            )
            with _LOCK:
                _JOBS[job_id].pages_done += 1

        def on_page2_error(page_num: int, error: Exception) -> None:
            _record_phase2_failure(config, job_id, run_id, edition, date, page_num, error)

        # Only pages that survived Phase 1 are eligible for Phase 2 - a page
        # extract_pages already isolated as failed has no bronze lines to
        # group, and must not be attempted here too.
        #
        # Concurrent across pages (see rate_limit.TokenAwareLimiter /
        # config/default.yaml "concurrency") rather than the old strictly
        # sequential loop - one page's failure is reported via
        # on_page2_error and doesn't stop the rest.
        process_edition_articles(
            config,
            edition,
            date,
            extracted_page_nums,
            use_cache=True,
            progress_callback=on_page_done,
            error_callback=on_page2_error,
            tracer=tracer,
        )

        write_edition_markdown(config, edition, date, extracted_page_nums)
        with _LOCK:
            job = _JOBS[job_id]
            any_failed = any(p.status == "failed" for p in job.per_page)
            job.status = "completed_with_errors" if any_failed else "done"
            job.finished_at = time.time()
        tracer.finish_run("completed_with_errors" if any_failed else "done")
    except Exception as e:  # noqa: BLE001 - worker-thread boundary: must never crash silently
        with _LOCK:
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error = str(e)
            _JOBS[job_id].finished_at = time.time()
        tracer.finish_run("failed")


def retry_page_sync(config: Config, edition: str, date: str, page_num: int) -> None:
    """Backs POST /api/editions/{id}/pages/{n}/retry - re-runs Phase 1 (a
    cache hit if it already succeeded - see pipeline.retry_single_page) and
    Phase 2 for exactly this page, synchronously (matches
    trigger_ranking_route's existing sync-def-in-threadpool pattern - one
    page is cheap enough that blocking the request is simpler than
    building a job for it). Never raises: a failure here is recorded via
    page_errors the same way a failure during the main job is, and the
    caller (main.py) re-reads the page's status afterward rather than
    branching on this function's return value.

    Deliberately does NOT clear the page's existing error.json up front,
    before attempting anything - only on confirmed success, right before
    finish_run("done") below. If this retry fails again, write_page_error
    simply overwrites the old error with the new one; there is never a
    window where the page has neither a valid gold write nor a record of
    why it's still failed (see page_errors.py)."""
    run_id = new_run_id()
    tracer = RunTracer(db_path=config.trace_db, run_id=run_id)
    tracer.start_run(edition, date, None, 1)

    pdf_path = storage.raw_pdf_path(config, edition, date)
    try:
        retry_single_page(pdf_path, edition, date, page_num, config, tracer=tracer)
    except Exception as e:  # noqa: BLE001 - recorded, not raised - see docstring
        page_errors.write_page_error(
            config, edition, date, page_num, stage="phase1_extraction", code=None, message=str(e),
            attempt_count=1, retryable=is_retryable(e),
        )
        tracer.finish_run("failed")
        return

    try:
        process_page_articles(config, edition, date, page_num, use_cache=True, tracer=tracer)
    except Exception as e:  # noqa: BLE001 - recorded, not raised - see docstring
        stage = _phase2_failure_stage(e)
        code = getattr(e, "code", None)
        attempt_count = _lookup_attempt_count(config, run_id, page_num) if stage == "gemini_call" else 1
        page_errors.write_page_error(
            config, edition, date, page_num, stage=stage, code=code, message=str(e),
            attempt_count=attempt_count, retryable=is_retryable(e),
        )
        tracer.finish_run("failed")
        return

    # process_page_articles has already written articles.json by this
    # point (it returns only after that write succeeds) - the error is
    # cleared strictly after, never before.
    page_errors.clear_page_error(config, edition, date, page_num)
    tracer.finish_run("done")


def _run_retry_job(job_id: str, edition: str, date: str, page_nums: list[int], config: Config, run_id: str) -> None:
    """Phase 2 only, for an explicit page_nums subset - backs both the
    single-page retry endpoint's underlying work and "retry N failed
    pages." Does NOT clear error.json up front for any page - only
    on_page_done (below) clears it, and only after that page's gold write
    has already succeeded, so a page that fails again just gets its
    error.json overwritten with the new failure rather than passing
    through a gap with no error recorded at all."""
    tracer = RunTracer(
        db_path=config.trace_db,
        run_id=run_id,
        on_stage_start=lambda page_num, stage_name: _on_stage_start(job_id, page_num, stage_name),
    )
    try:
        with _LOCK:
            job = _JOBS[job_id]
            job.status = "running"
            job.started_at = time.time()
            job.pages_total = len(page_nums)
            job.per_page = [PagePhase(page_num=n, status="grouping") for n in page_nums]
        tracer.start_run(edition, date, None, len(page_nums))

        def on_page_done(outcome) -> None:
            page_errors.clear_page_error(config, edition, date, outcome.page_num)
            _update_page(
                job_id, outcome.page_num, status="done", error=None,
                articles_found=len(outcome.articles), validation_ok=outcome.validation_ok,
                needs_review=outcome.needs_review, cached=outcome.all_cached,
            )
            with _LOCK:
                _JOBS[job_id].pages_done += 1

        def on_page_error(page_num: int, error: Exception) -> None:
            _record_phase2_failure(config, job_id, run_id, edition, date, page_num, error)

        process_edition_articles(
            config, edition, date, page_nums, use_cache=True,
            progress_callback=on_page_done, error_callback=on_page_error, tracer=tracer,
        )
        write_edition_markdown(config, edition, date, page_nums)

        with _LOCK:
            job = _JOBS[job_id]
            any_failed = any(p.status == "failed" for p in job.per_page)
            job.status = "completed_with_errors" if any_failed else "done"
            job.finished_at = time.time()
        tracer.finish_run("completed_with_errors" if any_failed else "done")
    except Exception as e:  # noqa: BLE001 - worker-thread boundary: must never crash silently
        with _LOCK:
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error = str(e)
            _JOBS[job_id].finished_at = time.time()
        tracer.finish_run("failed")
