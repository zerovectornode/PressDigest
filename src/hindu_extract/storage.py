"""Bronze layer: per-page JSON, keyed on edition/date/page. This is the
durable, human-browsable output of Phase 1.
"""
from __future__ import annotations

import json
from pathlib import Path

from hindu_extract.config import Config


def bronze_page_dir(config: Config, edition: str, date: str, page_num: int) -> Path:
    return config.bronze_root / edition / date / f"page_{page_num:02d}"


def bronze_edition_dir(config: Config, edition: str, date: str) -> Path:
    return config.bronze_root / edition / date


def write_manifest(config: Config, edition: str, date: str, manifest: dict) -> Path:
    edition_dir = bronze_edition_dir(config, edition, date)
    edition_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = edition_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest_path


def read_page_json(config: Config, edition: str, date: str, page_num: int) -> dict:
    path = bronze_page_dir(config, edition, date, page_num) / "page.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def raw_pdf_path(config: Config, edition: str, date: str) -> Path:
    """Where the API stores the originally-uploaded PDF for an edition, so
    GET /api/editions/{id}/pdf can serve it back for PDF.js rendering."""
    return config.raw_pdf_root / edition / date / "source.pdf"
