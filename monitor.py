#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
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
MOVIE_TERMS = [
    value.strip().casefold()
    for value in os.getenv("MOVIE_TERMS", "האודיסאה,the odyssey").split(",")
    if value.strip()
]
MOVIE_NAME = os.getenv("MOVIE_NAME", "האודיסאה")
CINEMA_NAME = "Planet Rishon LeZion"
FORMAT_NAME = "IMAX"
STATE_VERSION = 2
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
BASE_URL = "https://www.planetcinema.co.il"

TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
NEXT_FORMAT_RE = re.compile(
    r"(?=VIP\s*2D|4DX\s*2D|SCREENX\s*2D|ICE\s*2D|2D\s+\d|$)",
    re.IGNORECASE,
)


def normalize(value: str) -> str:
    return " ".join((value or "").split())


def showing_key(item: dict[str, str]) -> str:
    """Create a stable identity for a single screening."""
    identity = {
        "cinema": item.get("cinema", ""),
        "movie": item.get("movie", ""),
        "date": item.get("date", ""),
        "time": item.get("time", ""),
        "format": item.get("format", ""),
    }
    material = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# Backward-compatible alias for the original tests/imports.
stable_key = showing_key


def is_relevant(text: str) -> bool:
    folded = normalize(text).casefold()
    return "imax" in folded and any(term in folded for term in MOVIE_TERMS)


def extract_date(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("at", [])
    return values[0] if values else ""


def extract_imax_showings(
    context: str,
    booking_url: str,
) -> list[dict[str, str]]:
    """
    Extract only IMAX screenings from a Planet movie card.

    Planet places several format sections in one text block. This function
    isolates the text after IMAX and before the next format heading, then
    extracts only the times in that section.
    """
    compact = normalize(context)
    match = re.search(r"IMAX\s*(?:2D|3D)?\s*(.*)", compact, flags=re.IGNORECASE)
    if not match:
        return []

    imax_section = NEXT_FORMAT_RE.split(match.group(1), maxsplit=1)[0]
    times = list(dict.fromkeys(TIME_RE.findall(imax_section)))
    date = extract_date(booking_url)

    return [
        {
            "cinema": CINEMA_NAME,
            "movie": MOVIE_NAME,
            "date": date,
            "time": time,
            "format": FORMAT_NAME,
            "url": booking_url or CINEMA_URL,
        }
        for time in times
    ]


async def scrape_showings(url: str = CINEMA_URL) -> list[dict[str, str]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126 Safari/537.36 PlanetShowtimeMonitor/2.0"
            ),
        )

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=25_000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(4_000)

            cards: list[dict[str, str]] = await page.evaluate(
                """(movieTerms) => {
                  const terms = movieTerms.map(value => value.toLocaleLowerCase());
                  const result = [];
                  const seen = new Set();

                  for (const anchor of document.querySelectorAll('a[href]')) {
                    const href = anchor.href || '';
                    const ownText = (anchor.innerText || '')
                      .replace(/\\s+/g, ' ')
                      .trim();

                    const looksLikeMovieLink =
                      href.includes('/films/the-odyssey/') ||
                      terms.some(term =>
                        ownText.toLocaleLowerCase().includes(term)
                      );

                    if (!looksLikeMovieLink) continue;

                    let node = anchor;
                    let cardText = '';

                    for (
                      let depth = 0;
                      depth < 8 && node;
                      depth++, node = node.parentElement
                    ) {
                      const text = (node.innerText || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                      const lower = text.toLocaleLowerCase();

                      if (
                        text.length < 1800 &&
                        text.includes('IMAX') &&
                        terms.some(term => lower.includes(term))
                      ) {
                        cardText = text;
                        break;
                      }
                    }

                    if (!cardText) continue;

                    const key = cardText + '\\n' + href;
                    if (!seen.has(key)) {
                      seen.add(key);
                      result.push({context: cardText, url: href});
                    }
                  }

                  return result;
                }""",
                MOVIE_TERMS,
            )
        finally:
            await browser.close()

    showings: list[dict[str, str]] = []

    for card in cards:
        context = normalize(card.get("context", ""))
        if not is_relevant(context):
            continue

        booking_url = urljoin(BASE_URL, card.get("url", ""))
        showings.extend(extract_imax_showings(context, booking_url))

    deduplicated = {
        showing_key(showing): showing
        for showing in showings
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
    # Treat the old noisy schema as a migration baseline, so upgrading does not
    # cause false "new screening" notifications.
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

    print(f"IMAX screenings found: {len(current)}")
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
