"""FastAPI app: a thin read layer over the gold JSON Phase 1/2/3 already
produce on disk, plus job orchestration for kicking off extraction.
No extraction logic lives here - see hindu_extract.api.jobs, which reuses
pipeline.extract_pages and articles_pipeline.process_page_articles exactly
as the CLI does.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hindu_extract import ranking as ranking_lib
from hindu_extract.api import editions as editions_lib
from hindu_extract.api import jobs
from hindu_extract.api import pages as pages_lib
from hindu_extract.api import runs as runs_lib
from hindu_extract.api.edition_id import InvalidEditionId, split_edition_id
from hindu_extract.api.metadata_parser import parse_metadata_from_pdf
from hindu_extract.api.schemas import (
    DeleteEditionOut,
    EditionDetailOut,
    EditionSummaryOut,
    JobStatusOut,
    PageArticlesOut,
    PageErrorOut,
    PageOut,
    ParsedMetadataOut,
    PagePhaseOut,
    PageRawOut,
    QuotaOut,
    RankingOut,
    RunDetailOut,
    RunSummaryOut,
    StageEventOut,
    StartJobOut,
)
from hindu_extract.config import load_config
from hindu_extract.delete_edition import delete_edition
from hindu_extract.storage import raw_pdf_path
from hindu_extract.trace import RunTracer, new_run_id

config = load_config()
_FRONTEND_DIST = config.project_root / "frontend" / "dist"

_log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level_name, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_logger = logging.getLogger("hindu_extract.startup")


def _proc_meminfo() -> dict[str, int] | None:
    """Parses /proc/meminfo (Linux only - the e2-micro deployment; returns
    None anywhere else, e.g. local Windows dev, rather than raising)."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        digits = rest.strip().split(" ")[0]
        if digits.isdigit():
            out[key] = int(digits) * 1024  # meminfo values are in kB
    return out


