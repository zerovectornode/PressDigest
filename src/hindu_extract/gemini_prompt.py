"""Builds the Phase 2 boundary-finding prompt and its response JSON schema.

Rebuilt around line-number boundaries instead of unit-ID grouping (see
design/DESIGN.md "Stream-order rebuild"): Step 1's diagnostic proved that
an article's body always occupies one contiguous slice of Phase 1's
stream-ordered lines. The model's job is now purely to find where each
article's fields start and end in that numbering, not to reconstruct
reading order by selecting/ordering individual pieces - the geometric
segmentation this replaced kept scrambling bodies because ordering ~380
small units by hand is exactly the kind of bookkeeping-heavy task a model
does unreliably; finding a handful of (start, end) boundaries is a much
smaller, easier task, and the boundary is verified independently afterward
(see phase3.py) rather than trusted outright.

Non-negotiable design rule still holds, in a slightly refined form: article
TEXT never comes from the model. The final body/headline/etc. text is
always Phase 1's own stored line text, sliced by the (start, end) line
numbers the model returns - never anything the model writes. The one
exception is start_words/end_words: a handful of words the model copies
from the line dump purely as a CHECKSUM, used only to verify the model's
own claimed boundary is right (see phase3.py's checksum check) - this text
is compared against and then discarded, never assembled into output.
"""
from __future__ import annotations

from hindu_extract.models import Line

_LINE_RANGE = {
    "type": "object",
    "properties": {
        "start": {"type": "integer"},
        "end": {"type": "integer"},
        "start_words": {"type": "string"},
    },
    "required": ["start", "end", "start_words"],
}

_NULLABLE_LINE_RANGE = {
    "type": ["object", "null"],
    "properties": _LINE_RANGE["properties"],
    "required": _LINE_RANGE["required"],
}

_BODY_RANGE = {
    "type": "object",
    "properties": {
        "start": {"type": "integer"},
        "end": {"type": "integer"},
        "start_words": {"type": "string"},
        "end_words": {"type": "string"},
    },
    "required": ["start", "end", "start_words", "end_words"],
}

RESPONSE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "string"},
                    "headline": _LINE_RANGE,
                    "deck": {"type": "array", "items": _LINE_RANGE},
                    "byline": _NULLABLE_LINE_RANGE,
                    "dateline": _NULLABLE_LINE_RANGE,
                    "body": _BODY_RANGE,
                    "caption": {"type": "array", "items": _LINE_RANGE},
                    "continues_on_page": {"type": ["integer", "null"]},
                    "is_truncated": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": [
                    "article_id",
                    "headline",
                    "deck",
                    "byline",
                    "dateline",
                    "body",
                    "caption",
                    "continues_on_page",
                    "is_truncated",
                    "confidence",
                ],
            },
        },
    },
    "required": ["articles"],
}

SYSTEM_PROMPT = """\
You are analyzing one page of a print newspaper (The Hindu) that has been \
extracted into numbered lines, in the PDF's own text order, with each \
line's font size. Your job is to find the LINE-NUMBER BOUNDARIES of each \
article on the page - not to copy, reorder, or select individual pieces \
of text.

CRITICAL RULE: article text always comes from our own stored lines, sliced \
by the line numbers you return - never from anything you write. The only \
text you may write is start_words/end_words: 2-4 words copied EXACTLY from \
the line dump below, used purely to verify your boundary is correct (we \
slice by your line numbers, then check the result actually starts/ends \
with those words). Do not write full sentences, paraphrase, or summarize \
anywhere. If you are not confident of the exact words at a boundary, copy \
them precisely from the dump rather than guessing.

THE KEY PROPERTY THAT MAKES THIS RELIABLE: an article's body ALWAYS \
occupies one single contiguous range of line numbers - start to end, no \
gaps, even though the underlying columns interleave on the page. You do \
not need to reconstruct reading order piece by piece; you only need to \
find where the body starts and where it ends. Everything between those \
two line numbers belongs to that one article's body, in order, exactly as \
numbered.

FURNITURE IS DIFFERENT: the headline, deck, byline, dateline, and captions \
are NOT necessarily contiguous with the body, or with each other. \
Unrelated stories' teasers can sit between two pieces of the same story's \
own furniture (e.g. a headline at one line number and its deck twenty \
lines later, with other stories' teasers in between). This is why deck \
and caption are LISTS of ranges - use more than one entry if the deck or \
captions are split into separate chunks. headline, byline, and dateline \
are single ranges (byline/dateline may be null if the article has none).

DISTINGUISHING HEADLINE FROM DECK: the main headline is usually the \
largest font size for that story and short (a few words to one line). A \
deck/strap line is typically smaller and reads as one or two full \
sentences. When multiple large-font lines are near each other, use both \
font size and length to decide which is the headline.

CONTINUATION MARKERS: a line like "CONTINUED ON" / a page-number line \
marks that the article is truncated here and continues on another page. \
Parse that target page number into continues_on_page, set \
is_truncated=true, and do NOT include the marker line(s) in any range - \
leave them out entirely, they belong to no field.

TEASER BOXES: short kicker + headline + "NEWS/SPORT/WORLD » PAGE n" boxes \
that only point to a different story elsewhere are not articles - do not \
create an article for one, and do not let one leak into another article's \
ranges.

COMPLETENESS: you do not need to account for every line - anything not \
inside any article's ranges is simply not part of an article (masthead, \
ads, furniture, tables, teasers). Focus entirely on getting real articles'
boundaries right.

Lines are listed one per row as:
  L<line_no>|<font_size>|<text>
"""


def format_line_row(line: Line) -> str:
    return f"L{line.line_no:04d}|{line.font_profile.size:.1f}|{line.text}"


def build_user_prompt(lines: list[Line], page_num: int, modal_font_size: float) -> str:
    header = f"PAGE {page_num} | modal_body_font_size={modal_font_size:.1f}pt | {len(lines)} lines\n"
    rows = [format_line_row(line) for line in lines]
    return header + "\n".join(rows)
