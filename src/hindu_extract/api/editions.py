"""Read-only helpers over the gold layer for the /api/editions* endpoints.
Thin by design: no computation here that isn't a direct read or a cheap
aggregation of files pipeline code already wrote.
"""
from __future__ import annotations

import json

from hindu_extract import storage
from hindu_extract import trace
from hindu_extract.api import jobs as jobs_lib
from hindu_extract.api.edition_id import make_edition_id
from hindu_extract.api.schemas import EditionDetailOut, EditionSummaryOut, PageStatusOut
from hindu_extract.articles_pipeline import gold_edition_dir
from hindu_extract.config import Config


def _iter_edition_date_dirs(config: Config):
    gold_root = config.gold_root
    if not gold_root.exists():
        return
    for edition_dir in sorted(gold_root.iterdir()):
        if not edition_dir.is_dir():
            continue
        for date_dir in sorted(edition_dir.iterdir()):
            if date_dir.is_dir():
                yield edition_dir.name, date_dir.name


def _page_article_counts(config: Config, edition: str, date: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    edition_dir = gold_edition_dir(config, edition, date)
    for page_dir in sorted(edition_dir.glob("page_*")):
        articles_path = page_dir / "articles.json"
        if not articles_path.exists():
            continue
        page_num = int(page_dir.name.split("_")[1])
        gold = json.loads(articles_path.read_text(encoding="utf-8"))
        counts[page_num] = len(gold.get("articles") or [])
    return counts


def _has_gold(config: Config, edition: str, date: str, page_num: int) -> bool:
    return (gold_edition_dir(config, edition, date) / f"page_{page_num:02d}" / "articles.json").exists()


def get_total_page_count(config: Config, edition: str, date: str) -> int | None:
    """The edition's full page count, independent of how much of it has
    been processed yet - preferring the live job (most current) over the
    Phase 1 manifest (written once Phase 1 finishes) over the gold count
    (best-effort fallback for data extracted before manifest.json existed).
    """
    active_job = jobs_lib.get_active_job_for_edition(edition, date)
    if active_job is not None and active_job.pages_total:
        return active_job.pages_total
    manifest = storage.read_manifest(config, edition, date)
    if manifest is not None:
        return manifest.get("page_count")
    counts = _page_article_counts(config, edition, date)
    return len(counts) if counts else None


def get_page_status(config: Config, edition: str, date: str, page_num: int) -> str | None:
    """pending|in_progress|done|failed, or None if page_num is out of
    range (or the edition doesn't exist at all) - the single source of
    truth reused by the page/article routes and by get_edition_detail's
    per-page list, so "is this page ready yet" is answered the same way
    everywhere. See the plan's "Enhancement 2" for the two-source design:
    prefer a live job's per-page state; fall back to disk otherwise."""
    active_job = jobs_lib.get_active_job_for_edition(edition, date)
    if active_job is not None:
        if page_num < 1 or page_num > active_job.pages_total:
            return None
        phase = next((p for p in active_job.per_page if p.page_num == page_num), None)
        if phase is None:
            return "pending"
        if phase.status in ("extracting", "grouping"):
            return "in_progress"
        return phase.status  # pending|done|failed

    total = get_total_page_count(config, edition, date)
    if total is None or page_num < 1 or page_num > total:
        return None
    return "done" if _has_gold(config, edition, date, page_num) else "pending"


def list_editions(config: Config) -> list[EditionSummaryOut]:
    summaries = []
    for edition, date in _iter_edition_date_dirs(config):
        counts = _page_article_counts(config, edition, date)
        if not counts:
            continue
        latest_run = trace.get_latest_run_for_edition(config.trace_db, edition, date)
        summaries.append(
            EditionSummaryOut(
                edition_id=make_edition_id(edition, date),
                edition=edition,
                date=date,
                page_count=len(counts),
                article_count=sum(counts.values()),
                extracted_at=latest_run["started_at"] if latest_run else None,
                status=latest_run["status"] if latest_run else None,
            )
        )
    summaries.sort(key=lambda s: s.extracted_at or "", reverse=True)
    return summaries


def get_edition_detail(config: Config, edition: str, date: str) -> EditionDetailOut | None:
    counts = _page_article_counts(config, edition, date)
    if not counts:
        return None
    zero_pages = sorted(page for page, count in counts.items() if count == 0)
    latest_run = trace.get_latest_run_for_edition(config.trace_db, edition, date)
    total = get_total_page_count(config, edition, date) or len(counts)
    pages = [
        PageStatusOut(page_num=n, status=get_page_status(config, edition, date, n) or "pending")
        for n in range(1, total + 1)
    ]
    return EditionDetailOut(
        edition_id=make_edition_id(edition, date),
        edition=edition,
        date=date,
        page_count=len(counts),
        article_count=sum(counts.values()),
        extracted_at=latest_run["started_at"] if latest_run else None,
        status=latest_run["status"] if latest_run else None,
        pages_with_articles=len(counts) - len(zero_pages),
        pages_with_zero_articles=zero_pages,
        pages=pages,
    )
