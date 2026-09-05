"""Deterministic geometric corrections to the model's claimed line-number
boundaries, applied before validation.

Found live across the first full 18-page run: on two pages, the model's
body range started one line AFTER the paragraph's actual drop-cap line, so
the real assembled text was missing its first letter entirely ("eyond the
opening..." instead of "Beyond the opening..."). A drop-cap glyph is always
geometrically adjacent to the first line of the paragraph it belongs to -
same y-band, its right edge touching that line's left edge - which is a
fact about the PDF's layout, not something the model needs to infer. This
mirrors Phase 1's drop-cap FUSION (assemble._fuse_drop_cap): that was
already made deterministic rather than model-driven, and boundary
placement is the other half of the same problem.

Requiring BOTH the numeric adjacency (drop-cap candidate is exactly one
line before the claimed start - drop caps are always stream-adjacent to
their continuation, see design/DESIGN.md "Stream-order rebuild") AND the
independent geometric bbox check is what makes this a correction rather
than a guess: numeric adjacency alone is exactly what the system prompt
tells the model NOT to trust, so it isn't trusted here either.
"""
from __future__ import annotations

from dataclasses import dataclass

from hindu_extract.models import Line

# How far a drop-cap's own bbox may sit from the following line's bbox and
# still count as "the same visual line start" - calibrated on the three
# real drop caps in docs/Newspaper.pdf (pages 1, 15, 16): observed top
# offset up to ~9.2pt and horizontal gap up to ~2.1pt, both comfortably
# inside a body line's own font size (~9pt), while still far short of a
# genuinely different line/column.
_Y_BAND_TOLERANCE_RATIO = 2.0
_HORIZONTAL_GAP_RATIO = 1.5


@dataclass(frozen=True)
class BoundaryFixup:
    article_id: str
    field: str
    original_start: int
    corrected_start: int

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "field": self.field,
            "original_start": self.original_start,
            "corrected_start": self.corrected_start,
        }


def _is_drop_cap_adjacent(candidate: Line, start_line: Line) -> bool:
    """True if `candidate` is a drop-cap glyph sitting immediately to the
    left of, and vertically level with, `start_line` - i.e. `candidate` is
    the drop-cap `start_line` visually continues, purely from geometry."""
    if not (candidate.flags.single_glyph and candidate.flags.size_outlier):
        return False
    size = start_line.font_profile.size or 9.0
    same_y_band = abs(candidate.bbox[1] - start_line.bbox[1]) <= size * _Y_BAND_TOLERANCE_RATIO
    immediately_left = candidate.bbox[0] < start_line.bbox[0]
    touching = abs(candidate.bbox[2] - start_line.bbox[0]) <= size * _HORIZONTAL_GAP_RATIO
    return same_y_band and immediately_left and touching


def extend_body_ranges_for_drop_caps(parsed: dict, lines: list[Line]) -> list[BoundaryFixup]:
    """Mutates parsed["articles"][*]["body"]["start"] in place, extending a
    body range back by one line whenever the line immediately before its
    claimed start is a geometrically-adjacent drop cap. Returns the list of
    corrections made, for an audit trail in the gold JSON (mirroring
    assemble.py's dehyphenation_log) - so a correction is always visible,
    never silent."""
    lines_by_no = {line.line_no: line for line in lines}
    fixups: list[BoundaryFixup] = []
    for article in parsed.get("articles") or []:
        body = article.get("body")
        if not body:
            continue
        start = body.get("start")
        if start is None:
            continue
        candidate = lines_by_no.get(start - 1)
        start_line = lines_by_no.get(start)
        if candidate is None or start_line is None:
            continue
        if _is_drop_cap_adjacent(candidate, start_line):
            fixups.append(
                BoundaryFixup(
                    article_id=article.get("article_id", "?"),
                    field="body",
                    original_start=start,
                    corrected_start=start - 1,
                )
            )
            body["start"] = start - 1
    return fixups
