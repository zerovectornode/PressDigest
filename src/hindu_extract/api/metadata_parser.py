"""Parses edition name + date directly from a page's masthead text, so the
upload flow doesn't force the user to hand-type metadata that's already in
the PDF - and, more importantly, doesn't silently default to "today" for a
back-dated PDF and corrupt the (edition, date) storage key.

Calibrated against The Hindu's page-1 masthead layout (verified on
docs/Newspaper.pdf): the first ~10 lines in stream order read
SATURDAY / www.thehindu.com / <social urls> / September 13, 2025 /
<social urls> / DELHI / CITY EDITION / Regd. .../ 18 Pages ...

Two independent structural signals, neither of which hardcodes a city or
date:
  - date: the first line matching "Month D(D), YYYY".
  - edition: a short ALL-CAPS alphabetic line immediately followed by a
    line containing "EDITION" (case-insensitive) - this pattern should hold
    for any city edition of this newspaper (Chennai, Mumbai, ...), not just
    Delhi.

Returns None for whichever field it can't find, so the caller can fall back
to asking the user - this parser never guesses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls

import pdfplumber

from hindu_extract.lines import build_page

DATE_PATTERN = re.compile(r"\b([A-Z][a-z]+ \d{1,2},\s*\d{4})\b")


@dataclass(frozen=True)
class ParsedMetadata:
    edition: str | None
    date: str | None  # ISO format YYYY-MM-DD


def _parse_date(text: str) -> date_cls | None:
    for fmt in ("%B %d, %Y", "%B %d,%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_masthead(lines_in_order: list[str]) -> ParsedMetadata:
    parsed_date = None
    for text in lines_in_order:
        match = DATE_PATTERN.search(text)
        if match:
            d = _parse_date(match.group(1))
            if d:
                parsed_date = d.isoformat()
                break

    parsed_edition = None
    for prev, nxt in zip(lines_in_order, lines_in_order[1:]):
        if prev.isalpha() and prev.isupper() and 2 <= len(prev) <= 20 and "EDITION" in nxt.upper():
            parsed_edition = prev.lower()
            break

    return ParsedMetadata(edition=parsed_edition, date=parsed_date)


def parse_metadata_from_pdf(pdf_path, config) -> ParsedMetadata:
    """Extracts just page 1 (no bronze cache write) to read the masthead."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        _metadata, lines, _word_space_log = build_page(page, 1, config)

    # Lines are already in stream order by construction - no re-sorting needed.
    return parse_masthead([line.text for line in lines])
