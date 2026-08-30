"""Builds Phase 1's line records by walking pdfplumber's raw char stream in
original order - never sorted, never re-clustered by geometry.

Why: verified empirically (design/DESIGN.md "Stream-order rebuild" - Step 1
diagnostic on docs/Newspaper.pdf pages 1 and 8) that newspaper layout
software emits each story as one contiguous run in the PDF content stream,
in true reading order, across all its columns. Grouping consecutive chars
in that native order - with no global sort by (top, x0) at all - reproduces
perfect, correctly-ordered prose directly, with none of the column-fusion
risk the previous geometry-sorted approach had to fight with calibrated
gap thresholds. The separator/gap-threshold logic itself is unchanged and
reused as-is (it was already validated); what changed is that it is now
applied to a single forward pass over the untouched stream instead of a
globally-sorted char list.
"""
from __future__ import annotations

from collections import Counter

from hindu_extract.config import Config
from hindu_extract.fonts import get_font_inventory
from hindu_extract.models import FontProfile, Line, LineFlags, PageMetadata


def modal_font_size(chars: list[dict]) -> float:
    sizes = Counter(round(c["size"], 1) for c in chars if c.get("size"))
    return sizes.most_common(1)[0][0] if sizes else 0.0


def is_bold(font_name: str, bold_markers: tuple[str, ...]) -> bool:
    lowered = font_name.lower()
    return any(marker in lowered for marker in bold_markers)


def is_italic(font_name: str, italic_markers: tuple[str, ...]) -> bool:
    lowered = font_name.lower()
    return any(marker in lowered for marker in italic_markers)


def _dominant_style(chars: list[dict]) -> tuple[str, float, bool]:
    non_space = [c for c in chars if c["text"] != " "] or chars
    counts = Counter((c["fontname"], round(c["size"], 2)) for c in non_space)
    (name, size), _ = counts.most_common(1)[0]
    return name, size, len(counts) > 1


def _group_lines(chars: list[dict], row_tol: float, gap_ratio: float, outlier_threshold: float) -> list[list[dict]]:
    """Single forward pass over chars in their given (stream) order. Two
    breaking rules, checked against only the immediately preceding char:

    1. An outlier-sized char (>= outlier_threshold, e.g. a drop-cap or
       headline) never merges with a non-outlier one, in either direction -
       this is what keeps a drop-cap isolated as its own line even though
       it sits stream-adjacent to ordinary body text (verified: without
       this rule, isolation would depend on the row/gap check happening to
       fail by coincidence, which is not a guarantee worth relying on for
       something this important - see design/DESIGN.md).
    2. Otherwise, the existing, already-validated same-row + gap check:
       same top (within row_tol) and a horizontal gap no larger than
       gap_ratio * font_size continues the current line; anything else
       starts a new one.
    """
    lines: list[list[dict]] = []
    current: list[dict] = []
    for c in chars:
        if not current:
            current = [c]
            continue
        prev = current[-1]
        prev_outlier = (prev.get("size") or 0) >= outlier_threshold
        cur_outlier = (c.get("size") or 0) >= outlier_threshold
        if prev_outlier != cur_outlier:
            lines.append(current)
            current = [c]
            continue

        same_row = abs(c["top"] - prev["top"]) <= row_tol
        if same_row:
            gap = c["x0"] - prev["x1"]
            size = prev.get("size") or c.get("size") or 1
            if gap <= gap_ratio * size:
                current.append(c)
                continue

        lines.append(current)
        current = [c]
    if current:
        lines.append(current)
    return lines


def build_page(page, page_num: int, config: Config) -> tuple[PageMetadata, list[Line]]:
    raw_chars = list(page.chars)
    chars = [dict(c, stream_index=i) for i, c in enumerate(raw_chars)]
    modal = modal_font_size(chars)

    thresholds = config.thresholds
    row_tol = thresholds.row_band_tolerance_ratio * modal if modal else 0.0
    gap_ratio = thresholds.span_break_gap_ratio
    outlier_threshold = thresholds.size_outlier_ratio * modal if modal else float("inf")

    char_groups = _group_lines(chars, row_tol, gap_ratio, outlier_threshold)

    lines: list[Line] = []
    for line_no, group in enumerate(char_groups, start=1):
        text = "".join(c["text"] for c in group)
        bbox = (
            min(c["x0"] for c in group),
            min(c["top"] for c in group),
            max(c["x1"] for c in group),
            max(c["bottom"] for c in group),
        )
        font_name, font_size, mixed = _dominant_style(group)
        char_count = len(group)
        outlier = (group[0].get("size") or 0) >= outlier_threshold

        lines.append(
            Line(
                line_no=line_no,
                page_num=page_num,
                text=text,
                bbox=bbox,
                font_profile=FontProfile(
                    name=font_name,
                    size=font_size,
                    is_bold=is_bold(font_name, config.style.bold_markers),
                    is_italic=is_italic(font_name, config.style.italic_markers),
                    mixed=mixed,
                ),
                stream_start=group[0]["stream_index"],
                stream_end=group[-1]["stream_index"],
                flags=LineFlags(
                    single_glyph=char_count == 1,
                    size_outlier=outlier,
                    ends_with_hyphen=text.rstrip().endswith("-"),
                ),
            )
        )

    metadata = PageMetadata(
        page_num=page_num,
        width=page.width,
        height=page.height,
        modal_font_size=modal,
        fonts=get_font_inventory(page, chars),
        char_count=len(chars),
        line_count=len(lines),
    )
    return metadata, lines
