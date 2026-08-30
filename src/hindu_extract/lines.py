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
from hindu_extract.models import FontProfile, Line, LineFlags, PageMetadata, WordSpaceInsertion


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


def _join_group_text(
    group: list[dict], word_space_gap_ratio: float, page_num: int, line_no: int
) -> tuple[str, str, list[WordSpaceInsertion]]:
    """Concatenates one line's characters exactly as before (the return
    value's whitespace-stripped content is unchanged - see verify.py), but
    also computes a second, corrected variant with a synthetic ASCII space
    inserted between two adjacent alphabetic characters wherever the gap
    between them is wide enough to be a real word-space that this PDF
    simply never encoded as a literal space glyph (see config/default.yaml
    word_space_gap_ratio for the calibration). Restricted to alphabetic
    pairs on both sides: this is what excludes punctuation/digit-heavy runs
    (stock-ticker leader dots, decorative dividers) without needing a
    separate magnitude cap - see the calibration note in config/default.yaml.

    Every insertion is logged - see design/DESIGN.md "Word-space gap fix".
    """
    literal_parts: list[str] = []
    corrected_parts: list[str] = []
    insertions: list[WordSpaceInsertion] = []
    for i, c in enumerate(group):
        if i > 0:
            prev = group[i - 1]
            if prev["text"].isalpha() and c["text"].isalpha():
                size = prev.get("size") or c.get("size") or 0
                if size:
                    gap = c["x0"] - prev["x1"]
                    ratio = gap / size
                    if ratio > word_space_gap_ratio:
                        corrected_parts.append(" ")
                        insertions.append(
                            WordSpaceInsertion(
                                page_num=page_num,
                                line_no=line_no,
                                position=prev["stream_index"],
                                char_before=prev["text"],
                                char_after=c["text"],
                                gap=round(gap, 3),
                                ratio=round(ratio, 3),
                            )
                        )
        literal_parts.append(c["text"])
        corrected_parts.append(c["text"])
    return "".join(literal_parts), "".join(corrected_parts), insertions


def build_page(page, page_num: int, config: Config) -> tuple[PageMetadata, list[Line], list[WordSpaceInsertion]]:
    raw_chars = list(page.chars)
    chars = [dict(c, stream_index=i) for i, c in enumerate(raw_chars)]
    modal = modal_font_size(chars)

    thresholds = config.thresholds
    row_tol = thresholds.row_band_tolerance_ratio * modal if modal else 0.0
    gap_ratio = thresholds.span_break_gap_ratio
    outlier_threshold = thresholds.size_outlier_ratio * modal if modal else float("inf")

    char_groups = _group_lines(chars, row_tol, gap_ratio, outlier_threshold)
    word_space_ratio = thresholds.word_space_gap_ratio

    lines: list[Line] = []
    word_space_log: list[WordSpaceInsertion] = []
    for line_no, group in enumerate(char_groups, start=1):
        text, corrected_text, insertions = _join_group_text(group, word_space_ratio, page_num, line_no)
        word_space_log.extend(insertions)
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
                corrected_text=corrected_text,
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
    return metadata, lines, word_space_log
