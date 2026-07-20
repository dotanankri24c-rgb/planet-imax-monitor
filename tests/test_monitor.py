import asyncio
import json
from datetime import date

import monitor
from monitor import (
    STATE_VERSION,
    alert_messages,
    card_has_requested_date,
    extract_date,
    extract_imax_showings,
    find_new_showings,
    load_state,
    merge_known_showings,
    save_state,
    split_telegram_message,
    weekday_name,
)


URL_19 = (
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r"
    "#/buy-tickets-by-film?"
    "in-cinema=1072&at=2026-07-19&for-movie=7460s2r"
)
URL_20 = URL_19.replace("2026-07-19", "2026-07-20")
RUN_TEST_TODAY = date(2026, 7, 19)


def showing(screening_date: str, screening_time: str = "20:30") -> dict[str, str]:
    return {
        "cinema": "Planet Rishon LeZion",
        "movie": "האודיסאה",
        "date": screening_date,
        "time": screening_time,
        "format": "IMAX",
        "url": URL_19.replace("2026-07-19", screening_date),
    }


def valid_state(*items: dict[str, str]) -> dict:
    return {
        "state_version": STATE_VERSION,
        "showings": list(items),
    }


def freeze_monitor_today(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "israel_today", lambda: RUN_TEST_TODAY)


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


def test_missing_state_fails_loudly(tmp_path):
    try:
        load_state(tmp_path / "missing.json")
    except RuntimeError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Missing state must fail instead of creating a silent baseline")


def test_blank_state_fails_loudly(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("", encoding="utf-8")
    try:
        load_state(path)
    except RuntimeError as exc:
        assert "blank" in str(exc)
        assert "do not empty" in str(exc)
    else:
        raise AssertionError("Blank state must not reset the comparison baseline")


def test_malformed_state_fails_loudly(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json}", encoding="utf-8")
    try:
        load_state(path)
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("Malformed state must fail")


def test_wrong_state_version_fails_loudly(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"state_version": STATE_VERSION - 1, "showings": []}),
        encoding="utf-8",
    )
    try:
        load_state(path)
    except RuntimeError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("Version mismatch must fail")


def test_invalid_showings_shape_fails_loudly(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"state_version": STATE_VERSION, "showings": {}}),
        encoding="utf-8",
    )
    try:
        load_state(path)
    except RuntimeError as exc:
        assert "showings" in str(exc)
    else:
        raise AssertionError("Invalid showings shape must fail")


def test_valid_empty_state_naturally_detects_all_current_showings():
    current = [showing("2026-07-19"), showing("2026-07-20", "10:00")]
    new_items = find_new_showings(valid_state(), current)
    assert len(new_items) == 2


def test_current_state_detects_only_missing_showing():
    known = showing("2026-07-19")
    new = showing("2026-07-20", "10:00")
    new_items = find_new_showings(valid_state(known), [known, new])
    assert len(new_items) == 1
    assert new_items[0]["date"] == "2026-07-20"
    assert new_items[0]["weekday"] == "יום שני"


def test_run_sends_normal_alert_for_genuinely_new_showing(monkeypatch):
    freeze_monitor_today(monkeypatch)
    known = showing("2026-07-19")
    new = showing("2026-07-20", "10:00")
    previous = valid_state(known)
    sent_messages: list[str] = []
    saved_showings: list[dict[str, str]] = []

    async def fake_scrape_showings():
        return [known, new]

    monkeypatch.setattr(monitor, "scrape_showings", fake_scrape_showings)
    monkeypatch.setattr(monitor, "load_state", lambda: previous)
    monkeypatch.setattr(
        monitor,
        "telegram_send_many",
        lambda messages: sent_messages.extend(messages),
    )
    monkeypatch.setattr(
        monitor,
        "save_state",
        lambda showings: saved_showings.extend(showings),
    )

    result = asyncio.run(monitor.run(send_test=False))

    assert result == 0
    assert len(sent_messages) == 1
    assert "נמצאו הקרנות IMAX חדשות" in sent_messages[0]
    assert "יום שני, 20/07/2026" in sent_messages[0]
    assert "יום ראשון, 19/07/2026" not in sent_messages[0]
    assert len(saved_showings) == 2