def _log_system_stats() -> None:
    """The only window into this process's actual environment - see
    design/DESIGN.md "Deployment: GCP e2-micro VM": free RAM and swap
    status matter specifically because pip installing pdfplumber's
    dependency tree, and extraction itself, have both been sized against
    an assumption (2GB swap active) that a silent provisioning slip could
    quietly violate."""
    meminfo = _proc_meminfo()
    if meminfo is not None:
        mem_available = meminfo.get("MemAvailable")
        swap_total = meminfo.get("SwapTotal")
        swap_free = meminfo.get("SwapFree")
        _logger.info(
            "memory: available=%s swap_total=%s swap_free=%s%s",
            f"{mem_available / 1e6:.0f}MB" if mem_available is not None else "unknown",
            f"{swap_total / 1e6:.0f}MB" if swap_total is not None else "unknown",
            f"{swap_free / 1e6:.0f}MB" if swap_free is not None else "unknown",
            "" if swap_total else " (NO SWAP ACTIVE)",
        )
        if not swap_total:
            _logger.warning("no swap active - see deploy/setup.sh; extraction may OOM under memory pressure")
    else:
        _logger.info("memory: /proc/meminfo unavailable (not Linux)")

    try:
        usage = shutil.disk_usage(config.data_anchor)
        _logger.info(
            "disk (%s): free=%.1fGB used=%.1fGB total=%.1fGB",
            config.data_anchor,
            usage.free / 1e9,
            usage.used / 1e9,
            usage.total / 1e9,
        )
    except OSError as e:
        _logger.warning("disk usage check failed for %s: %s", config.data_anchor, e)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Runs once per process start. Neither deployment target this has run
    on so far (Hugging Face Spaces, then a bare GCP VM - see design/
    DESIGN.md "Deployment") has had a local environment to iterate
    against, so a silent misconfiguration (data dir not writable, a
    dependency missing its expected version, a concurrency override that
    didn't take, swap not actually active) needs to be visible in the
    first few log lines rather than surfacing later as an opaque 500 or a
    silent OOM kill on first upload."""
    data_dir = config.data_anchor / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    writable = os.access(data_dir, os.W_OK)

    packages = ["pdfplumber", "pypdfium2", "Pillow", "fastapi", "uvicorn", "google-genai"]
    versions = []
    for pkg in packages:
        try:
            versions.append(f"{pkg}={version(pkg)}")
        except PackageNotFoundError:
            versions.append(f"{pkg}=MISSING")

    _logger.info("python=%s", sys.version.split()[0])
    _logger.info("log_level=%s", _log_level_name)
    _logger.info("project_root=%s", config.project_root)
    _logger.info("data_anchor=%s", config.data_anchor)
    _logger.info("data_dir=%s writable=%s", data_dir, writable)
    _logger.info("frontend_dist=%s exists=%s", _FRONTEND_DIST, _FRONTEND_DIST.is_dir())
    _logger.info("packages: %s", ", ".join(versions))
    _logger.info(
        "concurrency: max_concurrent=%d requests_per_minute=%d tokens_per_minute=%d",
        config.concurrency.max_concurrent,
        config.concurrency.requests_per_minute,
        config.concurrency.tokens_per_minute,
    )
    _log_system_stats()
    if not writable:
        _logger.error("data_dir %s is NOT writable - uploads/extraction will fail", data_dir)

    yield


app = FastAPI(title="hindu-extract API", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_route():
    """Liveness/readiness probe, and what the frontend polls with backoff
    on first load to ride out a cold start (container just booted, or the
    Space woke from idle) rather than showing a blank page or a hard
    error - see AppReadyGate.tsx. has_editions being false is a normal,
    expected state (a fresh container has nothing extracted yet), not a
    failure."""
    has_editions = bool(editions_lib.list_editions(config))
    return {"status": "ok", "has_editions": has_editions}


def _save_upload(file: UploadFile) -> Path:
    staging_dir = config.data_anchor / "data" / "uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / file.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


@app.post("/api/editions/parse-metadata", response_model=ParsedMetadataOut)
async def parse_metadata(file: UploadFile):
    pdf_path = _save_upload(file)
    result = parse_metadata_from_pdf(pdf_path, config)
    return ParsedMetadataOut(edition=result.edition, date=result.date)


@app.post("/api/editions", response_model=StartJobOut)
async def create_edition(file: UploadFile, edition: str, date: str):
    pdf_path = _save_upload(file)
    dest = raw_pdf_path(config, edition, date)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf_path, dest)

    job_id = jobs.start_extraction_job(dest, edition, date, config)
    return StartJobOut(job_id=job_id, edition=edition, date=date)


def _page_phase_to_out(p: jobs.PagePhase) -> PagePhaseOut:
    return PagePhaseOut(
        page_num=p.page_num,
        status=p.status,
        current_stage=p.current_stage,
        articles_found=p.articles_found,
        validation_ok=p.validation_ok,
        needs_review=p.needs_review,
        cached=p.cached,
        error=PageErrorOut(
            stage=p.error.stage, code=p.error.code, message=p.error.message,
            attempt_count=p.error.attempt_count, retryable=p.error.retryable,
        )
        if p.error is not None
        else None,
    )


def _job_to_status_out(record: jobs.JobRecord) -> JobStatusOut:
    elapsed_s = 0.0
    eta_s = None
    if record.started_at is not None:
        elapsed_s = (record.finished_at or time.time()) - record.started_at
        if record.status == "running" and record.pages_done > 0:
            avg_per_page = elapsed_s / record.pages_done
            remaining = record.pages_total - record.pages_done
            eta_s = avg_per_page * remaining if remaining > 0 else 0.0
    return JobStatusOut(
        job_id=record.job_id,
        edition=record.edition,
        date=record.date,
        status=record.status,
        pages_done=record.pages_done,
        pages_total=record.pages_total,
        per_page=[_page_phase_to_out(p) for p in record.per_page],
        all_cached=record.all_cached,
        error=record.error,
        elapsed_s=elapsed_s,
        eta_s=eta_s,
    )


@app.get("/api/jobs/active", response_model=list[JobStatusOut])
async def list_active_jobs_route():
    """Lets the Dashboard reconnect to a job that's still running after a
    page reload, without already knowing its job_id - see design/DESIGN.md
    and the plan's Enhancement 4."""
    return [_job_to_status_out(r) for r in jobs.list_active_jobs()]


@app.get("/api/jobs/{job_id}", response_model=JobStatusOut)
async def get_job_status(job_id: str):
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    return _job_to_status_out(record)


@app.get("/api/editions", response_model=list[EditionSummaryOut])
async def list_editions_route():
    return editions_lib.list_editions(config)


@app.get("/api/editions/{edition_id}", response_model=EditionDetailOut)
async def get_edition_route(edition_id: str):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    detail = editions_lib.get_edition_detail(config, edition, date)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no edition {edition_id!r}")
    return detail


@app.get("/api/editions/{edition_id}/pdf")
async def get_edition_pdf(edition_id: str):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    path = raw_pdf_path(config, edition, date)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no PDF stored for edition {edition_id!r}")
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/editions/{edition_id}/pages/{page_num}", response_model=PageOut)
async def get_page_route(edition_id: str, page_num: int):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    status = editions_lib.get_page_status(config, edition, date, page_num)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no page {page_num} for edition {edition_id!r}")
    if status != "done":
        return PageOut(
            page_num=page_num,
            status=status,
            width=None,
            height=None,
            line_count=None,
            article_count=0,
            validation_ok=False,
            coverage_ratio=None,
            error=editions_lib.get_page_error(config, edition, date, page_num) if status == "failed" else None,
        )
    return pages_lib.get_page(config, edition, date, page_num)


@app.get("/api/editions/{edition_id}/pages/{page_num}/articles", response_model=PageArticlesOut)
async def get_page_articles_route(edition_id: str, page_num: int):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    status = editions_lib.get_page_status(config, edition, date, page_num)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no page {page_num} for edition {edition_id!r}")
    if status != "done":
        return PageArticlesOut(status=status, articles=[])
    articles = pages_lib.get_page_articles(config, edition, date, page_num) or []
    return PageArticlesOut(status="done", articles=articles)


# Plain `def`, not `async def` - retry_page_sync makes a synchronous,
# possibly slow Gemini call; see trigger_ranking_route above for why this
# is safe (FastAPI runs it in a worker thread automatically).
@app.post("/api/editions/{edition_id}/pages/{page_num}/retry", response_model=PageOut)
def retry_page_route(edition_id: str, page_num: int):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    status = editions_lib.get_page_status(config, edition, date, page_num)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no page {page_num} for edition {edition_id!r}")
    if jobs.get_active_job_for_edition(edition, date) is not None:
        raise HTTPException(status_code=409, detail=f"an extraction is already running for {edition_id!r}")

    jobs.retry_page_sync(config, edition, date, page_num)

    new_status = editions_lib.get_page_status(config, edition, date, page_num)
    if new_status == "done":
        return pages_lib.get_page(config, edition, date, page_num)
    return PageOut(
        page_num=page_num, status=new_status or "pending", width=None, height=None, line_count=None,
        article_count=0, validation_ok=False, coverage_ratio=None,
        error=editions_lib.get_page_error(config, edition, date, page_num),
    )


@app.post("/api/editions/{edition_id}/retry-failed", response_model=StartJobOut)
async def retry_failed_pages_route(edition_id: str):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    if jobs.get_active_job_for_edition(edition, date) is not None:
        raise HTTPException(status_code=409, detail=f"an extraction is already running for {edition_id!r}")
    failed_pages = editions_lib.get_failed_pages(config, edition, date)
    if not failed_pages:
        raise HTTPException(status_code=404, detail=f"no failed pages for edition {edition_id!r}")

    job_id = jobs.start_retry_job(edition, date, failed_pages, config)
    return StartJobOut(job_id=job_id, edition=edition, date=date)


@app.delete("/api/editions/{edition_id}", response_model=DeleteEditionOut)
async def delete_edition_route(edition_id: str):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    if editions_lib.get_total_page_count(config, edition, date) is None:
        raise HTTPException(status_code=404, detail=f"no edition {edition_id!r}")
    if jobs.get_active_job_for_edition(edition, date) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"an extraction is currently running for {edition_id!r} - wait for it to finish or fail before deleting",
        )

    result = delete_edition(config, edition, date)
    return DeleteEditionOut(edition=result.edition, date=result.date, bytes_freed=result.bytes_freed)


