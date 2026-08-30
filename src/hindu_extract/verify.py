"""Text-fidelity verification: proves line-building never drops, adds, or
reorders a single character relative to the raw pdfplumber character
stream. Simple by construction now: lines are built by walking page.chars
in their own native order (see lines.py), so concatenating all lines in
line_no order IS the stream order already - no re-sorting needed, unlike
the old span-based version this replaced.
"""
from __future__ import annotations


def whitespace_normalize(text: str) -> str:
    return "".join(text.split())


def raw_extraction_text(page) -> str:
    return "".join(c["text"] for c in page.chars)


def check_text_fidelity(page, lines) -> tuple[bool, str, str]:
    reconstructed = whitespace_normalize("".join(line.text for line in lines))
    raw = whitespace_normalize(raw_extraction_text(page))
    return reconstructed == raw, reconstructed, raw
