"""Text-fidelity verification: proves line-building never drops, adds, or
reorders a single character relative to the raw pdfplumber character
stream. Simple by construction now: lines are built by walking page.chars
in their own native order (see lines.py), so concatenating all lines in
line_no order IS the stream order already - no re-sorting needed, unlike
the old span-based version this replaced.

The guarantee, stated precisely (see design/DESIGN.md "Word-space gap
fix"): comparing with whitespace stripped from both sides is not a looser
version of "character-for-character identical" - it IS that guarantee,
restricted to the non-whitespace characters, which is exactly the
invariant that must hold once lines.py is allowed to insert a synthetic
space (Line.corrected_text) where the source PDF has none. No glyph is
ever added, removed, reordered, or altered by either check below - the
only permitted deviation from raw extraction, anywhere in this module, is
where whitespace is placed.
"""
from __future__ import annotations


def whitespace_normalize(text: str) -> str:
    return "".join(text.split())


def raw_extraction_text(page) -> str:
    return "".join(c["text"] for c in page.chars)


def check_text_fidelity(page, lines) -> tuple[bool, str, str]:
    """Checks Line.text (the literal, uncorrected glyph-joined text - see
    models.py) against raw extraction. Untouched by the word-space fix:
    Line.text never gains a synthetic space, so this keeps proving exactly
    what it always proved - line-GROUPING drops/adds/reorders nothing."""
    reconstructed = whitespace_normalize("".join(line.text for line in lines))
    raw = whitespace_normalize(raw_extraction_text(page))
    return reconstructed == raw, reconstructed, raw


def check_word_space_correction_fidelity(lines) -> tuple[bool, list[str]]:
    """Checks Line.corrected_text against Line.text, per line: proves the
    word-space fix (lines.py, config/default.yaml word_space_gap_ratio)
    only ever inserts whitespace and never adds, drops, reorders, or alters
    a real character. Runs per-line (finer-grained than check_text_fidelity's
    whole-page comparison) so a future change to the insertion logic that
    corrupts one line's content can't hide inside an otherwise-passing page.
    """
    mismatches = []
    for line in lines:
        if whitespace_normalize(line.corrected_text) != whitespace_normalize(line.text):
            mismatches.append(
                f"L{line.line_no}: text={line.text!r} corrected_text={line.corrected_text!r}"
            )
    return not mismatches, mismatches
