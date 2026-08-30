from hindu_extract.api.metadata_parser import parse_masthead


def test_parses_real_the_hindu_masthead_sequence():
    spans = [
        "SATURDAY",
        "www.thehindu.com",
        "https://newsth.live/fb",
        "September 13, 2025",
        "https://newsth.live/x",
        "https://newsth.live/ig",
        "DELHI",
        "CITY EDITION",
        "Regd. DL(ND)-11/6110/2006-07-08",
        "18 Pages",
    ]
    result = parse_masthead(spans)
    assert result.edition == "delhi"
    assert result.date == "2025-09-13"


def test_returns_none_when_no_date_found():
    result = parse_masthead(["SATURDAY", "DELHI", "CITY EDITION"])
    assert result.date is None
    assert result.edition == "delhi"


def test_returns_none_when_no_edition_pattern_found():
    result = parse_masthead(["SATURDAY", "September 13, 2025", "some other text"])
    assert result.edition is None
    assert result.date == "2025-09-13"


def test_does_not_false_positive_on_unrelated_allcaps_word():
    # "REGD" alone with no "...EDITION" follower must not be mistaken for a city
    spans = ["SATURDAY", "September 13, 2025", "REGD", "some registration text"]
    result = parse_masthead(spans)
    assert result.edition is None


def test_different_city_edition_pattern_still_parses():
    spans = ["MONDAY", "January 5, 2026", "CHENNAI", "CITY EDITION"]
    result = parse_masthead(spans)
    assert result.edition == "chennai"
    assert result.date == "2026-01-05"
