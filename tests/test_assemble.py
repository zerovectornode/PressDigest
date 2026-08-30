import pytest

from hindu_extract.assemble import InvalidFusionError, _fuse_drop_cap, assemble_articles
from hindu_extract.models import FontProfile, Line, LineFlags


def make_line(line_no, text, size=9.0, x0=0.0, top=None, single_glyph=False, size_outlier=False):
    top = top if top is not None else float(line_no) * 10
    return Line(
        line_no=line_no,
        page_num=1,
        text=text,
        bbox=(x0, top, x0 + 100.0, top + 9.0),
        font_profile=FontProfile(name="Body-Regular", size=size, is_bold=False, is_italic=False, mixed=False),
        stream_start=line_no,
        stream_end=line_no,
        flags=LineFlags(single_glyph=single_glyph, size_outlier=size_outlier, ends_with_hyphen=False),
    )


def _fields(**overrides):
    base = {
        "deck": [],
        "byline": None,
        "dateline": None,
        "caption": [],
        "continues_on_page": None,
        "is_truncated": False,
        "confidence": "high",
    }
    base.update(overrides)
    return base


def test_dehyphenates_line_final_hyphen_before_lowercase_continuation():
    lines = [
        make_line(1, "The committee released a joint state-"),
        make_line(2, "ment about the new proposal on Monday"),
    ]
    parsed = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 2}, **_fields()}]}
    articles, join_log = assemble_articles(parsed, lines, page_num=1)

    assert articles[0].body == "The committee released a joint statement about the new proposal on Monday"
    assert "stateMent" not in articles[0].body
    assert articles[0].body_raw == "The committee released a joint state- ment about the new proposal on Monday"
    assert len(join_log) == 1
    assert join_log[0].prev_line == 1 and join_log[0].next_line == 2


def test_does_not_dehyphenate_when_next_line_starts_uppercase():
    lines = [make_line(1, "co-"), make_line(2, "Run enterprises are next")]
    parsed = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 2}, **_fields()}]}
    articles, _ = assemble_articles(parsed, lines, page_num=1)
    assert articles[0].body == "co- Run enterprises are next"


def test_drop_cap_fuses_with_no_space_and_no_hyphen_stripping():
    lines = [
        make_line(1, "T", single_glyph=True, size_outlier=True),
        make_line(2, "oday marks a historic shift"),
    ]
    parsed = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 2}, **_fields()}]}
    articles, join_log = assemble_articles(parsed, lines, page_num=1)

    assert articles[0].body == "Today marks a historic shift"
    assert articles[0].body_raw == "Today marks a historic shift"
    assert not join_log  # drop-cap fusion is not a de-hyphenation join


def test_fuse_drop_cap_allows_single_glyph_line():
    n = make_line(1, "T", single_glyph=True, size_outlier=True)
    assert _fuse_drop_cap(n, "oday") == "oday"


def test_fuse_drop_cap_rejects_non_single_glyph_line():
    ordinary = make_line(1, "An ordinary line of text-")
    with pytest.raises(InvalidFusionError):
        _fuse_drop_cap(ordinary, "oday marks a historic shift")


def test_deck_list_joins_separate_ranges_with_plain_space_no_fusion():
    lines = [
        make_line(10, "First deck chunk-"),
        make_line(20, "unrelated teaser in between"),
        make_line(30, "second lowercase chunk"),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 10, "end": 10},
                **_fields(deck=[{"start": 10, "end": 10}, {"start": 30, "end": 30}]),
            }
        ]
    }
    articles, join_log = assemble_articles(parsed, lines, page_num=1)
    # each deck range is its own list entry (a two-part deck is genuinely
    # two distinct pieces of text, not one blob - see assemble.py
    # _join_field_list), and separate ranges must NOT de-hyphenate even
    # though the first ends in a hyphen and the second starts lowercase
    assert articles[0].deck == ["First deck chunk-", "second lowercase chunk"]
    assert not join_log


def test_multi_rect_splits_body_into_geometric_fragments_not_one_union_box():
    lines = [
        make_line(1, "col one line one", x0=0.0, top=0.0),
        make_line(2, "col one line two", x0=0.0, top=10.0),
        make_line(3, "col two line one", x0=300.0, top=0.0),
        make_line(4, "col two line two", x0=300.0, top=10.0),
    ]
    parsed = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 4}, **_fields()}]}
    articles, _ = assemble_articles(parsed, lines, page_num=1)
    assert len(articles[0].body_rects) == 2


def test_needs_review_true_when_validation_issues_or_low_confidence():
    lines = [make_line(1, "text")]
    parsed = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 1}, **_fields(confidence="low")}]}
    articles, _ = assemble_articles(parsed, lines, page_num=1)
    assert articles[0].needs_review is True

    parsed2 = {"articles": [{"article_id": "a1", "headline": None, "body": {"start": 1, "end": 1}, **_fields(confidence="high")}]}
    articles2, _ = assemble_articles(
        parsed2, lines, page_num=1, issues_by_article={"a1": ["checksum[body]: mismatch"]}
    )
    assert articles2[0].needs_review is True
    assert articles2[0].validation_issues == ["checksum[body]: mismatch"]
