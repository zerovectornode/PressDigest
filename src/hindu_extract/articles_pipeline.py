"""Orchestrates Phase 2 + Phase 3 for one or more pages: load Phase 1's
bronze lines, call Gemini (cached), validate the claimed boundaries, and
assemble gold-layer articles + markdown.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from hindu_extract.assemble import AssembledArticle, assemble_articles
from hindu_extract.config import Config
from hindu_extract.grouping import group_page
from hindu_extract.markdown_render import render_edition_markdown, render_page_markdown
from hindu_extract.models import FontProfile, Line, LineFlags
from hindu_extract.phase3 import ValidationResult
from hindu_extract.rate_limit import TokenAwareLimiter
from hindu_extract.storage import bronze_page_dir
from hindu_extract.trace import RunTracer


def _issues_by_article(validation: ValidationResult) -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}
    for m in validation.checksum_mismatches:
        issues.setdefault(m.article_id, []).append(f"checksum[{m.field}]: {m.detail}")
    for c in validation.contiguity_issues:
        issues.setdefault(c.article_id, []).append(f"contiguity: {c.detail}")
    for o in validation.overlap_issues:
        issues.setdefault(o.article_id_a, []).append(
            f"body range overlaps {o.article_id_b}: {o.range_a} vs {o.range_b}"
        )
        issues.setdefault(o.article_id_b, []).append(
            f"body range overlaps {o.article_id_a}: {o.range_b} vs {o.range_a}"
        )
    for h in validation.headline_quality_issues:
        issues.setdefault(h.article_id, []).append(f"headline_quality: {h.detail}")
    return issues


def _line_from_dict(d: dict) -> Line:
    return Line(
        line_no=d["line_no"],
        page_num=d["page_num"],
        text=d["text"],
        bbox=tuple(d["bbox"]),
        font_profile=FontProfile(**d["font_profile"]),
        stream_start=d["stream_start"],
        stream_end=d["stream_end"],
        flags=LineFlags(**d["flags"]),
    )


@dataclass
class PageArticlesOutcome:
    page_num: int
    articles: list[AssembledArticle]
    excluded_line_nos: list[int]
    validation_ok: bool
    total_tokens: int
    all_cached: bool
    coverage_ratio: float
    join_log: list = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return any(a.needs_review for a in self.articles)


def gold_page_dir(config: Config, edition: str, date: str, page_num: int) -> Path:
    return config.gold_root / edition / date / f"page_{page_num:02d}"


def gold_edition_dir(config: Config, edition: str, date: str) -> Path:
    return config.gold_root / edition / date


def process_page_articles(
    config: Config,
    edition: str,
    date: str,
    page_num: int,
    use_cache: bool = True,
    limiter: TokenAwareLimiter | None = None,
    tracer: RunTracer | None = None,
) -> PageArticlesOutcome:
    bronze_dir = bronze_page_dir(config, edition, date, page_num)
    page_data = json.loads((bronze_dir / "page.json").read_text(encoding="utf-8"))

    lines = [_line_from_dict(d) for d in page_data["lines"]]
    modal_font_size = page_data["metadata"]["modal_font_size"]

    outcome = group_page(
        lines, page_num, modal_font_size, config, use_cache=use_cache, limiter=limiter, tracer=tracer
    )
    issues = _issues_by_article(outcome.validation)

    stage_ctx = tracer.stage(page_num, "assembly") if tracer is not None else nullcontext({})
    with stage_ctx as detail:
        articles, join_log = assemble_articles(outcome.parsed, lines, page_num, issues_by_article=issues)
        lines_by_no = {line.line_no: line for line in lines}
        detail["articles_produced"] = len(articles)
        detail["dehyphenation_joins"] = len(join_log)
        # A drop-cap fusion happens once per single_glyph line referenced by
        # an assembled article - that's exactly the condition _fuse_drop_cap
        # (assemble.py) fires on, so counting referenced single_glyph lines
        # is an exact count without needing assemble.py to log it separately.
        detail["drop_cap_fusions"] = sum(
            1 for a in articles for n in a.line_nos if lines_by_no.get(n) and lines_by_no[n].flags.single_glyph
        )

    referenced_line_nos = {n for a in articles for n in a.line_nos}
    all_line_nos = {line.line_no for line in lines}
    excluded_line_nos = sorted(all_line_nos - referenced_line_nos)

    out_dir = gold_page_dir(config, edition, date, page_num)
    out_dir.mkdir(parents=True, exist_ok=True)

    gold = {
        "page_num": page_num,
        "articles": [a.to_dict() for a in articles],
        "excluded_line_nos": excluded_line_nos,
        "validation_ok": outcome.validation.ok,
        "boundary_fixups": [f.to_dict() for f in outcome.boundary_fixups],
        "coverage": {
            "total_lines": outcome.validation.coverage.total_lines,
            "covered_lines": outcome.validation.coverage.covered_lines,
            "coverage_ratio": outcome.validation.coverage.coverage_ratio,
        },
        "dehyphenation_log": [j.to_dict() for j in join_log],
    }
    (out_dir / "articles.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "page.md").write_text(render_page_markdown(page_num, articles), encoding="utf-8")

    return PageArticlesOutcome(
        page_num=page_num,
        articles=articles,
        excluded_line_nos=excluded_line_nos,
        validation_ok=outcome.validation.ok,
        total_tokens=outcome.total_tokens,
        all_cached=outcome.all_cached,
        coverage_ratio=outcome.validation.coverage.coverage_ratio,
        join_log=join_log,
    )


def process_edition_articles(
    config: Config,
    edition: str,
    date: str,
    page_nums: list[int],
    use_cache: bool = True,
    progress_callback: Callable[[PageArticlesOutcome], None] | None = None,
    error_callback: Callable[[int, Exception], None] | None = None,
    tracer: RunTracer | None = None,
) -> list[PageArticlesOutcome]:
    """Runs process_page_articles for every page in page_nums concurrently,
    gated by a TokenAwareLimiter sized from config.concurrency (see
    rate_limit.py and config/default.yaml "concurrency" - this replaces the
    strictly-sequential per-page loop the CLI and API job runner used to
    run, which left the real TPM/RPM headroom unused). One page's failure
    doesn't stop the others: if error_callback is given it's called with
    (page_num, exception) and that page is omitted from the returned list;
    otherwise the exception propagates.

    Results are returned in page_num order regardless of completion order.
    """
    limiter = TokenAwareLimiter(
        max_concurrent=config.concurrency.max_concurrent,
        requests_per_minute=config.concurrency.requests_per_minute,
        tokens_per_minute=config.concurrency.tokens_per_minute,
        estimated_tokens_per_request=config.concurrency.estimated_tokens_per_request,
    )

    def _run(page_num: int) -> tuple[int, PageArticlesOutcome | None]:
        try:
            outcome = process_page_articles(
                config, edition, date, page_num, use_cache=use_cache, limiter=limiter, tracer=tracer
            )
        except Exception as e:  # noqa: BLE001 - a single page's failure must not sink the whole edition
            if error_callback is None:
                raise
            error_callback(page_num, e)
            return page_num, None
        if progress_callback is not None:
            progress_callback(outcome)
        return page_num, outcome

    results: dict[int, PageArticlesOutcome] = {}
    with ThreadPoolExecutor(max_workers=config.concurrency.max_concurrent) as executor:
        for page_num, outcome in executor.map(_run, page_nums):
            if outcome is not None:
                results[page_num] = outcome

    return [results[n] for n in sorted(results)]


def write_edition_markdown(config: Config, edition: str, date: str, page_nums: list[int]) -> Path:
    """Skips any page whose gold JSON is missing (e.g. Phase 2 failed for
    that page) rather than crashing the whole edition's markdown over one
    bad page."""
    pages = []
    for page_num in sorted(page_nums):
        articles_path = gold_page_dir(config, edition, date, page_num) / "articles.json"
        if not articles_path.exists():
            continue
        gold = json.loads(articles_path.read_text(encoding="utf-8"))
        articles = [AssembledArticle(**a) for a in gold["articles"]]
        pages.append((page_num, articles))

    md = render_edition_markdown(edition, date, pages)
    edition_dir = gold_edition_dir(config, edition, date)
    edition_dir.mkdir(parents=True, exist_ok=True)
    out_path = edition_dir / "edition.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
