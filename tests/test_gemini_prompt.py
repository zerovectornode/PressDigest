from hindu_extract.gemini_prompt import RESPONSE_JSON_SCHEMA, build_user_prompt, format_line_row
from hindu_extract.models import FontProfile, Line, LineFlags


def make_line(line_no, text, size=9.0, single_glyph=False, size_outlier=False):
    return Line(
        line_no=line_no,
        page_num=1,
        text=text,
        bbox=(1.4, 2.6, 10.9, 20.1),
        font_profile=FontProfile(name="Body-Regular", size=size, is_bold=False, is_italic=False, mixed=False),
        stream_start=0,
        stream_end=0,
        flags=LineFlags(single_glyph=single_glyph, size_outlier=size_outlier, ends_with_hyphen=False),
    )


def test_format_line_row_is_pipe_delimited_with_line_no_size_and_dropcap_flag():
    line = make_line(1, "hello world", size=9.0)
    row = format_line_row(line)
    assert row == "L0001|9.0|-|hello world"


def test_format_line_row_flags_drop_cap_lines():
    drop_cap = make_line(1, "P", size=37.0, single_glyph=True, size_outlier=True)
    row = format_line_row(drop_cap)
    assert row == "L0001|37.0|D|P"


def test_format_line_row_does_not_flag_size_outlier_alone_as_drop_cap():
    # A multi-character headline can be size_outlier without being a drop
    # cap (single_glyph is what makes it a drop cap specifically).
    headline = make_line(1, "Big Headline", size=40.0, single_glyph=False, size_outlier=True)
    row = format_line_row(headline)
    assert row == "L0001|40.0|-|Big Headline"


def test_build_user_prompt_includes_modal_size_header_and_all_lines():
    lines = [make_line(1, "hello"), make_line(2, "world")]
    prompt = build_user_prompt(lines, page_num=1, modal_font_size=9.0)
    assert "PAGE 1" in prompt
    assert "modal_body_font_size=9.0pt" in prompt
    assert "L0001" in prompt
    assert "L0002" in prompt


def test_schema_has_no_free_text_content_fields_only_ranges_and_enums():
    article_props = RESPONSE_JSON_SCHEMA["properties"]["articles"]["items"]["properties"]

    single_range_fields = {"headline", "byline", "dateline", "section_kicker"}
    for field_name in single_range_fields:
        props = article_props[field_name]["properties"]
        assert set(props.keys()) == {"start", "end", "start_words"}
        assert props["start"]["type"] == "integer"
        assert props["start_words"]["type"] == "string"

    list_range_fields = {"deck", "caption"}
    for field_name in list_range_fields:
        assert article_props[field_name]["type"] == "array"
        item_props = article_props[field_name]["items"]["properties"]
        assert set(item_props.keys()) == {"start", "end", "start_words"}

    body_props = article_props["body"]["properties"]
    assert set(body_props.keys()) == {"start", "end", "start_words", "end_words"}

    # confidence is a closed enum, can't carry article prose either
    assert article_props["confidence"]["enum"] == ["high", "medium", "low"]


def test_schema_required_fields_present_on_every_article():
    article_schema = RESPONSE_JSON_SCHEMA["properties"]["articles"]["items"]
    assert set(article_schema["required"]) == {
        "article_id",
        "section_kicker",
        "headline",
        "deck",
        "byline",
        "dateline",
        "body",
        "caption",
        "continues_on_page",
        "is_truncated",
        "confidence",
    }
