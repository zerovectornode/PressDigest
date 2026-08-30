"""Soft, review-only dictionary sweep for residual word-space fusions the
geometric fix (lines.py word_space_gap_ratio) might still miss - e.g. a
future edition with different kerning where the calibrated threshold
doesn't transfer. NOT a build-breaker: see design/DESIGN.md "Word-space gap
fix" for why. Live investigation on this PDF's 74 candidates from this
exact pattern found ~66% were genuine fusions and ~34% were real proper
nouns that happen to start with a capital "A"/"I" (Amit Shah, Akali Dal,
Ayon Sengupta, AHEL, ...) - a false-positive rate too high to hard-fail a
build on, but still useful as a flagged list for a human to skim, the same
way the dictionary/ligature approach canary.py's module docstring explains
was rejected as a hard-fail check for a structurally identical reason.

Deliberately narrower than a generic "does this word split into two
dictionary words" search (see the module docstring in canary.py for why
that's far noisier - proper-noun substrings collide constantly): this only
flags a non-dictionary word that starts with a lowercase "a" or "i" whose
remainder is itself a common dictionary word, which is what the confirmed
bug mechanism on this PDF actually looks like (a single-character function
word losing its trailing space) - not a general-purpose spelling checker.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from functools import lru_cache

_WORD_RE = re.compile(r"[A-Za-z]+")
_MIN_REMAINDER_FREQ = 1e-7
_MIN_REMAINDER_LEN = 2


@dataclass(frozen=True)
class FusionCandidate:
    word: str
    prefix: str  # "a" or "i"
    remainder: str
    context: str  # ~40 chars centered on the word, for a human to judge

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=1)
def _dictionary():
    from spellchecker import SpellChecker

    return SpellChecker()


def find_candidates(text: str, context_chars: int = 20) -> list[FusionCandidate]:
    if not text:
        return []
    sp = _dictionary()
    freq = sp.word_frequency.dictionary
    candidates: list[FusionCandidate] = []
    for m in _WORD_RE.finditer(text):
        word = m.group()
        word_lower = word.lower()
        if freq.get(word_lower, 0) > 0:
            continue  # already a valid word - not a fusion candidate
        if len(word_lower) < 1 + _MIN_REMAINDER_LEN:
            continue
        prefix = word_lower[0]
        if prefix not in ("a", "i"):
            continue
        remainder = word_lower[1:]
        if freq.get(remainder, 0) <= _MIN_REMAINDER_FREQ:
            continue
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        candidates.append(
            FusionCandidate(word=word, prefix=prefix, remainder=remainder, context=text[start:end])
        )
    return candidates
