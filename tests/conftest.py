from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

from hindu_extract.config import load_config
from hindu_extract.pipeline import extract_pages

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = PROJECT_ROOT / "docs" / "Newspaper.pdf"
EDITION = "delhi"
DATE = "2025-09-13"


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def pdf_path():
    """Every test that needs the real source PDF goes through this fixture
    (directly, or transitively via all_page_nums/all_outcomes below), so
    gating it here is enough to make every one of them skip cleanly - with
    a clear reason - instead of erroring when the PDF isn't present. The
    PDF itself is never committed (it's The Hindu's copyrighted e-paper
    content); see README.md "Supplying a PDF"."""
    if not PDF_PATH.exists():
        pytest.skip(
            f"docs/Newspaper.pdf not found - this repo doesn't include a copy of the "
            f"source PDF (it's copyrighted newspaper content). Supply your own licensed "
            f"e-paper PDF at {PDF_PATH} to run this test - see README.md."
        )
    return PDF_PATH


@pytest.fixture(scope="session")
def all_page_nums(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return list(range(1, len(pdf.pages) + 1))


@pytest.fixture(scope="session")
def all_outcomes(pdf_path, config, all_page_nums):
    """Extracts every page once per test session (cache-backed, so repeat
    test runs are fast after the first)."""
    return extract_pages(pdf_path, EDITION, DATE, all_page_nums, config)
