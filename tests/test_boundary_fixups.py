"""Offline tests for the deterministic drop-cap boundary fixup - see
boundary_fixups.py. Fixtures mirror the real geometry found live on pages
1, 15, and 16 of docs/Newspaper.pdf (drop-cap bbox top within ~9pt of the
continuation line's top, right edge within ~2pt of the continuation line's
left edge)."""
from __future__ import annotations

from hindu_extract.boundary_fixups import extend_body_ranges_for_drop_caps
from hindu_extract.models import FontProfile, Line, LineFlags


def make_line(line_no, text, bbox, size=9.0, single_glyph=False, size_outlier=False):
    return Line(
        line_no=line_no,
        page_num=1,
        text=text,
        bbox=bbox,
        font_profile=FontProfile(name="Body-Regular", size=size, is_bold=False, is_italic=False, mixed=False),
        stream_start=line_no,
        stream_end=line_no,
        flags=LineFlags(single_glyph=single_glyph, size_outlier=size_outlier, ends_with_hyphen=False),
    )


# Real page-15 geometry: drop cap "B" at (28.35, 620.45, 56.31, 657.48),
# continuation "eyond the opening" at (58.34, 622.31, 132.95, 631.27).
DROP_CAP = make_line(118, "B", (28.35, 620.45, 56.31, 657.48), size=37.0, single_glyph=True, size_outlier=True)
CONTINUATION = make_line(119, "eyond the opening", (58.34, 622.31, 132.95, 631.27))
NEXT_LINE = make_line(120, "pair of batters", (28.35, 641.0, 132.95, 650.0))


def test_extends_body_start_when_drop_cap_is_geometrically_adjacent():
    lines = [DROP_CAP, CONTINUATION, NEXT_LINE]
    parsed = {"articles": [{"article_id": "a1", "body": {"start": 119, "end": 120}}]}

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert parsed["articles"][0]["body"]["start"] == 118
    assert len(fixups) == 1
    assert fixups[0].article_id == "a1"
    assert fixups[0].original_start == 119
    assert fixups[0].corrected_start == 118


def test_does_not_extend_when_preceding_line_is_not_single_glyph():
    ordinary = make_line(118, "Sports Bureau", (28.35, 600.0, 132.95, 609.0))
    lines = [ordinary, CONTINUATION, NEXT_LINE]
    parsed = {"articles": [{"article_id": "a1", "body": {"start": 119, "end": 120}}]}

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert parsed["articles"][0]["body"]["start"] == 119
    assert fixups == []


def test_does_not_extend_when_preceding_line_is_single_glyph_but_far_away():
    # A single_glyph/size_outlier line exists at start-1, but it's a
    # headline initial cap on a completely different part of the page, not
    # geometrically touching this body's start line - numeric adjacency
    # alone must never be trusted (see design/DESIGN.md "ID ORDERING").
    far_away_cap = make_line(118, "X", (500.0, 50.0, 530.0, 90.0), size=37.0, single_glyph=True, size_outlier=True)
    lines = [far_away_cap, CONTINUATION, NEXT_LINE]
    parsed = {"articles": [{"article_id": "a1", "body": {"start": 119, "end": 120}}]}

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert parsed["articles"][0]["body"]["start"] == 119
    assert fixups == []


def test_does_not_extend_when_candidate_is_to_the_right_not_left():
    # single_glyph/size_outlier but positioned to the RIGHT of the
    # continuation line - not a drop cap this line continues from.
    to_the_right = make_line(118, "X", (200.0, 620.0, 230.0, 657.0), size=37.0, single_glyph=True, size_outlier=True)
    lines = [to_the_right, CONTINUATION, NEXT_LINE]
    parsed = {"articles": [{"article_id": "a1", "body": {"start": 119, "end": 120}}]}

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert parsed["articles"][0]["body"]["start"] == 119
    assert fixups == []


def test_leaves_correctly_placed_body_range_untouched():
    # start line IS the drop cap itself already - nothing to extend.
    lines = [DROP_CAP, CONTINUATION, NEXT_LINE]
    parsed = {"articles": [{"article_id": "a1", "body": {"start": 118, "end": 120}}]}

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert parsed["articles"][0]["body"]["start"] == 118
    assert fixups == []


def test_handles_multiple_articles_independently():
    lines = [DROP_CAP, CONTINUATION, NEXT_LINE]
    parsed = {
        "articles": [
            {"article_id": "a1", "body": {"start": 119, "end": 120}},
            {"article_id": "a2", "body": None},
            {"article_id": "a3", "headline": {"start": 1, "end": 1}},
        ]
    }

    fixups = extend_body_ranges_for_drop_caps(parsed, lines)

    assert len(fixups) == 1
    assert fixups[0].article_id == "a1"
