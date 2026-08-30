"""Read-only helpers over the gold layer for the /api/editions* endpoints.
Thin by design: no computation here that isn't a direct read or a cheap
aggregation of files pipeline code already wrote.
"""
from __future__ import annotations

import json

from hindu_extract.api.edition_id import make_edition_id
from hindu_extract.api.schemas import EditionDetailOut, EditionSummaryOut
from hindu_extract.articles_pipeline import gold_edition_dir
from hindu_extract.config import Config


def _iter_edition_date_dirs(config: Config):
    gold_root = config.gold_root
    if not gold_root.exists():
        return
    for edition_dir in sorted(gold_root.iterdir()):
        if not edition_dir.is_dir():
            continue
        for date_dir in sorted(edition_dir.iterdir()):
            if date_dir.is_dir():
                yield edition_dir.name, date_dir.name


def _page_article_counts(config: Config, edition: str, date: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    edition_dir = gold_edition_dir(config, edition, date)
    for page_dir in sorted(edition_dir.glob("page_*")):
        articles_path = page_dir / "articles.json"
        if not articles_path.exists():
            continue
        page_num = int(page_dir.name.split("_")[1])
        gold = json.loads(articles_path.read_text(encoding="utf-8"))
        counts[page_num] = len(gold.get("articles") or [])
    return counts


def list_editions(config: Config) -> list[EditionSummaryOut]:
    summaries = []
    for edition, date in _iter_edition_date_dirs(config):
        counts = _page_article_counts(config, edition, date)
        if not counts:
            continue
        summaries.append(
            EditionSummaryOut(
                edition_id=make_edition_id(edition, date),
                edition=edition,
                date=date,
                page_count=len(counts),
                article_count=sum(counts.values()),
            )
        )
    return summaries


def get_edition_detail(config: Config, edition: str, date: str) -> EditionDetailOut | None:
    counts = _page_article_counts(config, edition, date)
    if not counts:
        return None
    zero_pages = sorted(page for page, count in counts.items() if count == 0)
    return EditionDetailOut(
        edition_id=make_edition_id(edition, date),
        edition=edition,
        date=date,
        page_count=len(counts),
        article_count=sum(counts.values()),
        pages_with_articles=len(counts) - len(zero_pages),
        pages_with_zero_articles=zero_pages,
    )
