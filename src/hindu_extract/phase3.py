"""Minimal Phase 3: independent validation of the model's claimed line-number
boundaries, without trusting anything it wrote as content.

Three checks, all computed from Phase 1's own stored lines - never from the
model's response text itself:

1. Checksum: start_words/end_words are a few words the model copied from
   the line dump. After slicing by (start, end) and joining with the same
   join semantics assemble.py will actually use (a naive character-level
   concatenation would glue "Ram" + "Chandra" into "RamChandra" and fail a
   checksum the model got right - verified live, see design/DESIGN.md
   "Checksum validation must use real join semantics"), the actual text
   must start/end with those words (normalized for whitespace and case).
   The model cannot game this - the words it wrote only pass if they
   genuinely match text we already had in storage, so this catches
   off-by-N line arithmetic without trusting the model's own claim that a
   boundary is right.
2. Contiguity sanity: a body range should never contain a size_outlier
   line that isn't also single_glyph (a legitimate drop-cap) - i.e. it
   should never pick up a headline-scale line partway through. That would
   mean the claimed boundary is wrong even if the checksum happened to
   pass at the edges.
3. Overlap: no two articles' body ranges may share a line number.

A fourth thing, coverage, is reported but is NOT a failure signal - ads,
furniture, and teasers are legitimately unassigned, so a healthy page can
have well under 100% coverage. It exists to make a sudden, unexplained drop
visible.

There is no retry loop and no unit-ID partition concept anymore (see
design/DESIGN.md "Stream-order rebuild") - a checksum or contiguity failure
is flagged on the specific article/field via needs_review, and the pipeline
continues rather than crashing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from hindu_extract.assemble import _join_consecutive
from hindu_extract.models import Line

ARTICLE_REQUIRED_FIELDS = ("article_id", "headline", "deck", "byline", "dateline", "body", "caption")

# Sentence/quote punctuation the model's checksum frequently gets slightly
# wrong around (trailing period omitted, or a closing smart-quote placed on
# the wrong side of a period) - verified live across the first full
# 18-page run (e.g. checksum "the RTI Act" vs actual text ending "...the
# RTI Act.", or checksum "public authorities." vs actual "...public
# authorities".", where the real difference is only whether the period
# sits inside or outside the closing quote). Deliberately excludes the
# plain ASCII hyphen "-": that case is already handled by comparing against
# the de-hyphenated `cleaned` text (see _slice_texts), and stripping it
# here too could mask a genuine word-level difference.
_PUNCT_RE = re.compile(r"[.,;:!?\"'‘’“”]")


def _strip_punctuation(text: str) -> str:
    return _PUNCT_RE.sub("", text)


def normalize_words(text: str) -> str:
    return " ".join((text or "").lower().split())


def _fuzzy_prefix_match(haystack: str, needle: str) -> bool:
    """True if haystack starts with needle, tolerating two specific,
    understood discrepancies rather than every possible near-match:

    1. The model transcribes start_words/end_words by copying words as
       they appear as SEPARATE ROWS in the line dump, so a checksum
       spanning a drop-cap boundary naturally includes a space
       ("N epal...") that the real, correctly-fused text does not have
       ("Nepal...", per assemble.py's drop-cap rule - see design/DESIGN.md
       "Checksum validation must use real join semantics").
    2. Trailing/leading sentence or quote punctuation sometimes differs
       (a period included or omitted, a closing quote on the other side of
       a period) without the underlying words differing at all.

    Verified live: neither is evidence of a wrong boundary - a genuinely
    wrong boundary puts different WORDS there entirely, which neither
    fallback here would ever paper over (both real off-by-one boundary
    bugs found in the first full 18-page run - a drop-cap line excluded
    from its own body range on two pages - still fail this check, because
    the missing first LETTER of a real word is not a punctuation or
    spacing difference)."""
    if haystack.startswith(needle):
        return True
    if haystack.replace(" ", "").startswith(needle.replace(" ", "")):
        return True
    return _strip_punctuation(haystack).startswith(_strip_punctuation(needle))


def _fuzzy_suffix_match(haystack: str, needle: str) -> bool:
    if haystack.endswith(needle):
        return True
    if haystack.replace(" ", "").endswith(needle.replace(" ", "")):
        return True
    return _strip_punctuation(haystack).endswith(_strip_punctuation(needle))


@dataclass(frozen=True)
class ChecksumMismatch:
    article_id: str
    field: str
    detail: str


@dataclass(frozen=True)
class ContiguityIssue:
    article_id: str
    line_no: int
    detail: str


@dataclass(frozen=True)
class OverlapIssue:
    article_id_a: str
    article_id_b: str
    range_a: tuple[int, int]
    range_b: tuple[int, int]


@dataclass(frozen=True)
class CoverageReport:
    total_lines: int
    covered_lines: int
    coverage_ratio: float


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    checksum_mismatches: tuple[ChecksumMismatch, ...] = field(default_factory=tuple)
    contiguity_issues: tuple[ContiguityIssue, ...] = field(default_factory=tuple)
    overlap_issues: tuple[OverlapIssue, ...] = field(default_factory=tuple)
    coverage: CoverageReport | None = None


def _slice_texts(lines_by_no: dict[int, Line], start: int, end: int) -> tuple[str, str]:
    """Uses assemble.py's own join semantics (drop-cap fusion, de-hyphenation)
    rather than a naive concatenation - see module docstring point 1.
    Returns (raw, cleaned): raw preserves the literal dump text (a hyphen at
    a line break stays a hyphen followed by a space), cleaned applies the
    same de-hyphenation the real assembly uses. A checksum is checked
    against both - verified live across the first full 18-page run: the
    model is told to copy words from the dump, but in practice it often
    quotes the semantically de-hyphenated word instead of the literal
    hyphen-broken fragment (e.g. writes "accountability" for a checksum
    ending where the dump actually reads "accounta-" / "bility" split
    across two lines). This is tolerated the same way the drop-cap
    whitespace artifact is (see _fuzzy_prefix_match) - a genuinely wrong
    boundary would put different words there entirely, not just a
    hyphenation variant of the same word."""
    range_lines = [lines_by_no[n] for n in range(start, end + 1) if n in lines_by_no]
    return _join_consecutive(range_lines, "checksum", "checksum")


def _check_range_checksum(
    article_id: str,
    field_name: str,
    rng: dict,
    lines_by_no: dict[int, Line],
    out: list[ChecksumMismatch],
    check_end: bool,
) -> None:
    start, end = rng.get("start"), rng.get("end")
    if start is None or end is None or start > end:
        out.append(ChecksumMismatch(article_id, field_name, f"invalid range start={start} end={end}"))
        return

    raw_text, cleaned_text = _slice_texts(lines_by_no, start, end)
    norm_raw = normalize_words(raw_text)
    norm_cleaned = normalize_words(cleaned_text)

    # A drop-cap line fuses with no separator (assemble._fuse_drop_cap), so
    # e.g. "M" + "aharashtra Minis-" becomes one word "Maharashtra" in both
    # norm_raw/norm_cleaned. Verified live across the first full 18-page
    # run: the model sometimes quotes only the continuation line's text for
    # its start_words checksum ("aharashtra Minis-"), omitting the
    # drop-cap glyph it nonetheless correctly included in the range - so
    # the start check also tries the text with exactly that one leading
    # character removed.
    start_prefixes = [norm_raw, norm_cleaned]
    if lines_by_no.get(start) and lines_by_no[start].flags.single_glyph:
        start_prefixes += [norm_raw[1:], norm_cleaned[1:]]

    start_words = normalize_words(rng.get("start_words", ""))
    if start_words and not any(_fuzzy_prefix_match(candidate, start_words) for candidate in start_prefixes):
        out.append(
            ChecksumMismatch(
                article_id,
                field_name,
                f"expected slice (L{start}-{end}) to start with {rng.get('start_words')!r}, "
                f"actual text starts {raw_text[:60]!r}",
            )
        )

    if check_end:
        end_words = normalize_words(rng.get("end_words", ""))
        if end_words and not (
            _fuzzy_suffix_match(norm_raw, end_words) or _fuzzy_suffix_match(norm_cleaned, end_words)
        ):
            out.append(
                ChecksumMismatch(
                    article_id,
                    field_name,
                    f"expected slice (L{start}-{end}) to end with {rng.get('end_words')!r}, "
                    f"actual text ends ...{raw_text[-60:]!r}",
                )
            )


def _check_body_contiguity(article_id: str, body_range: dict, lines_by_no: dict[int, Line]) -> list[ContiguityIssue]:
    start, end = body_range.get("start"), body_range.get("end")
    if start is None or end is None:
        return []
    issues = []
    for n in range(start, end + 1):
        line = lines_by_no.get(n)
        if line is None:
            continue
        if line.flags.size_outlier and not line.flags.single_glyph:
            issues.append(
                ContiguityIssue(
                    article_id,
                    n,
                    f"body range L{start}-{end} contains a headline-scale line at L{n} "
                    f"({line.font_profile.size:.1f}pt: {line.text[:40]!r}) - claimed boundary is likely wrong",
                )
            )
    return issues


def _check_overlaps(articles: list[dict]) -> list[OverlapIssue]:
    bodies = []
    for a in articles:
        body = a.get("body") or {}
        if body.get("start") is not None and body.get("end") is not None:
            bodies.append((a.get("article_id", "?"), body["start"], body["end"]))

    issues = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            id_a, sa, ea = bodies[i]
            id_b, sb, eb = bodies[j]
            if sa <= eb and sb <= ea:
                issues.append(OverlapIssue(id_a, id_b, (sa, ea), (sb, eb)))
    return issues


def _all_ranges(article: dict) -> list[dict]:
    ranges = []
    if article.get("headline"):
        ranges.append(article["headline"])
    if article.get("body"):
        ranges.append(article["body"])
    ranges.extend(article.get("deck") or [])
    ranges.extend(article.get("caption") or [])
    if article.get("byline"):
        ranges.append(article["byline"])
    if article.get("dateline"):
        ranges.append(article["dateline"])
    return ranges


def _coverage_report(total_lines: int, articles: list[dict]) -> CoverageReport:
    covered: set[int] = set()
    for a in articles:
        for rng in _all_ranges(a):
            start, end = rng.get("start"), rng.get("end")
            if start is not None and end is not None and start <= end:
                covered.update(range(start, end + 1))
    ratio = (len(covered) / total_lines) if total_lines else 0.0
    return CoverageReport(total_lines=total_lines, covered_lines=len(covered), coverage_ratio=ratio)


def validate_page(lines: list[Line], parsed: dict) -> ValidationResult:
    lines_by_no = {line.line_no: line for line in lines}
    articles = parsed.get("articles") or []

    checksum_mismatches: list[ChecksumMismatch] = []
    contiguity_issues: list[ContiguityIssue] = []

    for article in articles:
        article_id = article.get("article_id", "?")

        if article.get("headline"):
            _check_range_checksum(article_id, "headline", article["headline"], lines_by_no, checksum_mismatches, False)
        for i, deck in enumerate(article.get("deck") or []):
            _check_range_checksum(article_id, f"deck[{i}]", deck, lines_by_no, checksum_mismatches, False)
        if article.get("byline"):
            _check_range_checksum(article_id, "byline", article["byline"], lines_by_no, checksum_mismatches, False)
        if article.get("dateline"):
            _check_range_checksum(article_id, "dateline", article["dateline"], lines_by_no, checksum_mismatches, False)
        for i, cap in enumerate(article.get("caption") or []):
            _check_range_checksum(article_id, f"caption[{i}]", cap, lines_by_no, checksum_mismatches, False)
        if article.get("body"):
            _check_range_checksum(article_id, "body", article["body"], lines_by_no, checksum_mismatches, True)
            contiguity_issues.extend(_check_body_contiguity(article_id, article["body"], lines_by_no))

    overlap_issues = _check_overlaps(articles)
    coverage = _coverage_report(len(lines), articles)

    ok = not checksum_mismatches and not contiguity_issues and not overlap_issues
    return ValidationResult(
        ok=ok,
        checksum_mismatches=tuple(checksum_mismatches),
        contiguity_issues=tuple(contiguity_issues),
        overlap_issues=tuple(overlap_issues),
        coverage=coverage,
    )
