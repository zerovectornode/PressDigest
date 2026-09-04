"""Pydantic response/request models for the API. These are the source of
truth TS types are generated from - see scripts/generate_types.py.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

JobStatusValue = Literal["queued", "running", "done", "failed"]
PagePhaseValue = Literal["pending", "extracting", "grouping", "done", "failed"]
PageStatusValue = Literal["pending", "in_progress", "done", "failed"]
RunStatusValue = Literal["running", "done", "failed"]


class ParsedMetadataOut(BaseModel):
    edition: str | None
    date: str | None


class PagePhaseOut(BaseModel):
    page_num: int
    status: PagePhaseValue
    current_stage: str | None = None
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
    elapsed_s: float
    eta_s: float | None = None


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
    extracted_at: str | None = None
    status: RunStatusValue | None = None


class PageStatusOut(BaseModel):
    page_num: int
    status: PageStatusValue


class EditionDetailOut(EditionSummaryOut):
    pages_with_articles: int
    pages_with_zero_articles: list[int]
    pages: list[PageStatusOut]


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
    status: PageStatusValue
    width: float | None
    height: float | None
    line_count: int | None
    article_count: int
    validation_ok: bool
    coverage_ratio: float | None


class PageArticlesOut(BaseModel):
    status: PageStatusValue
    articles: list[ArticleOut]


# --- Step D: pipeline monitoring ("Pipeline" view) --------------------------


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


# --- Summaries: edition-wide importance ranking -----------------------------

ExclusionRiskValue = Literal["none", "possible_opinion", "possible_promotional"]


class RankedArticleOut(BaseModel):
    article_id: str
    page: int
    headline: str
    rank: int
    importance_score: int
    category: str
    why_it_matters: str
    exclusion_risk: ExclusionRiskValue


class DuplicateContinuationOut(BaseModel):
    first_part_id: str
    first_part_page: int
    continues_on_page: int
    conflicting_id: str


ExclusionReasonCodeValue = Literal[
    "PROMOTIONAL",
    "OPINION_WITHOUT_ANALYSIS",
    "ENTERTAINMENT",
    "LOCAL_WITHOUT_BROADER_RELEVANCE",
    "ROUTINE_STATEMENT",
    "CONTINUATION_OF_EARLIER_ARTICLE",
    "BELOW_THRESHOLD",
    "OTHER",
]


class ExcludedArticleOut(BaseModel):
    article_id: str
    page: int
    headline: str
    reason_code: ExclusionReasonCodeValue
    note: str


class RankingOut(BaseModel):
    generated_at: str
    top_n: int
    ranked: list[RankedArticleOut]
    excluded: list[ExcludedArticleOut]
    validation_ok: bool
    validation_issues: list[str]
    duplicate_continuations: list[DuplicateContinuationOut]
    retried: bool
    eligible_count_note: str | None
    total_tokens: int
    all_cached: bool