# --- Step D: pipeline monitoring ("Pipeline" view) --------------------------


@app.get("/api/runs", response_model=list[RunSummaryOut])
async def list_runs_route():
    return runs_lib.list_runs(config)


@app.get("/api/runs/{run_id}", response_model=RunDetailOut)
async def get_run_route(run_id: str):
    run = runs_lib.get_run(config, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return run


@app.get("/api/runs/{run_id}/pages/{page_num}", response_model=list[StageEventOut])
async def get_run_page_route(run_id: str, page_num: int):
    events = runs_lib.get_page_stages(config, run_id, page_num)
    if not events:
        raise HTTPException(status_code=404, detail=f"no stage events for run {run_id!r} page {page_num}")
    return events


@app.get("/api/runs/{run_id}/pages/{page_num}/raw", response_model=PageRawOut)
async def get_run_page_raw_route(run_id: str, page_num: int):
    raw = runs_lib.get_page_raw(config, run_id, page_num)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"no raw Gemini exchange for run {run_id!r} page {page_num}")
    return raw


@app.get("/api/quota", response_model=QuotaOut)
async def get_quota_route():
    return runs_lib.get_quota(config)


# --- Summaries: edition-wide importance ranking -----------------------------


@app.get("/api/editions/{edition_id}/ranking", response_model=RankingOut)
async def get_ranking_route(edition_id: str):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = ranking_lib.read_ranking(config, edition, date)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no ranking computed yet for edition {edition_id!r}")
    return result


