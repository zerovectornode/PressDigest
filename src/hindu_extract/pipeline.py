"""Orchestrates Phase 1 extraction for one or more pages: cache lookup,
stream-order line-building, geometric canary, and writing into the bronze
layer. Every decision point in here is either a cache hit/miss or an I/O
step - no content interpretation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pdfplumber

from hindu_extract import cache, storage
from hindu_extract.canary import check_page
from hindu_extract.config import Config
from hindu_extract.lines import build_page
from hindu_extract.models import CanaryFinding, FontInfo, PageMetadata
from hindu_extract.trace import RunTracer


class EmptyPageError(RuntimeError):
    """Raised when a page yields zero lines - almost certainly an extraction
    failure (e.g. an image-heavy page with no text layer at all) rather than
    a genuine blank page, and must never pass silently."""


@dataclass
class PageOutcome:
    page_num: int
    metadata: PageMetadata
    line_count: int
    canary_findings: list[CanaryFinding]
    from_cache: bool


def _process_one_page(
    pdf,
    pdf_hash: str,
    version_hash: str,
    page_num: int,
    config: Config,
    force: bool,
    tracer: RunTracer | None = None,
) -> tuple[dict, bool]:
    cache_dir = cache.cache_dir_for(config, pdf_hash, version_hash, page_num)
    if not force and cache.is_cache_complete(cache_dir):
        if tracer is not None:
            for stage_name in ("char_extraction", "line_building", "ligature_canary"):
                with tracer.stage(page_num, stage_name) as detail:
                    detail["cache_hit"] = True
        return cache.read_cache(cache_dir), True

    page = pdf.pages[page_num - 1]

    if tracer is None:
        chars = list(page.chars)
        metadata, lines, word_space_log = build_page(page, page_num, config)
        findings = check_page(page, page_num, metadata.modal_font_size, config)
    else:
        with tracer.stage(page_num, "char_extraction") as detail:
            chars = list(page.chars)
            detail["char_count"] = len(chars)
        with tracer.stage(page_num, "line_building") as detail:
            # build_page re-derives page.chars internally, but pdfplumber
            # caches the underlying extraction so this costs nothing extra -
            # see module docstring for why char_extraction/line_building are
            # timed as two stages without splitting lines.build_page itself.
            metadata, lines, word_space_log = build_page(page, page_num, config)
            detail["line_count"] = len(lines)
            detail["single_glyph_lines"] = sum(1 for l in lines if l.flags.single_glyph)
            detail["size_outlier_lines"] = sum(1 for l in lines if l.flags.size_outlier)
            detail["word_space_insertions"] = len(word_space_log)
        with tracer.stage(page_num, "ligature_canary") as detail:
            findings = check_page(page, page_num, metadata.modal_font_size, config)
            detail["finding_count"] = len(findings)
            detail["findings"] = [f.to_dict() for f in findings]

    page_result_dict = {
        "metadata": metadata.to_dict(),
        "lines": [line.to_dict() for line in lines],
        "canary_findings": [f.to_dict() for f in findings],
        "word_space_log": [w.to_dict() for w in word_space_log],
    }
    cache.write_cache(cache_dir, page_result_dict)
    return page_result_dict, False


def extract_pages(
    pdf_path: Path,
    edition: str,
    date: str,
    page_nums: list[int],
    config: Config,
    force: bool = False,
    progress_callback: Callable[[PageOutcome], None] | None = None,
    tracer: RunTracer | None = None,
) -> list[PageOutcome]:
    """progress_callback, if given, is invoked once per page immediately
    after that page's outcome is computed - purely additive (default None
    reproduces prior behavior exactly) so a caller like the API's
    background job runner can report incremental progress without this
    function needing to know anything about jobs. tracer, if given, records
    a char_extraction/line_building/ligature_canary stage event per page -
    see trace.py."""
    pdf_bytes = Path(pdf_path).read_bytes()
    pdf_hash = cache.hash_bytes(pdf_bytes)
    version_hash = cache.hash_text(config.pipeline_version)

    outcomes: list[PageOutcome] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in page_nums:
            page_dict, from_cache = _process_one_page(
                pdf, pdf_hash, version_hash, page_num, config, force, tracer=tracer
            )
            cache_dir = cache.cache_dir_for(config, pdf_hash, version_hash, page_num)
            dest_dir = storage.bronze_page_dir(config, edition, date, page_num)
            cache.copy_cache_to(cache_dir, dest_dir)

            line_count = page_dict["metadata"]["line_count"]
            if line_count == 0:
                raise EmptyPageError(
                    f"page {page_num} produced zero lines - likely an extraction "
                    f"failure, not a genuine blank page"
                )

            findings = [CanaryFinding(**f) for f in page_dict["canary_findings"]]
            metadata = PageMetadata(
                page_num=page_dict["metadata"]["page_num"],
                width=page_dict["metadata"]["width"],
                height=page_dict["metadata"]["height"],
                modal_font_size=page_dict["metadata"]["modal_font_size"],
                fonts=tuple(FontInfo(**f) for f in page_dict["metadata"]["fonts"]),
                char_count=page_dict["metadata"]["char_count"],
                line_count=line_count,
            )
            outcomes.append(
                PageOutcome(
                    page_num=page_num,
                    metadata=metadata,
                    line_count=line_count,
                    canary_findings=findings,
                    from_cache=from_cache,
                )
            )
            if progress_callback is not None:
                progress_callback(outcomes[-1])

    manifest = {
        "edition": edition,
        "date": date,
        "pdf_hash": pdf_hash,
        "pipeline_version": config.pipeline_version,
        "page_count": len(outcomes),
        "pages": [
            {
                "page_num": o.page_num,
                "line_count": o.line_count,
                "char_count": o.metadata.char_count,
                "canary_finding_count": len(o.canary_findings),
            }
            for o in outcomes
        ],
    }
    storage.write_manifest(config, edition, date, manifest)

    return outcomes
