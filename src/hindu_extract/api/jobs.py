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


@dataclass
class PagePhase:
    page_num: int
    status: str = "pending"  # pending|extracting|grouping|done|failed
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

    @property
    def all_cached(self) -> bool:
        return bool(self.per_page) and all(p.cached for p in self.per_page)


_JOBS: dict[str, JobRecord] = {}


def get_job(job_id: str) -> JobRecord | None:
    with _LOCK:
        return _JOBS.get(job_id)


def start_extraction_job(pdf_path: Path, edition: str, date: str, config: Config) -> str:
    job_id = uuid.uuid4().hex[:12]
    run_id = new_run_id()
    with _LOCK:
        _JOBS[job_id] = JobRecord(job_id=job_id, edition=edition, date=date, run_id=run_id)
    _EXECUTOR.submit(_run_job, job_id, pdf_path, edition, date, config, run_id)
    return job_id


def _update_page(job_id: str, page_num: int, **kwargs) -> None:
    with _LOCK:
        page = next(p for p in _JOBS[job_id].per_page if p.page_num == page_num)
        for key, value in kwargs.items():
            setattr(page, key, value)


def _run_job(job_id: str, pdf_path: Path, edition: str, date: str, config: Config, run_id: str) -> None:
    tracer = RunTracer(db_path=config.trace_db, run_id=run_id)
    try:
        with _LOCK:
            _JOBS[job_id].status = "running"

        with pdfplumber.open(pdf_path) as pdf:
            page_nums = list(range(1, len(pdf.pages) + 1))
        pdf_hash = cache_module.hash_bytes(Path(pdf_path).read_bytes())
        tracer.start_run(edition, date, pdf_hash, len(page_nums))
        with _LOCK:
            job = _JOBS[job_id]
            job.pages_total = len(page_nums)
            job.per_page = [PagePhase(page_num=n) for n in page_nums]

        for n in page_nums:
            _update_page(job_id, n, status="extracting")

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
        tracer.finish_run("done")
    except Exception as e:  # noqa: BLE001 - worker-thread boundary: must never crash silently
        with _LOCK:
            _JOBS[job_id].status = "failed"
            _JOBS[job_id].error = str(e)
        tracer.finish_run("failed")
