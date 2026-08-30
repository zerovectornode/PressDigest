"""Pydantic response/request models for the API. These are the source of
truth TS types are generated from - see scripts/generate_types.py.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

JobStatusValue = Literal["queued", "running", "done", "failed"]
PagePhaseValue = Literal["pending", "extracting", "grouping", "done", "failed"]


class ParsedMetadataOut(BaseModel):
    edition: str | None
    date: str | None


class PagePhaseOut(BaseModel):
    page_num: int
    status: PagePhaseValue
    articles_found: int | None = None
    validation_ok: bool | None = None
    needs_review: bool | None = None
    cached: bool | None = None
    error: str | None = None


class JobStatusOut(BaseModel):
    job_id: str
    edition: str
    date: str
    status: JobStatusValue
    pages_done: int
    pages_total: int
    per_page: list[PagePhaseOut]
    all_cached: bool
    error: str | None = None


class StartJobOut(BaseModel):
    job_id: str
    edition: str
    date: str


class EditionSummaryOut(BaseModel):
    edition_id: str
    edition: str
    date: str
    page_count: int
    article_count: int


class EditionDetailOut(EditionSummaryOut):
    pages_with_articles: int
    pages_with_zero_articles: list[int]


class ArticleOut(BaseModel):
    article_id: str
    page: int
    section_kicker: str
    section_kicker_raw: str
    headline: str
    headline_raw: str
    deck: list[str]
    deck_raw: list[str]
    byline: str
    byline_raw: str
    dateline: str
    dateline_raw: str
    body: str
    body_raw: str
    captions: list[str]
    captions_raw: list[str]
    is_truncated: bool
    continues_on_page: int | None
    confidence: str
    rects: list[list[float]]
    validation_ok: bool
    needs_review: bool
    validation_issues: list[str]


class PageOut(BaseModel):
    page_num: int
    width: float
    height: float
    line_count: int
    article_count: int
    validation_ok: bool
    coverage_ratio: float | None


# --- Step D: pipeline monitoring ("Pipeline" view) --------------------------

RunStatusValue = Literal["running", "done", "failed"]


class RunSummaryOut(BaseModel):
    run_id: str
    edition: str
    date: str
    pdf_hash: str | None
    page_count: int
    started_at: str
    finished_at: str | None
    total_wall_clock_s: float | None
    total_tokens: int | None
    cache_hit_ratio: float | None
    status: RunStatusValue


class StageEventOut(BaseModel):
    page_num: int
    stage: str
    started_at: str
    ended_at: str
    duration_s: float
    detail: dict
    error: str | None


class RunDetailOut(RunSummaryOut):
    pages: list[int]


class PageRawOut(BaseModel):
    run_id: str
    page_num: int
    prompt: str
    raw_response: str
    recorded_at: str


class QuotaOut(BaseModel):
    requests_today: int
    requests_per_day_limit: int
    tokens_last_minute: int
    tokens_per_minute_limit: int
