# Planet IMAX Telegram Monitor

Monitors Planet Rishon LeZion for validated IMAX screenings of **The Odyssey / האודיסאה** and sends Telegram notifications only when a screening appears that is not already recorded in `state.json`.

## Production behavior

Every normal run follows exactly one path:

1. scrape the current validated IMAX screenings;
2. load the previously committed state;
3. calculate `new screenings = current screenings - previous screenings`;
4. send a normal Telegram alert for those new screenings only;
5. save the merged state only after Telegram accepts the notification.

There is no forced-notification mode and no notification mode stored inside `state.json`.

## State safety

`state.json` is the comparison baseline and should not be emptied to test Telegram.

The monitor now fails loudly when the state is:

- missing;
- blank;
- malformed JSON;
- using the wrong state version;
- missing a valid `showings` list.

This prevents a damaged state from silently absorbing a genuinely new screening.

Planet occasionally omits an existing date during one scrape. Known future screenings are therefore retained until their date passes, preventing a disappearance/reappearance from producing duplicate alerts.

## Telegram sanity check

The manual **Send Telegram sanity-check message only** option verifies the bot token and chat ID. It does not scrape Planet, compare screenings, or change `state.json`.

It is only a connectivity test. Normal screening notifications always use the automatic comparison path.

## Diagnostic logs

For a real new screening, the workflow should print lines similar to:

```text
Previously known screenings: 12
Total validated IMAX screenings found this run: 13
New validated IMAX screenings found: 1
  NEW date=2026-07-23 weekday=יום חמישי time=20:30
Notification decision: mode=new-screenings, message_count=1
Telegram accepted message successfully; message_id=123
Telegram delivery completed for 1 message(s).
```

If Telegram fails, the state is not saved. The screening therefore remains new and will be retried in the next run.

## Local tests

```bash
pip install -r requirements.txt
playwright install chromium
pytest -q
```

The test suite includes an end-to-end regression test that starts with one known screening, returns one additional screening from the scraper, and verifies that the monitor sends the normal Hebrew “new IMAX screenings” alert containing only the new screening.
