"""Gold-layer assembly: slices Phase 1's stored lines by the line-number
ranges the model returned, and joins them into article text. The model
never contributes a single character of output text - every character in
every assembled field is a line that already existed in the bronze layer
before Gemini was called; the model only said which line numbers belong
where (see design/DESIGN.md "Stream-order rebuild").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hindu_extract.models import Line

Bbox = tuple[float, float, float, float]

FIELD_KEYS = {
    "headline": "headline",
    "deck": "deck",
    "byline": "byline",
    "dateline": "dateline",
    "body": "body",
    "captions": "caption",
}


class InvalidFusionError(RuntimeError):
    """Raised if assembly is about to fuse two lines directly (no space)
    for any reason other than the drop-cap rule (prev_line.flags.single_glyph).
    De-hyphenation (below) also removes the visible gap between two lines,
    but does so by consuming an actual hyphen character already present in
    the text - a different, independently-legitimate mechanism, not this
    one. This guards the drop-cap code path specifically against ever
    firing on a non-drop-cap line, e.g. from a future refactor.
    """


def _fuse_drop_cap(prev_line: Line, text: str) -> str:
    if not prev_line.flags.single_glyph:
        raise InvalidFusionError(
            f"attempted zero-separator fusion onto L{prev_line.line_no}, which is not "
            f"single_glyph - only a drop-cap line may be fused with no separator"
        )
    return text


@dataclass(frozen=True)
class JoinLogEntry:
    article_id: str
    field: str
    prev_line: int
    next_line: int
    context: str

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "field": self.field,
            "prev_line": self.prev_line,
            "next_line": self.next_line,
            "context": self.context,
        }


def _union_bbox(lines: list[Line]) -> Bbox:
    return (
        min(l.bbox[0] for l in lines),
        min(l.bbox[1] for l in lines),
        max(l.bbox[2] for l in lines),
        max(l.bbox[3] for l in lines),
    )


def _resolve_range(rng: dict, lines_by_no: dict[int, Line]) -> list[Line]:
    start, end = rng.get("start"), rng.get("end")
    if start is None or end is None or start > end:
        return []
    return [lines_by_no[n] for n in range(start, end + 1) if n in lines_by_no]


def _join_consecutive(
    lines: list[Line],
    article_id: str,
    field_name: str,
    join_log: list[JoinLogEntry] | None = None,
) -> tuple[str, str]:
    """Joins lines that are meant to be directly adjacent (within one
    range): applies the drop-cap fusion and de-hyphenation rules exactly as
    before, just operating on Line objects sliced from a range instead of
    a model-provided unit-id list.

    join_log defaults to a throwaway list so this can be called for
    read-only purposes (e.g. phase3.py's checksum validation needs the
    exact same joining semantics the real assembly will use, not a naive
    "".join() that would produce run-on text like "RamChandra" and fail a
    checksum the model actually got right - see design/DESIGN.md
    "Checksum validation must use real join semantics") without an
    audit-log side effect.
    """
    if join_log is None:
        join_log = []
    if not lines:
        return "", ""
    raw = ""
    cleaned = ""
    prev: Line | None = None
    for line in lines:
        text = line.text
        if prev is None:
            raw = text
            cleaned = text
        elif prev.flags.single_glyph:
            raw += _fuse_drop_cap(prev, text)
            cleaned += _fuse_drop_cap(prev, text)
        else:
            raw += " " + text
            stripped = cleaned.rstrip()
            if stripped.endswith("-") and text[:1].islower():
                cleaned = stripped[:-1] + text
                join_log.append(
                    JoinLogEntry(
                        article_id=article_id,
                        field=field_name,
                        prev_line=prev.line_no,
                        next_line=line.line_no,
                        context=f"{stripped[-24:]}{text[:24]}",
                    )
                )
            else:
                cleaned = stripped + " " + text
        prev = line
    return raw, cleaned


def _join_field(
    ranges: list[dict],
    lines_by_no: dict[int, Line],
    article_id: str,
    field_name: str,
    join_log: list[JoinLogEntry],
) -> tuple[str, str, list[Line]]:
    """A field can be one or more ranges (deck/caption are lists; the rest
    are a single range wrapped as a one-item list by the caller). Lines
    within one range are joined with the drop-cap/de-hyphenation rules;
    separate ranges are joined with a plain space only - never fused or
    de-hyphenated across a range gap, since ranges exist specifically
    because the content between them is NOT part of this field."""
    raw_parts = []
    cleaned_parts = []
    all_lines: list[Line] = []
    for rng in ranges:
        range_lines = _resolve_range(rng, lines_by_no)
        all_lines.extend(range_lines)
        r, c = _join_consecutive(range_lines, article_id, field_name, join_log)
        if r:
            raw_parts.append(r)
        if c:
            cleaned_parts.append(c)
    return " ".join(raw_parts), " ".join(cleaned_parts), all_lines


def _join_field_list(
    ranges: list[dict],
    lines_by_no: dict[int, Line],
    article_id: str,
    field_name: str,
    join_log: list[JoinLogEntry],
) -> tuple[list[str], list[str], list[Line]]:
    """Like _join_field, but keeps each range as its own list entry instead
    of joining them into one string - deck and captions are genuinely
    distinct pieces of furniture (e.g. a two-part kicker+subhead deck, or
    multiple photo captions on one page), and collapsing them loses that
    structure for no benefit (the frontend renders each as its own line -
    see design/DESIGN.md "PressDigest: frontend + API")."""
    raw_parts = []
    cleaned_parts = []
    all_lines: list[Line] = []
    for rng in ranges:
        range_lines = _resolve_range(rng, lines_by_no)
        all_lines.extend(range_lines)
        r, c = _join_consecutive(range_lines, article_id, field_name, join_log)
        if r:
            raw_parts.append(r)
        if c:
            cleaned_parts.append(c)
    return raw_parts, cleaned_parts, all_lines


def _fragment_bboxes(lines: list[Line]) -> list[Bbox]:
    """Splits a body's lines (already in stream/line_no order) into
    geometric fragments for the frontend to draw as separate highlight
    rects, since a multi-column body is not one rectangle. Purely a
    rendering aid - text assembly above never depends on this. A new
    fragment starts whenever consecutive lines are not visually adjacent:
    either a large vertical jump (new column, possibly further up the
    page) or a large horizontal shift (different column's left margin)."""
    if not lines:
        return []
    fragments: list[list[Line]] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        size = prev.font_profile.size or 9.0
        vertical_gap = abs(cur.bbox[1] - prev.bbox[3])
        horizontal_shift = abs(cur.bbox[0] - prev.bbox[0])
        same_fragment = vertical_gap <= size * 3 and horizontal_shift <= size * 3
        if same_fragment:
            fragments[-1].append(cur)
        else:
            fragments.append([cur])
    return [_union_bbox(group) for group in fragments]


@dataclass
class AssembledArticle:
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
    body_rects: list[Bbox] = field(default_factory=list)
    line_nos: list[int] = field(default_factory=list)
    needs_review: bool = False
    validation_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "page": self.page,
            "section_kicker": self.section_kicker,
            "section_kicker_raw": self.section_kicker_raw,
            "headline": self.headline,
            "headline_raw": self.headline_raw,
            "deck": self.deck,
            "deck_raw": self.deck_raw,
            "byline": self.byline,
            "byline_raw": self.byline_raw,
            "dateline": self.dateline,
            "dateline_raw": self.dateline_raw,
            "body": self.body,
            "body_raw": self.body_raw,
            "captions": self.captions,
            "captions_raw": self.captions_raw,
            "is_truncated": self.is_truncated,
            "continues_on_page": self.continues_on_page,
            "confidence": self.confidence,
            "body_rects": [list(b) for b in self.body_rects],
            "line_nos": self.line_nos,
            "needs_review": self.needs_review,
            "validation_issues": self.validation_issues,
        }


