"""Tests that apply to every page of docs/Newspaper.pdf (all 18 pages)."""
import json
from pathlib import Path

import pdfplumber
import pytest

from hindu_extract.lines import build_page
from hindu_extract.verify import check_text_fidelity, check_word_space_correction_fidelity

BASELINE_PATH = Path(__file__).parent / "baselines" / "page_counts.json"


def test_canary_findings_are_only_known_benign_cid_placeholders(all_outcomes):
    """The geometric canary now also catches "(cid:N)" placeholders and the
    U+FFFD replacement character (extended per design/DESIGN.md "Stream-
    order rebuild" - it previously only caught empty-string text). On this
    PDF the only real finding is a single decorative pi-font ornament near
    page 1's masthead ("(cid:1)", font EuropeanPi-Three) - genuinely
    non-textual (a dingbat glyph), not a data-loss bug. Any OTHER finding
    (an intra_word_gap, or an unmapped_glyph that isn't a cid placeholder)
    is still a real signal worth failing loudly for."""
    unexpected = []
    for o in all_outcomes:
        for f in o.canary_findings:
            is_known_benign_cid = f.kind == "unmapped_glyph" and "(cid:" in f.detail
            if not is_known_benign_cid:
                unexpected.append((o.page_num, f.to_dict()))
    assert not unexpected, f"unexpected canary findings: {unexpected}"


def test_no_page_produces_zero_lines(all_outcomes):
    empty_pages = [o.page_num for o in all_outcomes if o.line_count == 0]
    assert not empty_pages, f"pages with zero lines (likely extraction failure): {empty_pages}"


def test_text_fidelity_matches_raw_extraction(pdf_path, config, all_page_nums):
    """For each page, the concatenation of all lines' text (already in
    stream order by construction) must equal raw pdfplumber character
    extraction, after whitespace normalization. Proves line-building never
    drops, adds, or reorders character content."""
    mismatches = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in all_page_nums:
            page = pdf.pages[page_num - 1]
            _metadata, lines, _word_space_log = build_page(page, page_num, config)
            ok, reconstructed, raw = check_text_fidelity(page, lines)
            if not ok:
                mismatches.append((page_num, len(reconstructed), len(raw)))
    assert not mismatches, f"text fidelity mismatch on pages (page, recon_len, raw_len): {mismatches}"


def test_word_space_correction_never_alters_a_real_character(pdf_path, config, all_page_nums):
    """Line.corrected_text (see lines.py word_space_gap_ratio) may only ever
    insert a synthetic ASCII space relative to Line.text - never add, drop,
    reorder, or alter a real character. Checked per line, per page, across
    the whole edition."""
    mismatches = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in all_page_nums:
            page = pdf.pages[page_num - 1]
            _metadata, lines, _word_space_log = build_page(page, page_num, config)
            ok, line_mismatches = check_word_space_correction_fidelity(lines)
            if not ok:
                mismatches.append((page_num, line_mismatches))
    assert not mismatches, f"word-space correction altered real characters on pages: {mismatches}"


def test_line_count_and_char_count_regression_baseline(all_outcomes):
    """Regression baseline: if these numbers change, it means extraction
    behavior changed - update tests/baselines/page_counts.json deliberately
    if the change is intended (e.g. threshold recalibration)."""
    assert BASELINE_PATH.exists(), "see README to (re)generate the baseline first"
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    current = {
        str(o.page_num): {"line_count": o.line_count, "char_count": o.metadata.char_count}
        for o in all_outcomes
    }
    assert current == baseline


@pytest.mark.parametrize("outcome_index", range(18))
def test_every_page_has_font_inventory(all_outcomes, outcome_index):
    if outcome_index >= len(all_outcomes):
        pytest.skip("fewer pages than expected")
    outcome = all_outcomes[outcome_index]
    assert len(outcome.metadata.fonts) > 0
    assert outcome.metadata.modal_font_size > 0