# Plain `def`, not `async def`: rank_edition makes a synchronous, possibly
# slow (HIGH thinking, edition-wide) Gemini call - FastAPI runs a sync path
# operation in a worker thread automatically, so this doesn't block the
# event loop the way an async def calling blocking code would.
@app.post("/api/editions/{edition_id}/ranking", response_model=RankingOut)
def trigger_ranking_route(edition_id: str, no_cache: bool = False):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    detail = editions_lib.get_edition_detail(config, edition, date)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no edition {edition_id!r}")

    tracer = RunTracer(db_path=config.trace_db, run_id=new_run_id())
    tracer.start_run(edition, date, None, detail.page_count)
    try:
        ranking_lib.process_edition_ranking(config, edition, date, use_cache=not no_cache, tracer=tracer)
        tracer.finish_run("done")
    except Exception as e:  # noqa: BLE001 - report the failure, don't crash the request
        tracer.finish_run("failed")
        raise HTTPException(status_code=502, detail=f"ranking failed: {e}")

    return ranking_lib.read_ranking(config, edition, date)


# --- Serving the built frontend ---------------------------------------------
#
# Registered last, deliberately: every /api/* route above is a literal-
# prefix match Starlette resolves before ever reaching this catch-all, so
# there's no risk of this shadowing a real API route. Not present at all
# when running the API alone in dev (frontend/dist doesn't exist until
# `npm run build` has been run - see vite.config.ts's proxy for that case).
#
# StaticFiles(html=True) on its own does NOT do this: it only serves
# index.html for a request that resolves to a *directory* (e.g. "/"), and
# 404s on any other unmatched path - verified against Starlette's
# StaticFiles.get_response. A client-side route like /pipeline or
# /reader/<id>/<page> has no file on disk at all, so a direct navigation
# or a page refresh on one of those would 404 without the explicit
# fallback-to-index.html below. Verified against a real, locally-running
# instance of this app (no Docker available in this environment - see
# README "Deploying"): direct GET of /pipeline and /reader/<id>/<page>
# both return the SPA shell (200), and /assets/* and /favicon.svg serve
# correctly. _FRONTEND_DIST is set above, alongside `config`.
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def spa_route(full_path: str):
        # An unmatched /api/* path is a real 404 (a bad endpoint, not a
        # client-side route) - falling through to index.html here would
        # silently mask it as a 200 HTML response instead. Verified live:
        # this exact gap let GET /api/nonexistent return 200 before this
        # check was added.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail=f"no such route: /{full_path}")
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
