"""Read-only helpers over the bronze/gold layers for the
/api/editions/{id}/pages/{n}* endpoints. Thin by design, same spirit as
editions.py: no computation here beyond direct reads and cheap derivation
from what the pipeline already wrote to disk. Never fabricates a field the
pipeline didn't produce - see design/DESIGN.md "no fabricated data".
"""
from __future__ import annotations

import json

from hindu_extract.api.schemas import ArticleOut, PageOut
from hindu_extract.articles_pipeline import gold_page_dir
from hindu_extract.config import Config
from hindu_extract.storage import bronze_page_dir


def _read_bronze_metadata(config: Config, edition: str, date: str, page_num: int) -> dict | None:
    path = bronze_page_dir(config, edition, date, page_num) / "page.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["metadata"]


def _read_gold(config: Config, edition: str, date: str, page_num: int) -> dict | None:
    path = gold_page_dir(config, edition, date, page_num) / "articles.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def get_page(config: Config, edition: str, date: str, page_num: int) -> PageOut | None:
    metadata = _read_bronze_metadata(config, edition, date, page_num)
    if metadata is None:
        return None
    gold = _read_gold(config, edition, date, page_num)
    return PageOut(
        page_num=page_num,
        width=metadata["width"],
        height=metadata["height"],
        line_count=metadata["line_count"],
        article_count=len(gold["articles"]) if gold else 0,
        validation_ok=gold["validation_ok"] if gold else False,
        coverage_ratio=gold["coverage"]["coverage_ratio"] if gold else None,
    )


def get_page_articles(config: Config, edition: str, date: str, page_num: int) -> list[ArticleOut] | None:
    gold = _read_gold(config, edition, date, page_num)
    if gold is None:
        return None
    articles = []
    for a in gold["articles"]:
        issues = a.get("validation_issues") or []
        articles.append(
            ArticleOut(
                article_id=a["article_id"],
                page=a["page"],
                headline=a["headline"],
                headline_raw=a["headline_raw"],
                deck=a["deck"],
                deck_raw=a["deck_raw"],
                byline=a["byline"],
                byline_raw=a["byline_raw"],
                dateline=a["dateline"],
                dateline_raw=a["dateline_raw"],
                body=a["body"],
                body_raw=a["body_raw"],
                captions=a["captions"],
                captions_raw=a["captions_raw"],
                is_truncated=a["is_truncated"],
                continues_on_page=a["continues_on_page"],
                confidence=a["confidence"],
                rects=a["body_rects"],
                validation_ok=not issues,
                needs_review=a["needs_review"],
                validation_issues=issues,
            )
        )
    return articles
