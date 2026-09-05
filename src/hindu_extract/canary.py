"""Geometric glyph-drop canary.

Why not a dictionary/ligature-insertion canary: inserting an f-ligature into
a common short word frequently produces another common word ("at"->"flat",
"our"->"flour", "re"->"fire", "ow"->"flow"), so a dictionary check cannot
tell a genuine dropped ligature from a coincidental collision with a common
short word already on the page - precision is unfixable in principle, and
the false-positive rate on this PDF's naive dictionary version (1-11 words
per page, dominated by hyphenated line-wrap fragments and acronym
collisions) confirmed it in practice. See design/DESIGN.md for the full
rationale and measurement log.

Instead this checks the extraction geometry directly, word by word:
  1. Within each word - tokenized with pdfplumber's own extract_words(),
     deliberately independent of this package's line-building (which now
     walks the raw content stream in order - see lines.py - rather than
     grouping by geometry at all). extract_words()'s internal layout
     heuristics correctly keep column-separated fragments as distinct
     words even when they share a y-band and have no space character
     between them, which is exactly the ambiguity this check needs to
     avoid reintroducing.
     The gap between two adjacent characters is compared against
     kerning_gap_ratio * that character's font_size. A dropped glyph (e.g.
     an f-ligature that failed to extract) leaves an unexplained hole
     roughly the width of the missing glyph; normal kerning does not.
     Words containing any character at size_outlier magnitude (drop caps /
     headlines) are skipped, since their proportions differ from body text.
  2. Any raw char object pdfplumber reports with empty/unmapped/undecodable
     text - pdfplumber can return a positioned glyph with no resolvable
     Unicode text (empty string), a "(cid:N)" placeholder (a glyph with no
     Unicode mapping at all, seen on this PDF for a decorative pi-font
     ornament near the masthead), or the U+FFFD replacement character
     (seen nowhere on this PDF so far, but a real failure mode pdfminer can
     produce) - none of these show up in (1) at all, so they're checked
     separately across every char on the page.

Calibrated thresholds and their justification live in config/default.yaml.
"""
from __future__ import annotations

import re
from collections import defaultdict

from hindu_extract.config import Config
from hindu_extract.models import CanaryFinding

_CID_PLACEHOLDER = re.compile(r"^\(cid:\d+\)$")

# Row-bucketing granularity for _chars_in_word's candidate lookup (see
# below) - small relative to any real font size, so it narrows the
# candidate set without needing to be tuned per page.
_ROW_BUCKET_HEIGHT = 1.0


def _is_unmapped(text: str | None) -> bool:
    if text is None or text == "":
        return True
    if _CID_PLACEHOLDER.match(text):
        return True
    return "�" in text


def _bucket_chars_by_row(chars: list[dict]) -> dict[int, list[dict]]:
    """Buckets chars by a coarse row key (top // _ROW_BUCKET_HEIGHT) so
    _chars_in_word can look up only the handful of chars near a word's row
    instead of scanning every char on the page for every word - see the
    perf note on _chars_in_word for why this matters."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for c in chars:
        buckets[int(c["top"] // _ROW_BUCKET_HEIGHT)].append(c)
    return buckets


def _chars_in_word(row_buckets: dict[int, list[dict]], word: dict) -> list[dict]:
    """Was a full `for c in chars` scan per word - O(words * chars_on_page)
    (measured: 3,498 words * 21,285 chars = 74.4M comparisons, 39s, on one
    dense page of a real edition - see design/DESIGN.md "Ligature canary:
    row-bucketed word lookup"). row_buckets narrows the candidate pool to
    chars whose row could plausibly overlap this word's [top, bottom] band
    (a superset of true matches - the same x0/x1/top/bottom filter below is
    still applied unchanged, so results are identical), before the filter
    that used to run against every char on the page."""
    lo = int((word["top"] - 0.5) // _ROW_BUCKET_HEIGHT)
    hi = int((word["bottom"] + 0.5) // _ROW_BUCKET_HEIGHT)
    candidates = [c for key in range(lo, hi + 1) for c in row_buckets.get(key, ())]
    return sorted(
        (
            c
            for c in candidates
            if c["text"] != " "
            and c["x0"] >= word["x0"] - 0.1
            and c["x1"] <= word["x1"] + 0.1
            and c["top"] >= word["top"] - 0.5
            and c["bottom"] <= word["bottom"] + 0.5
        ),
        key=lambda c: c["x0"],
    )


def check_page(page, page_num: int, modal_font_size: float, config: Config) -> list[CanaryFinding]:
    findings: list[CanaryFinding] = []
    ratio = config.thresholds.kerning_gap_ratio
    outlier_ratio = config.thresholds.size_outlier_ratio
    outlier_cutoff = outlier_ratio * modal_font_size if modal_font_size else None

    chars = page.chars
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    row_buckets = _bucket_chars_by_row(chars)

    for word in words:
        wchars = _chars_in_word(row_buckets, word)
        if len(wchars) < 2:
            continue
        if outlier_cutoff and any((c.get("size") or 0) >= outlier_cutoff for c in wchars):
            continue
        for prev, cur in zip(wchars, wchars[1:]):
            size = prev.get("size") or cur.get("size") or 0
            if not size:
                continue
            gap = cur["x0"] - prev["x1"]
            if gap > ratio * size:
                findings.append(
                    CanaryFinding(
                        kind="intra_word_gap",
                        page_num=page_num,
                        line_no=None,
                        detail=(
                            f"gap={gap:.2f}pt ({gap / size:.2f}x font_size) between "
                            f"{prev['text']!r} and {cur['text']!r} in word {word['text']!r}"
                        ),
                    )
                )

    for c in chars:
        text = c.get("text")
        if _is_unmapped(text):
            findings.append(
                CanaryFinding(
                    kind="unmapped_glyph",
                    page_num=page_num,
                    line_no=None,
                    detail=(
                        f"unmapped glyph {text!r} at x0={c.get('x0')}, top={c.get('top')}, "
                        f"font={c.get('fontname')}"
                    ),
                )
            )

    return findings
