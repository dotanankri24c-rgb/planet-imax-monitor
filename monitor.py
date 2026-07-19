#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

MOVIE_PAGE_URL = os.getenv(
    "PLANET_MOVIE_URL",
    "https://www.planetcinema.co.il/films/the-odyssey/7460s2r",
)
MOVIE_ID = os.getenv("PLANET_MOVIE_ID", "7460s2r")
CINEMA_ID = os.getenv("PLANET_CINEMA_ID", "1072")
MOVIE_TERMS = [
    value.strip().casefold()
    for value in os.getenv("MOVIE_TERMS", "האודיסאה,the odyssey").split(",")
    if value.strip()
]
MOVIE_NAME = os.getenv("MOVIE_NAME", "האודיסאה")
CINEMA_NAME = "Planet Rishon LeZion"
FORMAT_NAME = "IMAX"
STATE_VERSION = 5
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "21"))
TELEGRAM_MAX_TEXT = 3900
ISRAEL_TIMEZONE = ZoneInfo("Asia/Jerusalem")

HEBREW_WEEKDAYS = (
    "יום שני",
    "יום שלישי",
    "יום רביעי",
    "יום חמישי",
    "יום שישי",
    "יום שבת",
    "יום ראשון",
)

TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
NEXT_FORMAT_RE = re.compile(
    r"(?=VIP\s*2D|4DX\s*2D|SCREENX\s*2D|ICE\s*2D|2D\s+\d|$)",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    return " ".join((value or "").split())


def israel_today() -> date:
    return datetime.now(ISRAEL_TIMEZONE).date()


def weekday_name(value: str) -> str:
    if not value:
        return "יום לא זוהה"

    try:
        return HEBREW_WEEKDAYS[date.fromisoformat(value).weekday()]
    except ValueError:
        return "יום לא זוהה"


def canonical_showing(item: dict[str, str]) -> dict[str, str]:
    screening_date = item.get("date", "")
    return {
        "cinema": item.get("cinema", CINEMA_NAME),
        "movie": item.get("movie", MOVIE_NAME),
        "date": screening_date,
        "weekday": weekday_name(screening_date),
        "time": item.get("time", ""),
        "format": item.get("format", FORMAT_NAME),
        "url": item.get("url", MOVIE_PAGE_URL),
    }


def showing_key(item: dict[str, str]) -> str:
    identity = {
        "cinema": item.get("cinema", ""),
        "movie": item.get("movie", ""),
        "date": item.get("date", ""),
        "time": item.get("time", ""),
        "format": item.get("format", ""),
    }
    material = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


stable_key = showing_key


def showing_sort_key(showing: dict[str, str]) -> tuple[str, str]:
    return (
        showing.get("date", ""),
        showing.get("time", ""),
    )


def is_relevant(text: str) -> bool:
    folded = normalize(text).casefold()
    return "imax" in folded and any(term in folded for term in MOVIE_TERMS)


def extract_date(url: str) -> str:
    parsed = urlparse(url)

    direct = parse_qs(parsed.query).get("at", [])
    if direct:
        return direct[0]

    fragment_query = parsed.fragment.split("?", 1)[1] if "?" in parsed.fragment else ""
    fragment = parse_qs(fragment_query).get("at", [])
    return fragment[0] if fragment else ""


def build_booking_url(day: date) -> str:
    day_text = day.isoformat()
    return (
        f"{MOVIE_PAGE_URL}"
        f"#/buy-tickets-by-film?"
        f"in-cinema={CINEMA_ID}&"
        f"at={day_text}&"
        f"for-movie={MOVIE_ID}&"
        f"view-mode=list"
    )


def extract_imax_showings(
    context: str,
    booking_url: str,
    screening_date: str,
) -> list[dict[str, str]]:
    compact = normalize(context)
    match = re.search(r"IMAX\s*(?:2D|3D)?\s*(.*)", compact, flags=re.IGNORECASE)
    if not match:
        return []

    imax_section = NEXT_FORMAT_RE.split(match.group(1), maxsplit=1)[0]
    times = list(dict.fromkeys(TIME_RE.findall(imax_section)))

    return [
        canonical_showing(
            {
                "cinema": CINEMA_NAME,
                "movie": MOVIE_NAME,
                "date": screening_date,
                "time": time,
                "format": FORMAT_NAME,
                "url": booking_url,
            }
        )
        for time in times
    ]


async def extract_movie_card(page) -> dict[str, Any]:
    return await page.evaluate(
        """(movieTerms) => {
          const terms = movieTerms.map(value => value.toLocaleLowerCase());
          let bestNode = null;
          let bestText = '';

          for (const element of document.querySelectorAll('body *')) {
            const text = (element.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 1800 || !text.includes('IMAX')) continue;

            const lower = text.toLocaleLowerCase();
            if (!terms.some(term => lower.includes(term))) continue;

            if (!bestText || text.length < bestText.length) {
              bestNode = element;
              bestText = text;
            }
          }

          if (!bestNode) return {text: '', links: []};

          const links = [...bestNode.querySelectorAll('a[href]')]
            .map(anchor => anchor.href || '')
            .filter(Boolean);

          return {text: bestText, links};
        }""",
        MOVIE_TERMS,
    )


def card_has_requested_date(card_links: list[str], requested_date: str) -> bool:
    """
    Reject stale/default content.

    Planet may keep showing the currently available day even when the URL hash
    requests another date. We accept a day only when at least one booking link
    inside the actual movie card contains that exact date.
    """
    return any(extract_date(link) == requested_date for link in card_links)


def choose_booking_link(card_links: list[str], requested_date: str, fallback: str) -> str:
    for link in card_links:
        if extract_date(link) == requested_date:
            return link
    return fallback


async def scrape_showings(days_ahead: int = DAYS_AHEAD) -> list[dict[str, str]]:
    all_showings: list[dict[str, str]] = []
    first_day = israel_today()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126 Safari/537.36 PlanetShowtimeMonitor/5.0"
            ),
        )

        try:
            for offset in range(days_ahead + 1):
                day = first_day + timedelta(days=offset)
                day_text = day.isoformat()
                requested_url = build_booking_url(day)

                print(f"Scanning {day_text} ({weekday_name(day_text)}): {requested_url}")

                await page.goto(
                    requested_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )

                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except PlaywrightTimeoutError:
                    pass

                await page.wait_for_timeout(2_500)

                card = await extract_movie_card(page)
                card_text = normalize(card.get("text", ""))
                card_links = [
                    str(link)
                    for link in card.get("links", [])
                    if isinstance(link, str)
                ]

                if not card_text or not is_relevant(card_text):
                    print(f"  No Odyssey IMAX card found for {day_text}")
                    continue

                if not card_has_requested_date(card_links, day_text):
                    actual_dates = sorted(
                        {
                            extract_date(link)
                            for link in card_links
                            if extract_date(link)
                        }
                    )
                    print(
                        f"  Skipping stale/unavailable day {day_text}; "
                        f"card links refer to: {actual_dates or ['unknown']}"
                    )
                    continue

                booking_url = choose_booking_link(
                    card_links,
                    day_text,
                    requested_url,
                )
                day_showings = extract_imax_showings(
                    card_text,
                    booking_url,
                    day_text,
                )

                print(
                    f"  Found {len(day_showings)} IMAX screening(s): "
                    + ", ".join(item["time"] for item in day_showings)
                )
                all_showings.extend(day_showings)
        finally:
            await browser.close()

    deduplicated = {
        showing_key(showing): canonical_showing(showing)
        for showing in all_showings
    }

    return sorted(deduplicated.values(), key=showing_sort_key)


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    """Load and validate the persisted comparison state.

    The production monitor must never silently rebuild a missing or corrupted
    baseline, because doing so could absorb a genuinely new screening without a
    notification. Restore a valid committed state instead.
    """
    if not path.exists():
        raise RuntimeError(
            f"State file {path} is missing. Restore the committed state before running."
        )

    try:
        raw_state = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not read state file {path}: {exc}") from exc

    if not raw_state.strip():
        raise RuntimeError(
            f"State file {path} is blank. Restore the committed JSON state; "
            "do not empty it to test notifications."
        )

    try:
        value = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"State file {path} contains invalid JSON. Restore the committed state."
        ) from exc

    if not isinstance(value, dict):
        raise RuntimeError(f"State file {path} must contain a JSON object.")

    if value.get("state_version") != STATE_VERSION:
        raise RuntimeError(
            f"State file {path} has version {value.get('state_version')!r}; "
            f"expected {STATE_VERSION}. Update the state together with the code."
        )

    showings = value.get("showings")
    if not isinstance(showings, list) or any(
        not isinstance(item, dict) for item in showings
    ):
        raise RuntimeError(
            f"State file {path} must contain a 'showings' list of JSON objects."
        )

    return value

