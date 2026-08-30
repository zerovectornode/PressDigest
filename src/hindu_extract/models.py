"""Data schema for Phase 1 output. Pure data holders - no extraction logic here.

Rebuilt around stream-ordered lines (see design/DESIGN.md "Stream-order
rebuild") - the geometric span/unit/block layers this replaced are gone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LineFlags:
    single_glyph: bool
    size_outlier: bool
    ends_with_hyphen: bool


@dataclass(frozen=True)
class FontProfile:
    name: str
    size: float
    is_bold: bool
    is_italic: bool
    mixed: bool  # True if this line's chars span more than one (font, size)


@dataclass(frozen=True)
class Line:
    line_no: int  # 1-based, per page, stream order - the only ordering key
    page_num: int
    text: str  # literal, exactly the concatenated char text for this line -
               # this is what the Gemini prompt dump is built from (see
               # gemini_prompt.py), so it must never change once a page has
               # been sent to the model, or the prompt cache key changes too
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom
    font_profile: FontProfile
    stream_start: int  # index into the page's raw pdfplumber char stream
    stream_end: int
    flags: LineFlags
    # Same characters as `text`, plus a synthetic ASCII space inserted
    # between two adjacent alphabetic characters wherever lines.py measured
    # a word-space-sized gap with no space glyph present (see
    # word_space_gap_ratio in config/default.yaml and design/DESIGN.md
    # "Word-space gap fix"). Never removes, reorders, or alters a character
    # - only ever inserts whitespace - see verify.py
    # check_word_space_correction_fidelity. This is what assemble.py uses
    # to build the final headline/body/deck/etc. text readers see;
    # deliberately NOT used for the Gemini prompt (see `text` above).
    # Defaults to `text` unchanged when not explicitly given, so existing
    # callers that construct a Line without knowing about this field (test
    # fixtures, mainly) get correct, safe behavior for free.
    corrected_text: str = ""

    def __post_init__(self) -> None:
        if not self.corrected_text:
            object.__setattr__(self, "corrected_text", self.text)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d


@dataclass(frozen=True)
class FontInfo:
    font_name: str
    char_count: int
    has_tounicode: bool


@dataclass(frozen=True)
class PageMetadata:
    page_num: int
    width: float
    height: float
    modal_font_size: float
    fonts: tuple[FontInfo, ...]
    char_count: int
    line_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CanaryFinding:
    kind: str  # "intra_word_gap" | "unmapped_glyph"
    page_num: int
    line_no: int | None
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WordSpaceInsertion:
    """One synthetic-space insertion made while building Line.corrected_text
    (see lines.py, config/default.yaml word_space_gap_ratio). Logged
    unconditionally, never silently - see design/DESIGN.md "Word-space gap
    fix"."""

    page_num: int
    line_no: int
    position: int  # stream_index of the char immediately before the insertion
    char_before: str
    char_after: str
    gap: float
    ratio: float

    def to_dict(self) -> dict:
        return asdict(self)
