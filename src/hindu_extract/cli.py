from __future__ import annotations

import sys
from pathlib import Path

import click
import pdfplumber

from hindu_extract import cache as cache_module
from hindu_extract.articles_pipeline import process_edition_articles, write_edition_markdown
from hindu_extract.config import load_config
from hindu_extract.pipeline import EmptyPageError, extract_pages
from hindu_extract.lines import build_page
from hindu_extract.render import render_debug_overlay, render_hires_image
from hindu_extract.storage import bronze_edition_dir
from hindu_extract.survey import build_survey, format_survey_table
from hindu_extract.trace import RunTracer, new_run_id


def _parse_page_list(pages: str) -> list[int]:
    result = []
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-")
            result.extend(range(int(lo), int(hi) + 1))
        else:
            result.append(int(part))
    return result


def _parse_pages(pages: str | None, pdf_path: Path) -> list[int]:
    if not pages:
        with pdfplumber.open(pdf_path) as pdf:
            return list(range(1, len(pdf.pages) + 1))
    return _parse_page_list(pages)


def _discover_bronze_pages(config, edition: str, date: str) -> list[int]:
    edition_dir = bronze_edition_dir(config, edition, date)
    return sorted(int(p.name.split("_")[1]) for p in edition_dir.glob("page_*") if p.is_dir())


@click.group()
def main():
    """Phase 1: verbatim span extraction for The Hindu e-paper PDF."""


@main.command()
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--date", required=True, help="Edition date, YYYY-MM-DD")
@click.option("--edition", required=True, help="Edition name, e.g. delhi")
@click.option("--pages", default=None, help="e.g. 1,2,3 or 1-5. Default: all pages")
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
@click.option("--force", is_flag=True, help="Ignore cache and re-extract")
def extract(pdf_path, date, edition, pages, config_path, force):
    """Extract every page's lines + metadata + vision image into the bronze layer."""
    config = load_config(config_path)
    page_nums = _parse_pages(pages, pdf_path)

    tracer = RunTracer(db_path=config.trace_db, run_id=new_run_id())
    pdf_hash = cache_module.hash_bytes(Path(pdf_path).read_bytes())
    tracer.start_run(edition, date, pdf_hash, len(page_nums))

    try:
        outcomes = extract_pages(pdf_path, edition, date, page_nums, config, force=force, tracer=tracer)
    except EmptyPageError as e:
        tracer.finish_run("failed")
        click.secho(f"FAILED: {e}", fg="red", err=True)
        sys.exit(1)

    total_findings = sum(len(o.canary_findings) for o in outcomes)
    for o in outcomes:
        tag = "(cached)" if o.from_cache else ""
        click.echo(f"page {o.page_num:2d}: {o.line_count:5d} lines, {o.metadata.char_count:6d} chars {tag}")

    click.echo(f"\nwrote bronze layer to {bronze_edition_dir(config, edition, date)}")

    if total_findings:
        tracer.finish_run("failed")
        click.secho(f"\nCANARY FAILED: {total_findings} finding(s) across all pages", fg="red", err=True)
        for o in outcomes:
            for f in o.canary_findings:
                click.echo(f"  page {f.page_num} [{f.kind}] line={f.line_no}: {f.detail}", err=True)
        sys.exit(1)
    else:
        tracer.finish_run("done")
        click.secho("\ncanary: clean (0 findings)", fg="green")


@main.command()
@click.option("--date", required=True)
@click.option("--edition", required=True)
@click.option("--pages", default=None)
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
def survey(date, edition, pages, config_path):
    """Print the cross-page survey table. Run `extract` first."""
    config = load_config(config_path)
    page_nums = _parse_page_list(pages) if pages else _discover_bronze_pages(config, edition, date)
    rows = build_survey(config, edition, date, page_nums)
    click.echo(format_survey_table(rows))


@main.command()
@click.option("--date", required=True, help="Edition date, YYYY-MM-DD")
@click.option("--edition", required=True, help="Edition name, e.g. delhi")
@click.option("--pages", default=None, help="e.g. 1,2,3 or 1-5. Default: every page in the bronze layer")
@click.option("--no-cache", is_flag=True, help="Bypass the Gemini response cache")
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
def articles(date, edition, pages, no_cache, config_path):
    """Phase 2+3: find each page's article boundaries via Gemini, validate
    them against Phase 1's own stored lines, and write gold-layer JSON +
    markdown. Run `extract` first."""
    config = load_config(config_path)
    page_nums = _parse_page_list(pages) if pages else _discover_bronze_pages(config, edition, date)

    tracer = RunTracer(db_path=config.trace_db, run_id=new_run_id())
    tracer.start_run(edition, date, None, len(page_nums))

    failed_pages: list[int] = []

    def on_error(page_num: int, error: Exception) -> None:
        failed_pages.append(page_num)
        click.secho(f"page {page_num:2d}: FAILED - {error}", fg="red", err=True)

    # Concurrent across pages, gated by a TokenAwareLimiter sized from
    # config.concurrency (see rate_limit.py) - one page's failure is
    # reported via on_error and doesn't stop the others.
    outcomes = process_edition_articles(
        config, edition, date, page_nums, use_cache=not no_cache, error_callback=on_error, tracer=tracer
    )
    tracer.finish_run("failed" if failed_pages else "done")

    for outcome in outcomes:
        cache_tag = "(cached)" if outcome.all_cached else ""
        validation_tag = "OK" if outcome.validation_ok else "NEEDS REVIEW"
        color = "green" if outcome.validation_ok else "yellow"
        click.secho(
            f"page {outcome.page_num:2d}: {len(outcome.articles):2d} articles, "
            f"{len(outcome.excluded_line_nos):3d} excluded lines, "
            f"coverage={outcome.coverage_ratio:.0%}, validation={validation_tag}, "
            f"tokens={outcome.total_tokens} {cache_tag}",
            fg=color,
        )

    write_edition_markdown(config, edition, date, page_nums)
    click.echo(f"\nwrote gold layer to {config.gold_root / edition / date}")

    any_needs_review = any(o.needs_review for o in outcomes)
    if any_needs_review:
        click.secho(
            f"\n{sum(o.needs_review for o in outcomes)} page(s) have at least one "
            f"article marked needs_review (checksum/contiguity/overlap failure or low "
            f"confidence) - see gold JSON for details",
            fg="yellow",
        )

    if failed_pages:
        click.secho(f"\n{len(failed_pages)} page(s) failed outright: {failed_pages}", fg="red", err=True)
        sys.exit(1)


@main.command("render-hires")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--page", "page_num", required=True, type=int)
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
def render_hires(pdf_path, page_num, out, config_path):
    """On-demand high-res render of one page (not persisted by `extract`)."""
    config = load_config(config_path)
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num - 1]
        image = render_hires_image(page, config.render.hires_dpi)
        image.save(out)
    click.echo(f"wrote {out}")


@main.command("debug-overlay")
@click.argument("pdf_path", type=click.Path(exists=True, path_type=Path))
@click.option("--page", "page_num", required=True, type=int)
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option("--config", "config_path", default=None, type=click.Path(path_type=Path))
def debug_overlay(pdf_path, page_num, out, config_path):
    """On-demand render with line_no boxes/labels overlaid, for visual QA."""
    config = load_config(config_path)
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num - 1]
        _metadata, lines = build_page(page, page_num, config)
        image = render_debug_overlay(page, lines, config.render.hires_dpi)
        image.save(out)
    click.echo(f"wrote {out} ({len(lines)} lines overlaid)")


if __name__ == "__main__":
    main()
