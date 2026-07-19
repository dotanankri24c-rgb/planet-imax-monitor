from monitor import (
    STATE_VERSION,
    extract_imax_showings,
    find_new_showings,
    is_relevant,
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

URL = (
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r"
    "?in-cinema=1072&at=2026-07-19"
)


def test_relevance_requires_movie_and_imax():
    assert is_relevant("האודיסאה IMAX יום חמישי 20:30")
    assert is_relevant("The Odyssey — IMAX")
    assert not is_relevant("האודיסאה 2D")
    assert not is_relevant("סרט אחר IMAX")


def test_extracts_only_imax_times():
    showings = extract_imax_showings(CARD, URL)

    assert [item["time"] for item in showings] == [
        "13:30",
        "17:00",
        "20:30",
    ]
    assert all(item["format"] == "IMAX" for item in showings)
    assert all(item["date"] == "2026-07-19" for item in showings)


def test_old_noisy_state_is_migrated_without_false_alert():
    old_state = {
        "showings": [
            {
                "context": CARD,
                "label": "12:30",
                "url": URL,
            }
        ]
    }
    current = extract_imax_showings(CARD, URL)

    assert find_new_showings(old_state, current) == []


def test_detects_only_new_imax_screening():
    current = extract_imax_showings(CARD, URL)
    previous = {
        "state_version": STATE_VERSION,
        "showings": current[:2],
    }

    assert find_new_showings(previous, current) == [current[2]]


def test_key_ignores_url_changes():
    original = {
        "cinema": "Planet Rishon LeZion",
        "movie": "האודיסאה",
        "date": "2026-07-19",
        "time": "20:30",
        "format": "IMAX",
        "url": "https://old-link",
    }
    changed_link = {
        **original,
        "url": "https://new-link",
    }

    assert showing_key(original) == showing_key(changed_link)