def state_is_current(previous: dict[str, Any] | None) -> bool:
    return bool(previous and previous.get("state_version") == STATE_VERSION)


def showing_is_not_past(item: dict[str, str], today: date) -> bool:
    try:
        return date.fromisoformat(item.get("date", "")) >= today
    except ValueError:
        return False


def merge_known_showings(
    previous: dict[str, Any] | None,
    current: list[dict[str, str]],
    today: date | None = None,
) -> list[dict[str, str]]:
    """
    Preserve all known, not-yet-past screenings.

    Planet's dynamically rendered page occasionally omits an entire date for a
    single run. Replacing the state with that incomplete scrape makes the same
    screenings look new when they reappear five minutes later. Keeping known
    future screenings prevents those duplicate Telegram alerts while expired
    dates are still removed automatically.
    """
    today = today or israel_today()
    merged: dict[str, dict[str, str]] = {}

    if state_is_current(previous):
        for item in previous.get("showings", []):
            if not isinstance(item, dict):
                continue
            normalized = canonical_showing(item)
            if showing_is_not_past(normalized, today):
                merged[showing_key(normalized)] = normalized

    for item in current:
        normalized = canonical_showing(item)
        if showing_is_not_past(normalized, today):
            merged[showing_key(normalized)] = normalized

    return sorted(merged.values(), key=showing_sort_key)


