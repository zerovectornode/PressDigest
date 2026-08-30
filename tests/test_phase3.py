from hindu_extract.models import FontProfile, Line, LineFlags
from hindu_extract.phase3 import validate_page


def make_line(line_no, text, size=9.0, single_glyph=False, size_outlier=False):
    return Line(
        line_no=line_no,
        page_num=1,
        text=text,
        bbox=(0.0, float(line_no) * 10, 100.0, float(line_no) * 10 + 9),
        font_profile=FontProfile(name="Body-Regular", size=size, is_bold=False, is_italic=False, mixed=False),
        stream_start=line_no,
        stream_end=line_no,
        flags=LineFlags(single_glyph=single_glyph, size_outlier=size_outlier, ends_with_hyphen=False),
    )


LINES = [
    make_line(1, "Very Big Headline", size=40.0, size_outlier=True),
    make_line(2, "By a Reporter"),
    make_line(3, "CITY"),
    make_line(4, "The quick brown fox"),
    make_line(5, "jumps over the lazy dog."),
]


def _empty_fields(**overrides):
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


def test_ok_when_checksums_match_and_no_overlap():
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                "body": {"start": 4, "end": 5, "start_words": "The quick", "end_words": "lazy dog."},
                **_empty_fields(byline={"start": 2, "end": 2, "start_words": "By a"}),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    assert result.ok
    assert not result.checksum_mismatches
    assert not result.contiguity_issues
    assert not result.overlap_issues
    assert result.coverage.total_lines == 5


