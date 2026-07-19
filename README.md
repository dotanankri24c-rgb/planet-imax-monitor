# Planet IMAX Telegram Monitor

Monitors Planet Rishon LeZion for validated IMAX screenings of **The Odyssey / האודיסאה** and sends Telegram notifications when genuinely new screenings appear.

## Telegram setup

Store these repository secrets under **Settings → Secrets and variables → Actions**:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The token and chat ID must never be committed to the repository.

## Notification modes

The monitor has three separate notification paths:

1. **Sanity check** — verifies only that Telegram is reachable.
2. **New screenings** — sends screenings not already recorded in the state.
3. **Forced current snapshot** — always sends a diagnostic message containing the screenings detected in the current run. If no screenings are detected, it still sends a Telegram message saying so.

### Reliable one-shot forced notification

Use valid JSON in `state.json`:

```json
{
  "state_version": 5,
  "force_notify": true,
  "showings": []
}
```

Commit and push it together with the current `monitor.py`. The next run will:

1. print `Forced notification requested: True`;
2. print `Notification decision: mode=forced-current-snapshot`;
3. send at least one Telegram message, even if the scraper found zero screenings;
4. print the Telegram `message_id` returned by the API;
5. save a normal state without `force_notify`, consuming the request once.

A blank state file is still recognized for backward compatibility, but the explicit JSON flag is the preferred mechanism.

### Force from the Actions interface

Open **Actions → Planet IMAX monitor → Run workflow** and set:

- **Send Telegram sanity-check message only:** false
- **Send a current-screenings notification even if nothing is new:** true

This uses the `--force-notify` command-line option and does not require editing `state.json`.

## State behavior

- Every showing includes its Hebrew weekday in `state.json` and Telegram.
- Known future screenings are retained when Planet temporarily fails to render a date.
- Past dates are pruned automatically.
- The optional `force_notify` field is never written back by `save_state`, so it is one-shot.
- Missing state uses first-run baseline protection.
- Malformed non-empty JSON fails loudly.

## Expected diagnostic log

A forced run should include lines similar to:

```text
Forced notification requested: True
Notification decision: mode=forced-current-snapshot, message_count=1
Telegram accepted message successfully; message_id=123
Telegram delivery completed for 1 message(s).
```

If the first two lines appear but the Telegram acceptance line does not, the workflow failed during the Telegram request. If the acceptance line appears, Telegram returned `ok: true` and supplied a concrete message ID.

## Manual checks

```bash
pip install -r requirements.txt
playwright install chromium
pytest -q
python monitor.py --send-test
python monitor.py --force-notify
```