def test_run_does_not_notify_when_nothing_is_new(monkeypatch):
    freeze_monitor_today(monkeypatch)
    known = showing("2026-07-19")
    previous = valid_state(known)
    sent_messages: list[str] = []

    async def fake_scrape_showings():
        return [known]

    monkeypatch.setattr(monitor, "scrape_showings", fake_scrape_showings)
    monkeypatch.setattr(monitor, "load_state", lambda: previous)
    monkeypatch.setattr(
        monitor,
        "telegram_send_many",
        lambda messages: sent_messages.extend(messages),
    )
    monkeypatch.setattr(monitor, "save_state", lambda showings: None)

    assert asyncio.run(monitor.run(send_test=False)) == 0
    assert sent_messages == []


def test_state_is_not_advanced_when_telegram_fails(monkeypatch):
    freeze_monitor_today(monkeypatch)
    known = showing("2026-07-19")
    new = showing("2026-07-20", "10:00")
    previous = valid_state(known)
    save_called = False

    async def fake_scrape_showings():
        return [known, new]

    def fail_send(messages):
        raise RuntimeError("Telegram unavailable")

    def record_save(showings):
        nonlocal save_called
        save_called = True

    monkeypatch.setattr(monitor, "scrape_showings", fake_scrape_showings)
    monkeypatch.setattr(monitor, "load_state", lambda: previous)
    monkeypatch.setattr(monitor, "telegram_send_many", fail_send)
    monkeypatch.setattr(monitor, "save_state", record_save)

    try:
        asyncio.run(monitor.run(send_test=False))
    except RuntimeError as exc:
        assert "Telegram unavailable" in str(exc)
    else:
        raise AssertionError("Telegram failure must propagate")

    assert not save_called


def test_send_test_does_not_scrape_or_modify_state(monkeypatch):
    sent: list[str] = []

    async def forbidden_scrape():
        raise AssertionError("sanity check must not scrape")

    monkeypatch.setattr(monitor, "scrape_showings", forbidden_scrape)
    monkeypatch.setattr(monitor, "telegram_send_one", sent.append)

    assert asyncio.run(monitor.run(send_test=True)) == 0
    assert len(sent) == 1
    assert "בדיקת שפיות" in sent[0]


def test_missing_future_showing_is_retained_in_state():
    previous = valid_state(showing("2026-07-22", "10:00"))
    merged = merge_known_showings(previous, current=[], today=date(2026, 7, 19))
    assert len(merged) == 1
    assert merged[0]["date"] == "2026-07-22"
    assert merged[0]["weekday"] == "יום רביעי"


def test_reappearing_showing_is_not_reported_again():
    original = showing("2026-07-22", "10:00")
    previous = valid_state(original)
    retained = merge_known_showings(previous, current=[], today=date(2026, 7, 19))
    assert find_new_showings(valid_state(*retained), [original]) == []


def test_past_showings_are_pruned_from_known_state():
    previous = valid_state(showing("2026-07-18"), showing("2026-07-19"))
    merged = merge_known_showings(previous, current=[], today=date(2026, 7, 19))
    assert [item["date"] for item in merged] == ["2026-07-19"]


def test_save_state_adds_weekday_and_preserves_valid_structure(tmp_path):
    path = tmp_path / "state.json"
    save_state([showing("2026-07-20")], path)
    saved = load_state(path)
    assert saved["state_version"] == STATE_VERSION
    assert saved["showings"][0]["weekday"] == "יום שני"
    assert set(saved) == {"state_version", "showings"}


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
    items = [showing(f"2026-07-{day:02d}") for day in range(1, 32)]
    messages = alert_messages(items)
    assert len(messages) > 1
    assert all(len(message) <= 3900 for message in messages)
