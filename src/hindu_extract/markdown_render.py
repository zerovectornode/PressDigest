"""Human-readable markdown rendering of assembled gold-layer articles - per
page and combined for the whole edition, so the extracted paper is
actually readable end to end.
"""
from __future__ import annotations

from hindu_extract.assemble import AssembledArticle


def render_article_markdown(article: AssembledArticle) -> str:
    blocks: list[str] = []

    if article.headline:
        blocks.append(f"# {article.headline}")
    for deck_line in article.deck:
        blocks.append(f"*{deck_line}*")

    meta_bits = []
    if article.byline:
        meta_bits.append(f"**{article.byline}**")
    if article.dateline:
        meta_bits.append(article.dateline)
    if meta_bits:
        blocks.append(" | ".join(meta_bits))

    if article.body:
        blocks.append(article.body)
    for caption in article.captions:
        blocks.append(f"> {caption}")

    footer_bits = []
    if article.is_truncated:
        footer_bits.append(
            f"continues on page {article.continues_on_page}"
            if article.continues_on_page
            else "truncated"
        )
    footer_bits.append(f"confidence: {article.confidence}")
    if article.needs_review:
        footer_bits.append("⚠ needs review")
    blocks.append(f"_{' · '.join(footer_bits)}_")

    return "\n\n".join(blocks)


def render_page_markdown(page_num: int, articles: list[AssembledArticle]) -> str:
    header = f"# Page {page_num}\n"
    if not articles:
        return header + "\n_No articles found on this page._\n"
    sections = [render_article_markdown(a) for a in articles]
    return header + "\n\n---\n\n".join(sections)


def render_edition_markdown(
    edition: str, date: str, pages: list[tuple[int, list[AssembledArticle]]]
) -> str:
    header = f"# The Hindu - {edition.title()} edition - {date}\n"
    sections = []
    for page_num, articles in pages:
        section = [f"## Page {page_num}\n"]
        if not articles:
            section.append("_No articles found on this page._")
        else:
            section.append("\n\n---\n\n".join(render_article_markdown(a) for a in articles))
        sections.append("\n".join(section))
    return header + "\n\n".join(sections)
