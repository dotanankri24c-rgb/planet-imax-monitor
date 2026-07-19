from datetime import date

from monitor import (
    STATE_VERSION,
    alert_messages,
    card_has_requested_date,
    extract_date,
    extract_imax_showings,
    find_new_showings,
    merge_known_showings,
    split_telegram_message,
    weekday_name,
)


URL_19 = (
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r"
    "#/buy-tickets-by-film?"
    "in-cinema=1072&at=2026-07-19&for-movie=7460s2r"
)
URL_20 = URL_19.replace("2026-07-19", "2026-07-20")


def showing(screening_date: str, screening_time: str = "20:30") -> dict[str, str]:
    return {
        "cinema": "Planet Rishon LeZion",
        "movie": "האודיסאה",
        "date": screening_date,
        "time": screening_time,
        "format": "IMAX",
        "url": URL_19.replace("2026-07-19", screening_date),
    }


def test_extract_date_from_fragment():
    assert extract_date(URL_19) == "2026-07-19"


def test_rejects_stale_card_for_wrong_date():
    assert card_has_requested_date([URL_19], "2026-07-19")
    assert not card_has_requested_date([URL_19], "2026-07-20")


def test_accepts_requested_date_among_multiple_links():
    assert card_has_requested_date([URL_19, URL_20], "2026-07-20")


def test_weekday_name_is_hebrew():
    assert weekday_name("2026-07-19") == "יום ראשון"
    assert weekday_name("2026-07-20") == "יום שני"


def test_extracted_showings_include_weekday():
    items = extract_imax_showings(
        "האודיסאה IMAX 2D 10:00 13:30 VIP 2D 20:00",
        URL_19,
        "2026-07-19",
    )

    assert [item["time"] for item in items] == ["10:00", "13:30"]
    assert all(item["weekday"] == "יום ראשון" for item in items)


def test_state_version_migration_avoids_false_alert():
    previous = {
        "state_version": STATE_VERSION - 1,
        "showings": [],
    }
    current = [showing("2026-07-19")]
    assert find_new_showings(previous, current) == []


def test_current_state_detects_new_showing():
    previous = {
        "state_version": STATE_VERSION,
        "showings": [],
    }
    current = [showing("2026-07-19")]

    new_items = find_new_showings(previous, current)

    assert len(new_items) == 1
    assert new_items[0]["weekday"] == "יום ראשון"


def test_missing_future_showing_is_retained_in_state():
    previous = {
        "state_version": STATE_VERSION,
        "showings": [showing("2026-07-22", "10:00")],
    }

    merged = merge_known_showings(
        previous,
        current=[],
        today=date(2026, 7, 19),
    )

    assert len(merged) == 1
    assert merged[0]["date"] == "2026-07-22"
    assert merged[0]["weekday"] == "יום רביעי"


def test_reappearing_showing_is_not_reported_again():
    original = showing("2026-07-22", "10:00")
    previous = {
        "state_version": STATE_VERSION,
        "showings": [original],
    }

    state_after_incomplete_scrape = merge_known_showings(
        previous,
        current=[],
        today=date(2026, 7, 19),
    )
    next_previous = {
        "state_version": STATE_VERSION,
        "showings": state_after_incomplete_scrape,
    }

    assert find_new_showings(next_previous, [original]) == []


def test_past_showings_are_pruned_from_known_state():
    previous = {
        "state_version": STATE_VERSION,
        "showings": [
            showing("2026-07-18"),
            showing("2026-07-19"),
        ],
    }

    merged = merge_known_showings(
        previous,
        current=[],
        today=date(2026, 7, 19),
    )

    assert [item["date"] for item in merged] == ["2026-07-19"]


def test_empty_alert_does_not_create_header_only_message():
    assert alert_messages([]) == []


def test_alert_message_contains_weekday():
    messages = alert_messages([showing("2026-07-19")])
    assert len(messages) == 1
    assert "יום ראשון, 19/07/2026" in messages[0]


def test_long_telegram_content_is_split():
    blocks = [f"block {index} " + ("x" * 500) for index in range(20)]
    messages = split_telegram_message("header", blocks)
    assert len(messages) > 1
    assert all(len(message) <= 3900 for message in messages)


def test_alert_messages_are_below_telegram_limit():
    items = [
        showing(f"2026-07-{day:02d}")
        for day in range(1, 32)
    ]
    messages = alert_messages(items)
    assert len(messages) > 1
    assert all(len(message) <= 3900 for message in messages)