def save_state(
    showings: list[dict[str, str]],
    path: Path = STATE_PATH,
) -> None:
    payload = {
        "state_version": STATE_VERSION,
        "showings": [canonical_showing(item) for item in showings],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def find_new_showings(
    previous: dict[str, Any] | None,
    current: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not state_is_current(previous):
        return []

    old_keys = {
        showing_key(item)
        for item in previous.get("showings", [])
        if isinstance(item, dict)
    }

    return [
        canonical_showing(item)
        for item in current
        if showing_key(item) not in old_keys
    ]


def telegram_send_one(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret."
        )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(
            f"Telegram error {response.status_code}: {response.text}"
        )

    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram returned an error: {body}")

    result = body.get("result") or {}
    message_id = result.get("message_id", "unknown")
    print(f"Telegram accepted message successfully; message_id={message_id}")


def split_telegram_message(header: str, blocks: list[str]) -> list[str]:
    messages: list[str] = []
    current = header

    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= TELEGRAM_MAX_TEXT:
            current = candidate
            continue

        if current != header:
            messages.append(current)

        current = f"{header}\n\n{block}"
        if len(current) > TELEGRAM_MAX_TEXT:
            current = current[: TELEGRAM_MAX_TEXT - 3] + "..."

    if current:
        messages.append(current)

    return messages


def telegram_send_many(messages: list[str]) -> None:
    for message in messages:
        if message.strip():
            telegram_send_one(message)


def readable_date(value: str) -> str:
    if not value:
        return "תאריך לא זוהה"

    try:
        parsed = date.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return value


def format_showing(item: dict[str, str]) -> str:
    screening_date = item.get("date", "")
    weekday = item.get("weekday") or weekday_name(screening_date)
    return (
        f"📅 {weekday}, {readable_date(screening_date)}\n"
        f"🕒 {item.get('time') or 'שעה לא זוהתה'}\n"
        f"🎞 {item.get('format', FORMAT_NAME)}\n"
        f"🔗 {item.get('url', MOVIE_PAGE_URL)}"
    )


def alert_messages(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return []

    header = (
        "🎬 נמצאו הקרנות IMAX חדשות של האודיסאה "
        "בפלאנט ראשון לציון:"
    )
    return split_telegram_message(
        header,
        [format_showing(item) for item in items],
    )


async def run(send_test: bool) -> int:
    if send_test:
        telegram_send_one(
            "✅ בדיקת שפיות הצליחה: "
            "בוט ניטור האודיסאה מחובר ל-Telegram."
        )
        print("Telegram sanity-check message sent.")
        return 0

    current = await scrape_showings()
    previous = load_state()
    previous_showings = [
        item for item in previous.get("showings", []) if isinstance(item, dict)
    ]
    new_items = find_new_showings(previous, current)
    known_showings = merge_known_showings(previous, current)

    print(f"Previously known screenings: {len(previous_showings)}")
    print(f"Total validated IMAX screenings found this run: {len(current)}")
    for item in current:
        print(
            f"- date={item.get('date') or '?'} "
            f"weekday={item.get('weekday') or '?'} "
            f"time={item.get('time')} "
            f"format={item.get('format')}"
        )

    print(f"New validated IMAX screenings found: {len(new_items)}")
    for item in new_items:
        print(
            f"  NEW date={item.get('date') or '?'} "
            f"weekday={item.get('weekday') or '?'} "
            f"time={item.get('time')}"
        )
    print(f"Known not-yet-past screenings saved: {len(known_showings)}")

    messages = alert_messages(new_items)
    notification_mode = "new-screenings" if messages else "none"
    print(
        f"Notification decision: mode={notification_mode}, "
        f"message_count={len(messages)}"
    )

    if messages:
        telegram_send_many(messages)
        print(f"Telegram delivery completed for {len(messages)} message(s).")
    else:
        print("No Telegram notification required for this run.")

    # Persist only after successful notification handling. If Telegram fails, the
    # state is not advanced, so the same new screening will be retried next run.
    save_state(known_showings)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-test", action="store_true")
    args = parser.parse_args()

    try:
        return asyncio.run(run(args.send_test))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