def test_checksum_normalizes_case_and_whitespace():
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "  VERY   big  Headline  "},
                "body": {"start": 4, "end": 5, "start_words": "the QUICK", "end_words": "LAZY   dog."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    assert result.ok


def test_checksum_mismatch_on_wrong_start_words():
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Completely Wrong"},
                "body": {"start": 4, "end": 5, "start_words": "The quick", "end_words": "lazy dog."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    assert not result.ok
    assert len(result.checksum_mismatches) == 1
    assert result.checksum_mismatches[0].field == "headline"


def test_checksum_mismatch_on_wrong_end_words_catches_off_by_n():
    # body claims to end at line 4 (with end_words matching line 5's text)
    # - this is exactly the off-by-one-line arithmetic error the checksum
    # is designed to catch.
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                "body": {"start": 4, "end": 4, "start_words": "The quick", "end_words": "lazy dog."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    assert not result.ok
    assert any(m.field == "body" for m in result.checksum_mismatches)


def test_contiguity_flags_headline_scale_line_inside_body_range():
    # body range wrongly includes the size_outlier headline line at L1
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                "body": {"start": 1, "end": 5, "start_words": "Very Big Headline", "end_words": "lazy dog."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    assert not result.ok
    assert len(result.contiguity_issues) == 1
    assert result.contiguity_issues[0].line_no == 1


def test_contiguity_allows_single_glyph_drop_cap_at_body_start():
    drop_cap_lines = [
        make_line(1, "N", size=40.0, single_glyph=True, size_outlier=True),
        make_line(2, "epal President Ram"),
        make_line(3, "continues the sentence."),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "N"},
                "body": {"start": 1, "end": 3, "start_words": "N epal", "end_words": "the sentence."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(drop_cap_lines, parsed)
    assert not result.contiguity_issues


def test_overlap_detected_between_two_bodies():
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                "body": {"start": 4, "end": 5, "start_words": "The quick", "end_words": "lazy dog."},
                **_empty_fields(),
            },
            {
                "article_id": "a2",
                "headline": {"start": 2, "end": 2, "start_words": "By a"},
                "body": {"start": 5, "end": 5, "start_words": "jumps over", "end_words": "lazy dog."},
                **_empty_fields(),
            },
        ]
    }
    result = validate_page(LINES, parsed)
    assert not result.ok
    assert len(result.overlap_issues) == 1
    issue = result.overlap_issues[0]
    assert {issue.article_id_a, issue.article_id_b} == {"a1", "a2"}


def test_checksum_accepts_dehyphenated_end_words_across_a_line_break():
    # Verified live across the first full 18-page run: the model's
    # end_words checksum quoted the semantically de-hyphenated word even
    # though the dump literally splits it across two lines with a hyphen.
    # The real assembled body text IS de-hyphenated the same way, so this
    # must not be flagged as a wrong boundary.
    lines = [
        make_line(1, "plans to modernise the transport and account-"),
        make_line(2, "ability, she added."),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 1, "end": 2, "start_words": "plans to", "end_words": "accountability, she added."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert result.ok


def test_checksum_accepts_start_words_that_omit_the_drop_cap_glyph():
    # Verified live: the model correctly included the drop-cap line in the
    # range but quoted only the continuation line's text for start_words,
    # omitting the drop-cap letter it nonetheless placed correctly in the
    # range boundary itself.
    drop_cap_lines = [
        make_line(1, "M", size=40.0, single_glyph=True, size_outlier=True),
        make_line(2, "inisters gathered for the summit today"),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 1, "end": 2, "start_words": "inisters gathered", "end_words": "summit today"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(drop_cap_lines, parsed)
    assert result.ok


def test_checksum_accepts_trailing_period_omitted_by_the_model():
    # Verified live: checksum end_words omitted a trailing sentence period
    # present in the real text - same words, only the period differs.
    lines = [make_line(1, "the new policy will require significant time and resources.")]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 1, "end": 1, "start_words": "the new", "end_words": "time and resources"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert result.ok


def test_checksum_accepts_quote_on_the_other_side_of_the_period():
    # Verified live: checksum end_words placed the period inside the
    # closing quote where the real text places it outside (or vice versa) -
    # same words, only quote/period ordering differs.
    lines = [make_line(1, 'are not classified as “essential services”.')]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 1, "end": 1, "start_words": "are not", "end_words": "essential services."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert result.ok


def test_checksum_still_rejects_a_missing_drop_cap_letter_in_the_actual_text():
    # The real off-by-one bug found live: the body range starts one line
    # AFTER the actual drop-cap line, so the real text is missing its first
    # letter entirely even though the model's checksum correctly includes
    # it. This is a real word-level difference, not punctuation or
    # drop-cap-fusion spacing - none of the fuzzy fallbacks should paper
    # over it.
    lines = [
        make_line(1, "MATCH REPORT"),
        # the drop-cap "B" line is missing from this range on purpose -
        # the range starts one line too late, exactly as found live.
        make_line(2, "eyond the opening overs of the tournament"),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": None,
                "body": {"start": 2, "end": 2, "start_words": "Beyond the opening", "end_words": "opening overs"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert not result.ok
    assert any(m.field == "body" for m in result.checksum_mismatches)


def test_headline_quality_flags_a_single_character_headline():
    # The real bug found live (page 6): a drop cap ("W", "T", "I") mistaken
    # for the headline. lines[0] here plays that role - single_glyph,
    # size_outlier, one character.
    lines = [
        make_line(1, "W", size=37.0, single_glyph=True, size_outlier=True),
        make_line(2, "eeks after calling off the visit"),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "W"},
                "body": {"start": 1, "end": 2, "start_words": "W", "end_words": "the visit"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert not result.ok
    assert len(result.headline_quality_issues) == 1
    issue = result.headline_quality_issues[0]
    assert issue.article_id == "a1"
    assert issue.headline_text == "W"
    assert "single character" in issue.detail


def test_headline_quality_flags_drop_cap_fused_with_body_opening():
    # The other real variant found live (page 6, article 1): the drop cap
    # fused with the start of body prose reads like the opening clause of
    # a sentence, not a title - still well under the word-count floor a
    # real headline is expected to clear... except this one is NOT under
    # the floor by word count alone (6 words), so this test documents that
    # the word-count check alone does not catch it - see the live
    # investigation for why the prompt fix (not validation) is the primary
    # defense for this specific sub-case.
    lines = [
        make_line(1, "P", size=37.0, single_glyph=True, size_outlier=True),
        make_line(2, "olicing the digital economy requires what"),
    ]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 2, "start_words": "P"},
                "body": {"start": 1, "end": 2, "start_words": "P", "end_words": "requires what"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    # 6 words - clears the word-count floor, so this specific case is NOT
    # caught by validation alone (documented, not asserted as a false
    # negative bug - the prompt fix is what actually prevents this).
    assert not result.headline_quality_issues


def test_headline_quality_flags_headline_shorter_than_three_words():
    lines = [make_line(1, "Two Words", size=20.0), make_line(2, "body text here")]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Two Words"},
                "body": {"start": 2, "end": 2, "start_words": "body text", "end_words": "text here"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert not result.ok
    assert len(result.headline_quality_issues) == 1
    assert "2 word(s)" in result.headline_quality_issues[0].detail


def test_headline_quality_ok_for_a_real_three_word_headline():
    result = validate_page(
        LINES,
        {
            "articles": [
                {
                    "article_id": "a1",
                    "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                    "body": {"start": 4, "end": 5, "start_words": "The quick", "end_words": "lazy dog."},
                    **_empty_fields(),
                }
            ]
        },
    )
    assert not result.headline_quality_issues


def test_headline_quality_flags_an_all_caps_section_kicker():
    # The real bug found live (page 7): "GROUND ZERO" (39.9pt, ALL-CAPS
    # section kicker) mistaken for the headline instead of the real title
    # "After the disaster" below it.
    lines = [make_line(1, "GROUND ZERO", size=39.9), make_line(2, "some body text here")]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "GROUND ZERO"},
                "body": {"start": 2, "end": 2, "start_words": "some body", "end_words": "text here"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert not result.ok
    assert len(result.headline_quality_issues) == 1
    issue = result.headline_quality_issues[0]
    assert issue.headline_text == "GROUND ZERO"
    assert "section kicker" in issue.detail


def test_headline_quality_ok_for_a_longer_all_caps_headline():
    # "LETTERS TO THE EDITOR" (found live, page 6) is a genuine, legitimate
    # ALL-CAPS headline for a letters roundup, not a kicker - 4 words is
    # above the kicker-like threshold, so it must not be flagged.
    lines = [make_line(1, "LETTERS TO THE EDITOR", size=20.0), make_line(2, "a reader writes in")]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "LETTERS TO"},
                "body": {"start": 2, "end": 2, "start_words": "a reader", "end_words": "writes in"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert not result.headline_quality_issues


def test_headline_quality_ok_for_a_short_headline_that_is_not_all_caps():
    # "Doublespeak" (found live, page 6) is a genuine one-word title-case
    # headline - flagged by the separate word-count check (a real, known
    # trade-off already accepted), but must NOT additionally match the
    # ALL-CAPS kicker pattern, which is specifically scoped to all-caps text.
    lines = [make_line(1, "Doublespeak", size=20.0), make_line(2, "weeks after the visit")]
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Doublespeak"},
                "body": {"start": 2, "end": 2, "start_words": "weeks after", "end_words": "the visit"},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(lines, parsed)
    assert len(result.headline_quality_issues) == 1
    assert "section kicker" not in result.headline_quality_issues[0].detail


def test_coverage_report_reflects_unassigned_lines():
    parsed = {
        "articles": [
            {
                "article_id": "a1",
                "headline": {"start": 1, "end": 1, "start_words": "Very Big Headline"},
                "body": {"start": 4, "end": 5, "start_words": "The quick", "end_words": "lazy dog."},
                **_empty_fields(),
            }
        ]
    }
    result = validate_page(LINES, parsed)
    # lines 2, 3 (byline/dateline not referenced) are unassigned
    assert result.coverage.total_lines == 5
    assert result.coverage.covered_lines == 3
    assert result.coverage.coverage_ratio == 3 / 5
