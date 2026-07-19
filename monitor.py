#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

CINEMA_URL = os.getenv(
    "PLANET_CINEMA_URL",
    "https://www.planetcinema.co.il/cinemas/Rishon_Letziyon/1072",
)
MOVIE_TERMS = [
    x.strip().casefold()
    for x in os.getenv("MOVIE_TERMS", "האודיסאה,the odyssey").split(",")
    if x.strip()
]
FORMAT_TERM = os.getenv("FORMAT_TERM", "IMAX").casefold()
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
BASE_URL = "https://www.planetcinema.co.il"


def normalize(value: str) -> str:
    return " ".join((value or "").split())


def stable_key(item: dict[str, str]) -> str:
    material = json.dumps(item, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def is_relevant(text: str) -> bool:
    folded = normalize(text).casefold()
    return FORMAT_TERM in folded and any(term in folded for term in MOVIE_TERMS)


async def scrape_showings(url: str = CINEMA_URL) -> list[dict[str, str]]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126 Safari/537.36 PlanetShowtimeMonitor/1.0"
            ),
        )
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=25_000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(4_000)

            candidates: list[dict[str, str]] = await page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  const anchors = [...document.querySelectorAll('a[href]')];

                  for (const a of anchors) {
                    let node = a;
                    let best = '';
                    for (let i = 0; i < 7 && node; i++, node = node.parentElement) {
                      const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
                      if (text.length > best.length && text.length < 1800) best = text;
                    }
                    const href = a.href || a.getAttribute('href') || '';
                    const label = (a.innerText || a.getAttribute('aria-label') || '').trim();
                    const item = {text: best, href, label};
                    const key = JSON.stringify(item);
                    if (!seen.has(key)) {
                      seen.add(key);
                      out.push(item);
                    }
                  }
                  return out;
                }"""
            )
        finally:
            await browser.close()

    relevant: list[dict[str, str]] = []
    for item in candidates:
        context = normalize(item.get("text", ""))
        if not is_relevant(context):
            continue
        href = urljoin(BASE_URL, item.get("href", ""))
        label = normalize(item.get("label", ""))
        relevant.append({"context": context, "label": label, "url": href})

    # Deduplicate while preserving distinct booking links/contexts.
    dedup: dict[str, dict[str, str]] = {}
    for item in relevant:
        dedup[stable_key(item)] = item
    return sorted(dedup.values(), key=lambda x: (x["context"], x["url"]))


def load_state(path: Path = STATE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(showings: list[dict[str, str]], path: Path = STATE_PATH) -> None:
    payload = {"showings": showings}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def find_new_showings(
    previous: dict[str, Any] | None,
    current: list[dict[str, str]],
) -> list[dict[str, str]]:
    if previous is None:
        return []
    old_keys = {
        stable_key(item)
        for item in previous.get("showings", [])
        if isinstance(item, dict)
    }
    return [item for item in current if stable_key(item) not in old_keys]


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


def format_alert(items: list[dict[str, str]]) -> str:
    lines = [
        "🎬 נמצאו הקרנות IMAX חדשות של האודיסאה בפלאנט ראשון לציון!",
        "",
    ]
    for i, item in enumerate(items[:8], 1):
        context = item["context"]
        if len(context) > 500:
            context = context[:497] + "..."
        lines.extend([f"{i}. {context}", item["url"], ""])
    if len(items) > 8:
        lines.append(f"ועוד {len(items) - 8} תוצאות.")
    return "\n".join(lines).strip()


async def run(send_test: bool, notify_on_first_run: bool) -> int:
    if send_test:
        telegram_send(
            "✅ בדיקת שפיות הצליחה: בוט ניטור האודיסאה מחובר ל-Telegram."
        )
        print("Telegram sanity-check message sent.")
        return 0

    current = await scrape_showings()
    previous = load_state()
    first_run = previous is None
    new_items = find_new_showings(previous, current)

    print(f"Relevant items found: {len(current)}")
    print(f"New items found: {len(new_items)}")
    print(f"First run: {first_run}")

    # Always update the baseline, even if zero showings are currently listed.
    save_state(current)

    if first_run and notify_on_first_run:
        telegram_send(
            "✅ הניטור הופעל ונוצר מצב התחלתי.\n"
            f"נמצאו כרגע {len(current)} תוצאות רלוונטיות."
        )
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
        return asyncio.run(run(args.send_test, args.notify_on_first_run))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
