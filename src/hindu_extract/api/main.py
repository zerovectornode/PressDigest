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
    ArticleOut,
    EditionDetailOut,
    EditionSummaryOut,
    JobStatusOut,
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
from hindu_extract.storage import raw_pdf_path
from hindu_extract.trace import RunTracer, new_run_id

config = load_config()
_FRONTEND_DIST = config.project_root / "frontend" / "dist"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_logger = logging.getLogger("hindu_extract.startup")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Runs once per container boot. This deployment has no local Docker
    verification (see design/DESIGN.md/README "Deploying") - iterating on
    a misconfigured environment happens entirely against HF's build/runtime
    logs, so a silent path/permission problem (data dir not writable, a
    dependency missing its expected version) needs to be visible here
    rather than surfacing later as an opaque 500 on first upload."""
    data_dir = config.project_root / "data"
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
    _logger.info("project_root=%s", config.project_root)
    _logger.info("data_dir=%s writable=%s", data_dir, writable)
    _logger.info("frontend_dist=%s exists=%s", _FRONTEND_DIST, _FRONTEND_DIST.is_dir())
    _logger.info("packages: %s", ", ".join(versions))
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
    staging_dir = config.project_root / "data" / "uploads"
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


@app.get("/api/jobs/{job_id}", response_model=JobStatusOut)
async def get_job_status(job_id: str):
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id!r}")
    return JobStatusOut(
        job_id=record.job_id,
        edition=record.edition,
        date=record.date,
        status=record.status,
        pages_done=record.pages_done,
        pages_total=record.pages_total,
        per_page=[PagePhaseOut(**vars(p)) for p in record.per_page],
        all_cached=record.all_cached,
        error=record.error,
    )


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
    page = pages_lib.get_page(config, edition, date, page_num)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no page {page_num} for edition {edition_id!r}")
    return page


@app.get("/api/editions/{edition_id}/pages/{page_num}/articles", response_model=list[ArticleOut])
async def get_page_articles_route(edition_id: str, page_num: int):
    try:
        edition, date = split_edition_id(edition_id)
    except InvalidEditionId as e:
        raise HTTPException(status_code=400, detail=str(e))
    articles = pages_lib.get_page_articles(config, edition, date, page_num)
    if articles is None:
        raise HTTPException(status_code=404, detail=f"no gold articles for page {page_num} of edition {edition_id!r}")
    return articles


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
