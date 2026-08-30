"""Page survey report: one row per page summarizing extraction shape, so a
human can see at a glance which pages are dense editorial, which are
ad-heavy, and which are structurally unusual - i.e. what layout variety
Phase 2 has to handle.

Reads already-extracted bronze page.json files; run `extract` first.
"""
from __future__ import annotations

from dataclasses import dataclass

from hindu_extract.config import Config
from hindu_extract.storage import read_page_json


@dataclass
class SurveyRow:
    page_num: int
    line_count: int
    char_count: int
    distinct_fonts: int
    modal_font_size: float
    size_outlier_lines: int
    largest_font_size: float
    text_area_coverage: float


def build_survey(config: Config, edition: str, date: str, page_nums: list[int]) -> list[SurveyRow]:
    rows = []
    for page_num in sorted(page_nums):
        data = read_page_json(config, edition, date, page_num)
        meta = data["metadata"]
        lines = data["lines"]

        page_area = meta["width"] * meta["height"]
        covered_area = sum(
            max(0.0, (l["bbox"][2] - l["bbox"][0])) * max(0.0, (l["bbox"][3] - l["bbox"][1]))
            for l in lines
        )
        rows.append(
            SurveyRow(
                page_num=page_num,
                line_count=meta["line_count"],
                char_count=meta["char_count"],
                distinct_fonts=len(meta["fonts"]),
                modal_font_size=meta["modal_font_size"],
                size_outlier_lines=sum(1 for l in lines if l["flags"]["size_outlier"]),
                largest_font_size=max((l["font_profile"]["size"] for l in lines), default=0.0),
                text_area_coverage=(covered_area / page_area) if page_area else 0.0,
            )
        )
    return rows


def format_survey_table(rows: list[SurveyRow]) -> str:
    headers = [
        "page", "lines", "chars", "fonts", "modal_pt",
        "outliers", "max_pt", "coverage",
    ]
    lines = [rows_to_line(headers)]
    lines.append("-" * len(lines[0]))
    for r in rows:
        lines.append(
            rows_to_line(
                [
                    str(r.page_num),
                    str(r.line_count),
                    str(r.char_count),
                    str(r.distinct_fonts),
                    f"{r.modal_font_size:.1f}",
                    str(r.size_outlier_lines),
                    f"{r.largest_font_size:.1f}",
                    f"{r.text_area_coverage:.2%}",
                ]
            )
        )
    return "\n".join(lines)


def rows_to_line(cells: list[str]) -> str:
    widths = [7, 7, 8, 6, 9, 9, 7, 9]
    return "".join(cell.rjust(w) for cell, w in zip(cells, widths))
