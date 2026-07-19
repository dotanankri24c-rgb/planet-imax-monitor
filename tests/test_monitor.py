from monitor import (
    STATE_VERSION,
    alert_messages,
    card_has_requested_date,
    extract_date,
    find_new_showings,
    split_telegram_message,
)


URL_19 = (
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r"
    "#/buy-tickets-by-film?"
    "in-cinema=1072&at=2026-07-19&for-movie=7460s2r"
)
URL_20 = URL_19.replace("2026-07-19", "2026-07-20")


def test_extract_date_from_fragment():
    assert extract_date(URL_19) == "2026-07-19"


def test_rejects_stale_card_for_wrong_date():
    assert card_has_requested_date([URL_19], "2026-07-19")
    assert not card_has_requested_date([URL_19], "2026-07-20")


def test_accepts_requested_date_among_multiple_links():
    assert card_has_requested_date([URL_19, URL_20], "2026-07-20")


def test_state_version_migration_avoids_false_alert():
    previous = {
        "state_version": 3,
        "showings": [],
    }
    current = [
        {
            "cinema": "Planet Rishon LeZion",
            "movie": "האודיסאה",
            "date": "2026-07-19",
            "time": "20:30",
            "format": "IMAX",
            "url": URL_19,
        }
    ]
    assert find_new_showings(previous, current) == []


def test_current_state_detects_new_showing():
    previous = {
        "state_version": STATE_VERSION,
        "showings": [],
    }
    current = [
        {
            "cinema": "Planet Rishon LeZion",
            "movie": "האודיסאה",
            "date": "2026-07-19",
            "time": "20:30",
            "format": "IMAX",
            "url": URL_19,
        }
    ]
    assert find_new_showings(previous, current) == current


def test_long_telegram_content_is_split():
    blocks = [f"block {index} " + ("x" * 500) for index in range(20)]
    messages = split_telegram_message("header", blocks)
    assert len(messages) > 1
    assert all(len(message) <= 3900 for message in messages)


def test_alert_messages_are_below_telegram_limit():
    items = [
        {
            "date": f"2026-07-{day:02d}",
            "time": "20:30",
            "format": "IMAX",
            "url": URL_19,
        }
        for day in range(1, 61)
    ]
    messages = alert_messages(items)
    assert len(messages) > 1
    assert all(len(message) <= 3900 for message in messages)
