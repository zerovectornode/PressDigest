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
    text: str  # verbatim, exactly the concatenated char text for this line
    bbox: tuple[float, float, float, float]  # x0, top, x1, bottom
    font_profile: FontProfile
    stream_start: int  # index into the page's raw pdfplumber char stream
    stream_end: int
    flags: LineFlags

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
