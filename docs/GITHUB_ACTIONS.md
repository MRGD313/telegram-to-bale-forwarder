# Free daily forwarding with GitHub Actions

Run the forwarder **once a day on GitHub Actions** instead of keeping a server awake.
This is the cheapest cloud option for low-volume channels: no VPS, no Render worker,
no webhook, and no Telegram bot admin on the source side.

The workflow is [`.github/workflows/daily-forward.yml`](../.github/workflows/daily-forward.yml).

## How it works

1. GitHub starts the job on a schedule (default: **13:37 UTC = 17:07 Iran**).
2. The job connects to Telegram with a **user session** (Telethon), crawls only
   messages newer than `MIN_TG_MESSAGE_ID`, and forwards them to Bale in order.
3. The SQLite queue (`state/state.db`) is committed back to your repository so the
   next run resumes without duplicates.
4. No messages? The run exits in seconds. A monthly heartbeat commit keeps GitHub
   from disabling the schedule after 60 days of inactivity.

Because it uses a user session instead of a bot, it can read the source channel
without adding a Telegram bot as admin, and it can download media larger than the
Bot API's 20 MB limit (Bale accepts up to 50 MB per file).

> Public repositories get unlimited GitHub Actions minutes on standard runners.
> A daily run of this job typically takes 10–60 seconds (a few minutes when it
> uploads media), so it costs nothing.

## Requirements

- A Telegram account that can read the source channel (member is enough for a
  public channel; admin is needed for a private one).
- Telegram `API_ID` / `API_HASH` from [my.telegram.org](https://my.telegram.org).
- A Bale bot token. The bot must be an **admin with “post messages”** in the
  destination Bale channel.
- A logged-in Telethon session (`session.session`), created by running the
  forwarder locally once.

## Step 1 — Create the session locally

If you don't already have an authorized `session.session`, log in once from a
machine that can reach Telegram:

```bash
pip install -r requirements.txt
cp .env.public.example .env.public   # edit API_ID, API_HASH, BALE_BOT_TOKEN, mapping
DOTENV_FILE=.env.public python scripts/telegram_login.py
```

The script sends a login code and completes the sign-in. On a filtered network,
set `TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808` (or your proxy) for this step.

## Step 2 — Export a StringSession

GitHub runners have no persistent disk, so the SQLite session file is replaced by a
`StringSession` stored in a secret:

```bash
python scripts/export_session_string.py --env-file .env.public --out session_string.txt
```

`session_string.txt` is a credential. **Never commit it.** Copy its contents into a
GitHub secret (next step), then delete the file.

## Step 3 — Add repository secrets

Repository → **Settings → Secrets and variables → Actions → Secrets → New repository secret**:

| Secret | Value |
|---|---|
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `BALE_BOT_TOKEN` | Bale bot token |
| `TELEGRAM_SESSION` | Contents of `session_string.txt` |

## Step 4 — Add repository variables

Same page → **Variables → New repository variable**:

| Variable | Required | Example | Notes |
|---|---|---|---|
| `ENABLE_DAILY_FORWARD` | **yes** | `true` | Opt-in guard; without it the scheduled job is skipped |
| `SOURCE_TO_BALE_MAPPING` | **yes** | `@mychannel->@mybalechannel` | Same syntax as `.env`; separate multiple sources with `;` |
| `MIN_TG_MESSAGE_ID` | recommended | `368` | Only messages with a **larger** id are forwarded |
| `DB_PATH` | no | `state/state.db` | Default shown; keep it inside `state/` so it is committed |
| `TELEGRAM_PHONE` | no | `+98912...` | Not needed with a valid StringSession |
| `TOPIC_TO_BALE_MAPPING` | no | `2->@refs;4->@highlights` | Forum groups |
| `STRICT_TOPIC_ROUTING_SOURCES` | no | `https://t.me/+invite` | Forum sources that must use topic mapping |
| `INCLUDE_SEND_TOPIC_IDS` / `EXCLUDE_SEND_TOPIC_IDS` | no | `2,4,6` | Topic filters |
| `FORCE_BALE_CHAT` | no | `@testchannel` | Send everything to one Bale channel |

### Choosing `MIN_TG_MESSAGE_ID`

If you leave it at `0`, the first run forwards the **entire history** of the source
channel. To start from “now”, set it to the current latest message id:

```bash
python scripts/get_channel_latest_id.py --chat @mychannel --env-file .env.public
```

The script prints a ready-to-paste `MIN_TG_MESSAGE_ID=...` line.

## Step 5 — Adjust the schedule

Edit the cron in `.github/workflows/daily-forward.yml`:

```yaml
on:
  schedule:
    - cron: "37 13 * * *"   # UTC
```

Cron is always UTC. Examples:

| Local time (Iran, UTC+3:30) | UTC cron |
|---|---|
| 17:00 | `30 13 * * *` |
| 17:07 | `37 13 * * *` |
| 08:00 | `30 4 * * *` |
| Every 6 hours | `0 */6 * * *` |

GitHub may start scheduled jobs a few minutes late. For daily catch-up this is
usually fine; if you need an exact time, use a paid scheduler.

## Step 6 — Test manually

Actions → **Daily forward (Telegram → Bale)** → **Run workflow**. For testing you can
override `min_tg_message_id` (for example, set it one lower to re-send the latest
message).

Check the job log for `[Crawl]`, `[Send]`, and `[OK]` lines, then verify the post in
Bale. The first successful run creates `state/state.db` and commits it.

## State, duplicates, and limits

- `state/state.db` stores message ids/status only (no message text). It is committed
  to the repository so each run resumes where the previous one stopped.
- If a run is killed after Bale accepted a message but before the state commit, that
  message can be forwarded twice on the next run. This is rare.
- Messages deleted from Telegram before the daily run are lost (Telegram no longer
  returns them).
- Blog/media files larger than 50 MB cannot be uploaded to Bale; the forwarder falls
  back to a `https://t.me/...` link when configured.
- GitHub disables scheduled workflows after **60 days without repository activity**.
  The workflow writes `state/heartbeat.txt` on the first day of each month to prevent
  this. If you disable state commits, re-enable the workflow manually.
- Private repositories have a monthly Actions minutes quota (2,000 minutes on the
  Free plan); a daily run normally fits easily.

## Security notes

- Store `TELEGRAM_SESSION`, `API_ID`, `API_HASH`, and `BALE_BOT_TOKEN` only in
  repository secrets. Never commit `.env` files or session strings.
- The StringSession grants full access to the Telegram account. Treat it like a
  password. If it leaks, terminate the session in Telegram → Settings → Devices.
- A public repository exposes `state/state.db` (message ids, dates, kinds, and status
  only). If that metadata is sensitive, use a private fork/repository.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Job is skipped | Set variable `ENABLE_DAILY_FORWARD=true` |
| `Set secret ...` error | Add the missing secret under Settings → Secrets |
| `ValueError: SOURCE_TO_BALE_MAPPING is empty` | Set the repository variable |
| Telegram login prompt / `Session is not authorized` | Re-export the StringSession |
| Bale `403 permission_denied` | Add the bot as channel admin with “post messages” |
| Bale `413` / upload timeouts | Increase timeouts or let the Telegram-link fallback handle it |
| No new messages | Expected; the job prints `Nothing left to send` |
| `git push` fails with 403 | Settings → Actions → General → Workflow permissions → **Read and write permissions** |
| Push rejected by branch protection | Allow `github-actions[bot]` to push, or run the workflow from an unprotected branch |
