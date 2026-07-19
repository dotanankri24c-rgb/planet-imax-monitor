from datetime import date

from monitor import (
    STATE_VERSION,
    build_booking_url,
    extract_date,
    extract_imax_showings,
    find_new_showings,
    showing_key,
)


CARD = (
    "האודיסאה פעולה, הרפתקאות, מלחמה |172 דקות "
    "2D 12:3013:1514:3016:0016:3018:0019:3020:0021:30 "
    "אנגלית·(כתוביות·עברית) "
    "4DX2D 12:3016:0019:30 אנגלית·(כתוביות·עברית) "
    "IMAX2D 13:3017:0020:30 אנגלית·(כתוביות·עברית) "
    "VIP2D 17:0020:30 אנגלית·(כתוביות·עברית)"
)

FRAGMENT_URL = (
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r"
    "#/buy-tickets-by-film?"
    "in-cinema=1072&at=2026-07-19&for-movie=7460s2r&view-mode=list"
)


def test_extract_date_from_fragment_query():
    assert extract_date(FRAGMENT_URL) == "2026-07-19"


def test_build_booking_url_contains_requested_date():
    url = build_booking_url(date(2026, 7, 25))
    assert "at=2026-07-25" in url
    assert "in-cinema=1072" in url


def test_extracts_only_imax_times_with_expected_date():
    showings = extract_imax_showings(
        CARD,
        FRAGMENT_URL,
        expected_date="2026-07-19",
    )

    assert [item["time"] for item in showings] == [
        "13:30",
        "17:00",
        "20:30",
    ]
    assert all(item["date"] == "2026-07-19" for item in showings)


def test_old_state_migrates_without_false_alert():
    old_state = {
        "state_version": 2,
        "showings": [],
    }
    current = extract_imax_showings(
        CARD,
        FRAGMENT_URL,
        expected_date="2026-07-19",
    )

    assert find_new_showings(old_state, current) == []


def test_detects_new_screening_on_different_date():
    current_day_one = extract_imax_showings(
        CARD,
        FRAGMENT_URL,
        expected_date="2026-07-19",
    )
    current_day_two = extract_imax_showings(
        CARD,
        FRAGMENT_URL.replace("2026-07-19", "2026-07-20"),
        expected_date="2026-07-20",
    )
    current = current_day_one + current_day_two

    previous = {
        "state_version": STATE_VERSION,
        "showings": current_day_one,
    }

    assert find_new_showings(previous, current) == current_day_two


def test_key_distinguishes_same_time_on_different_dates():
    first = {
        "cinema": "Planet Rishon LeZion",
        "movie": "האודיסאה",
        "date": "2026-07-19",
        "time": "20:30",
        "format": "IMAX",
        "url": "https://example.com/1",
    }
    second = {
        **first,
        "date": "2026-07-20",
        "url": "https://example.com/2",
    }

    assert showing_key(first) != showing_key(second)
