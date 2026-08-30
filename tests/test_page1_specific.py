"""Tests scoped explicitly to page 1 - do not generalize these assumptions
to other pages (verified only for page 1; see design/DESIGN.md)."""
import pdfplumber

from hindu_extract.lines import build_page

PAGE_NUM = 1


def _page1_lines(pdf_path, config):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[PAGE_NUM - 1]
        _metadata, lines, _word_space_log = build_page(page, PAGE_NUM, config)
        return lines


def test_meetings_never_corrupted_by_drop_cap(pdf_path, config):
    lines = _page1_lines(pdf_path, config)
    all_text = "".join(l.text for l in lines)
    assert "meetNings" not in all_text

    has_split_fragments = any(l.text.rstrip().endswith("meet-") for l in lines) and any(
        l.text.strip().lower().startswith("ings") for l in lines
    )
    assert has_split_fragments, "expected 'meet-' and 'ings' as separate stream-order lines"


def test_meet_hyphen_line_immediately_precedes_ings_line_in_stream_order(pdf_path, config):
    """The property the whole rebuild depends on: verified in Step 1's
    diagnostic that a "...meet-" / "ings..." sentence split across two
    fragments - which read as one continuous sentence despite being
    ~140pt apart vertically in different columns - are directly adjacent
    in the raw content stream, with no reordering needed."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[PAGE_NUM - 1]
        _metadata, lines, _word_space_log = build_page(page, PAGE_NUM, config)

    meet_line = next(l for l in lines if l.text.rstrip().endswith("meet-"))
    idx = lines.index(meet_line)
    assert lines[idx + 1].text.strip().lower().startswith("ings")


def test_drop_cap_n_is_isolated_single_glyph_outlier(pdf_path, config):
    lines = _page1_lines(pdf_path, config)
    drop_caps = [l for l in lines if l.text == "N" and l.flags.single_glyph and l.flags.size_outlier]
    assert len(drop_caps) >= 1, "expected an isolated oversized 'N' line flagged as a drop-cap candidate"


def test_drop_cap_n_immediately_precedes_epal_in_stream_order(pdf_path, config):
    lines = _page1_lines(pdf_path, config)
    n_line = next(l for l in lines if l.text == "N" and l.flags.single_glyph)
    idx = lines.index(n_line)
    assert lines[idx + 1].text.startswith("epal")
