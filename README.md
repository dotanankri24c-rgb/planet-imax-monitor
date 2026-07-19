# Planet IMAX Telegram Monitor

Monitors the public Planet Rishon LeZion cinema page for links whose surrounding
text contains both **The Odyssey / האודיסאה** and **IMAX**. When a new matching
item appears, the workflow sends a Telegram notification.

## Cost

Use a **public repository** so scheduled GitHub-hosted Actions are free.
Never place the Telegram token in the repository; keep it in GitHub Actions secrets.

## Setup

1. Create a public GitHub repository and upload these files.
2. Create a Telegram bot by chatting with `@BotFather`, then copy its token.
3. Send any message to the new bot.
4. Open the following URL in a browser, replacing `<TOKEN>`:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find `message.chat.id` in the JSON response.
6. In the GitHub repository, open:
   **Settings → Secrets and variables → Actions → New repository secret**
7. Add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
8. Open **Actions → Planet IMAX monitor → Run workflow**.
   Leave **Send Telegram sanity-check message** enabled.
9. Confirm that Telegram receives:
   `✅ בדיקת שפיות הצליחה...`
10. Run it again with `send_test=false` and `notify_on_first_run=true`.
    This creates the initial baseline and sends a second confirmation.

## Safety behavior

- One public-page check every five minutes.
- No login, seat selection, reservation, or purchasing.
- The monitor uses a normal browser renderer because showtimes are loaded dynamically.
- Initial discovery does not generate a false “new showtime” alert.
- A state file is committed only when the detected result set changes.

## Manual local checks

```bash
pip install -r requirements.txt
playwright install chromium
pytest -q
python monitor.py --send-test
python monitor.py --notify-on-first-run
```

## Troubleshooting

A failed scheduled run is visible under the repository's **Actions** tab.
The job logs report how many relevant and new items were detected. If Planet
changes its page structure, tests can still pass while scraping returns zero;
the initial-baseline confirmation and run logs make that visible.