def assemble_articles(
    parsed: dict,
    lines: list[Line],
    page_num: int,
    issues_by_article: dict[str, list[str]] | None = None,
) -> tuple[list[AssembledArticle], list[JoinLogEntry]]:
    lines_by_no = {line.line_no: line for line in lines}
    join_log: list[JoinLogEntry] = []
    articles: list[AssembledArticle] = []
    issues_by_article = issues_by_article or {}

    for art in parsed.get("articles") or []:
        article_id = art.get("article_id", "?")
        all_lines: list[Line] = []

        def field(key: str, name: str) -> tuple[str, str]:
            value = art.get(key)
            ranges = value if isinstance(value, list) else ([value] if value else [])
            raw, cleaned, field_lines = _join_field(ranges, lines_by_no, article_id, name, join_log)
            all_lines.extend(field_lines)
            return raw, cleaned

        def field_list(key: str, name: str) -> tuple[list[str], list[str]]:
            ranges = art.get(key) or []
            raw, cleaned, field_lines = _join_field_list(ranges, lines_by_no, article_id, name, join_log)
            all_lines.extend(field_lines)
            return raw, cleaned

        section_kicker_raw, section_kicker = field("section_kicker", "section_kicker")
        headline_raw, headline = field("headline", "headline")
        deck_raw, deck = field_list("deck", "deck")
        byline_raw, byline = field("byline", "byline")
        dateline_raw, dateline = field("dateline", "dateline")
        body_raw, body = field("body", "body")
        captions_raw, captions = field_list("caption", "captions")

        body_range = art.get("body") or {}
        body_lines = _resolve_range(body_range, lines_by_no)
        body_rects = _fragment_bboxes(body_lines)

        confidence = art.get("confidence", "low")
        issues = issues_by_article.get(article_id, [])
        needs_review = bool(issues) or confidence == "low"

        articles.append(
            AssembledArticle(
                article_id=article_id,
                page=page_num,
                section_kicker=section_kicker,
                section_kicker_raw=section_kicker_raw,
                headline=headline,
                headline_raw=headline_raw,
                deck=deck,
                deck_raw=deck_raw,
                byline=byline,
                byline_raw=byline_raw,
                dateline=dateline,
                dateline_raw=dateline_raw,
                body=body,
                body_raw=body_raw,
                captions=captions,
                captions_raw=captions_raw,
                is_truncated=bool(art.get("is_truncated", False)),
                continues_on_page=art.get("continues_on_page"),
                confidence=confidence,
                body_rects=body_rects,
                line_nos=sorted({l.line_no for l in all_lines}),
                needs_review=needs_review,
                validation_issues=issues,
            )
        )

    return articles, join_log
