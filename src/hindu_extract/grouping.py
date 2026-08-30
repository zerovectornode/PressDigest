"""Orchestrates one page's Phase 2 call + Phase 3 boundary validation.

No retry loop here (unlike the old unit-ID architecture): a checksum or
contiguity failure is a property of a specific field on a specific article,
not evidence the whole response is garbage, so it's flagged via
needs_review rather than re-prompted. See design/DESIGN.md "Stream-order
rebuild".
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass

from hindu_extract.boundary_fixups import BoundaryFixup, extend_body_ranges_for_drop_caps
from hindu_extract.config import Config
from hindu_extract.gemini_client import call_gemini
from hindu_extract.models import Line
from hindu_extract.phase3 import ValidationResult, validate_page
from hindu_extract.rate_limit import TokenAwareLimiter
from hindu_extract.trace import RunTracer


@dataclass
class GroupingOutcome:
    parsed: dict
    validation: ValidationResult
    usage: dict
    boundary_fixups: list[BoundaryFixup]

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_token_count") or 0

    @property
    def all_cached(self) -> bool:
        return bool(self.usage.get("cache_hit"))


def group_page(
    lines: list[Line],
    page_num: int,
    modal_font_size: float,
    config: Config,
    use_cache: bool = True,
    limiter: TokenAwareLimiter | None = None,
    tracer: RunTracer | None = None,
) -> GroupingOutcome:
    parsed, usage = call_gemini(
        lines, page_num, modal_font_size, config, use_cache=use_cache, limiter=limiter, tracer=tracer
    )

    # Deterministic geometric correction, applied before validation so the
    # checksum is checked against the corrected range - see
    # boundary_fixups.py. This is why a drop-cap boundary that used to fail
    # its start-word checksum now passes it outright: extending the range
    # to include the drop-cap line makes the model's own checksum correct.
    boundary_fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    stage_ctx = tracer.stage(page_num, "validation") if tracer is not None else nullcontext({})
    with stage_ctx as detail:
        validation = validate_page(lines, parsed)
        # Checksum mismatch detail is kept in full - per the instrumentation
        # spec this is "the most diagnostic signal we have" for a wrong
        # boundary, so it's stored verbatim rather than summarized.
        detail["ok"] = validation.ok
        detail["checksum_mismatches"] = [
            {"article_id": m.article_id, "field": m.field, "detail": m.detail} for m in validation.checksum_mismatches
        ]
        detail["contiguity_issues"] = [
            {"article_id": c.article_id, "line_no": c.line_no, "detail": c.detail} for c in validation.contiguity_issues
        ]
        detail["overlap_issues"] = [
            {
                "article_id_a": o.article_id_a,
                "article_id_b": o.article_id_b,
                "range_a": list(o.range_a),
                "range_b": list(o.range_b),
            }
            for o in validation.overlap_issues
        ]
        detail["coverage_ratio"] = validation.coverage.coverage_ratio if validation.coverage else None
        detail["boundary_fixups"] = [f.to_dict() for f in boundary_fixups]
        detail["headline_quality_issues"] = [
            {"article_id": h.article_id, "headline_text": h.headline_text, "detail": h.detail}
            for h in validation.headline_quality_issues
        ]

    return GroupingOutcome(parsed=parsed, validation=validation, usage=usage, boundary_fixups=boundary_fixups)
