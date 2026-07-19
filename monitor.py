#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

CINEMA_URL = os.getenv(
    "PLANET_CINEMA_URL",
    "https://www.planetcinema.co.il/cinemas/Rishon_Letziyon/1072",
)
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
STATE_VERSION = 3
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
BASE_URL = "https://www.planetcinema.co.il"
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "21"))

TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
NEXT_FORMAT_RE = re.compile(
    r"(?=VIP\s*2D|4DX\s*2D|SCREENX\s*2D|ICE\s*2D|2D\s+\d|$)",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    return " ".join((value or "").split())


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


def is_relevant(text: str) -> bool:
    folded = normalize(text).casefold()
    return "imax" in folded and any(term in folded for term in MOVIE_TERMS)


def extract_date(url: str) -> str:
    """
    Planet stores booking parameters inside the URL fragment, for example:
    ...#/buy-tickets-by-film?in-cinema=1072&at=2026-07-19
    """
    parsed = urlparse(url)

    query_values = parse_qs(parsed.query).get("at", [])
    if query_values:
        return query_values[0]

    fragment_query = parsed.fragment.split("?", 1)[1] if "?" in parsed.fragment else ""
    fragment_values = parse_qs(fragment_query).get("at", [])
    return fragment_values[0] if fragment_values else ""


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
    expected_date: str = "",
) -> list[dict[str, str]]:
    compact = normalize(context)
    match = re.search(r"IMAX\s*(?:2D|3D)?\s*(.*)", compact, flags=re.IGNORECASE)
    if not match:
        return []

    imax_section = NEXT_FORMAT_RE.split(match.group(1), maxsplit=1)[0]
    times = list(dict.fromkeys(TIME_RE.findall(imax_section)))
    screening_date = expected_date or extract_date(booking_url)

    return [
        {
            "cinema": CINEMA_NAME,
            "movie": MOVIE_NAME,
            "date": screening_date,
            "time": time,
            "format": FORMAT_NAME,
            "url": booking_url,
        }
        for time in times
    ]


async def extract_movie_card(page) -> str:
    return await page.evaluate(
        """(movieTerms) => {
          const terms = movieTerms.map(value => value.toLocaleLowerCase());
          let best = '';

          for (const element of document.querySelectorAll('body *')) {
            const text = (element.innerText || '').replace(/\\s+/g, ' ').trim();
            if (!text || text.length > 1800 || !text.includes('IMAX')) continue;

            const lower = text.toLocaleLowerCase();
            if (!terms.some(term => lower.includes(term))) continue;

            if (!best || text.length < best.length) {
              best = text;
            }
          }

          return best;
        }""",
        MOVIE_TERMS,
    )


async def scrape_showings(days_ahead: int = DAYS_AHEAD) -> list[dict[str, str]]:
    all_showings: list[dict[str, str]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126 Safari/537.36 PlanetShowtimeMonitor/3.0"
            ),
        )

        try:
            for offset in range(days_ahead + 1):
                day = date.today() + timedelta(days=offset)
                day_text = day.isoformat()
                booking_url = build_booking_url(day)

                print(f"Scanning {day_text}: {booking_url}")

                await page.goto(
                    booking_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )

                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except PlaywrightTimeoutError:
                    pass

                await page.wait_for_timeout(2_500)

                card_text = normalize(await extract_movie_card(page))
                if not card_text or not is_relevant(card_text):
                    print(f"  No IMAX card found for {day_text}")
                    continue

                day_showings = extract_imax_showings(
                    card_text,
                    booking_url,
                    expected_date=day_text,
                )
                print(
                    f"  Found {len(day_showings)} IMAX screening(s): "
                    + ", ".join(item["time"] for item in day_showings)
                )
                all_showings.extend(day_showings)
        finally:
            await browser.close()

    deduplicated = {
        showing_key(showing): showing
        for showing in all_showings
    }

    return sorted(
        deduplicated.values(),
        key=lambda showing: (
            showing.get("date", ""),
            showing.get("time", ""),
        ),
    )


def load_state(path: Path = STATE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def state_is_current(previous: dict[str, Any] | None) -> bool:
    return bool(previous and previous.get("state_version") == STATE_VERSION)


def save_state(
    showings: list[dict[str, str]],
    path: Path = STATE_PATH,
) -> None:
    payload = {
        "state_version": STATE_VERSION,
        "showings": showings,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
        item
        for item in current
        if showing_key(item) not in old_keys
    ]


def telegram_send(message: str) -> None:
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
    response.raise_for_status()

    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram returned an error: {body}")


def readable_date(value: str) -> str:
    if not value:
        return "תאריך לא זוהה"

    try:
        year, month, day = value.split("-")
        return f"{day}/{month}/{year}"
    except ValueError:
        return value


def format_showing(item: dict[str, str]) -> str:
    return (
        f"📅 {readable_date(item.get('date', ''))}\n"
        f"🕒 {item.get('time', 'שעה לא זוהתה')}\n"
        f"🎞 {item.get('format', FORMAT_NAME)}\n"
        f"🔗 {item.get('url', CINEMA_URL)}"
    )


def format_alert(items: list[dict[str, str]]) -> str:
    return (
        "🎬 נמצאו הקרנות IMAX חדשות של האודיסאה "
        "בפלאנט ראשון לציון:\n\n"
        + "\n\n".join(format_showing(item) for item in items)
    )


def format_baseline(showings: list[dict[str, str]]) -> str:
    if not showings:
        return (
            "✅ הניטור הופעל ונוצר baseline חדש.\n"
            "כרגע לא זוהו הקרנות IMAX של האודיסאה."
        )

    return (
        "✅ הניטור הופעל ונוצר baseline חדש.\n"
        f"נשמרו {len(showings)} הקרנות IMAX בלבד:\n\n"
        + "\n\n".join(format_showing(item) for item in showings)
    )


async def run(
    send_test: bool,
    notify_on_first_run: bool,
) -> int:
    if send_test:
        telegram_send(
            "✅ בדיקת שפיות הצליחה: "
            "בוט ניטור האודיסאה מחובר ל-Telegram."
        )
        print("Telegram sanity-check message sent.")
        return 0

    current = await scrape_showings()
    previous = load_state()
    baseline_run = not state_is_current(previous)
    new_items = find_new_showings(previous, current)

    print(f"Total IMAX screenings found: {len(current)}")
    for item in current:
        print(
            f"- date={item.get('date') or '?'} "
            f"time={item.get('time')} "
            f"format={item.get('format')}"
        )

    print(f"New IMAX screenings found: {len(new_items)}")
    print(f"Baseline/migration run: {baseline_run}")

    save_state(current)

    if baseline_run and notify_on_first_run:
        telegram_send(format_baseline(current))
    elif new_items:
        telegram_send(format_alert(new_items))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Send an immediate Telegram sanity-check message.",
    )
    parser.add_argument(
        "--notify-on-first-run",
        action="store_true",
        help="Notify after creating the initial baseline.",
    )
    args = parser.parse_args()

    try:
        return asyncio.run(
            run(args.send_test, args.notify_on_first_run)
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
