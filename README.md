# Telegram → Bale Forwarder

Forward messages from **Telegram channels or forum groups** to **Bale** channels with strict chronological order, album support, media compression for slow networks, and unattended daemon operation.

Supports:

- **Public channel** → one Bale channel (full history + live)
- **Private forum group** → different Bale channel per topic (mapped topics only)
- **SQLite queue** — resume after restarts without re-sending
- **Re-crawl on restart (default)** — discover newly arrived messages, skip already-forwarded rows
- **Strict order** — message *N+1* is not sent until *N* succeeds
- **Auto-reconnect** — Telegram disconnects are retried (daemon supervisor)

Uses [Telethon](https://github.com/LonamiWebs/Telethon) (user account) + [Bale Bot API](https://docs.bale.ai).

---

## Features

| Area | Behavior |
|------|----------|
| Text | Plain / HTML / markdown options; optional Telegram deep-link fallback |
| Photos | Albums use Bale `sendMediaGroup` when small; oversized albums fall back to per-part send |
| Documents | Oversized document albums skip `sendMediaGroup`; per-part send + 413-aware link fallback |
| Voice / audio | Opus re-encode ladder; compress-before-upload on slow links |
| Video | H.264 resize + CRF; confidence tiers: upload → compress → Telegram link |
| Forum topics | `TOPIC_TO_BALE_MAPPING` per topic; unlisted topics skipped |
| Order | `STRICT_SEND_ORDER=1`; auto-retry same message on failure |
| Daemon | Startup crawl (default) → send queue → live listener for new posts |

---

## Requirements

- Python **3.10+**
- **ffmpeg** on `PATH`, or bundled via `pip install imageio-ffmpeg` (see `FFMPEG_PATH`)
- Telegram account (API ID/hash from [my.telegram.org](https://my.telegram.org))
- Bale bot token from [tapi.bale.ai](https://tapi.bale.ai) — bot must be **admin** in destination channels

### Network (Iran, v2rayN, Clash) — automatic

**No configuration required by default.** On startup the forwarder:

1. Tries a **direct** connection to Telegram (works with TUN/global VPN).
2. If that fails, reads **Windows system proxy** (v2rayN “System Proxy”).
3. Scans common **local ports** (`10808`, `7890`, …) and uses the first proxy that reaches Telegram.
4. Sets **HTTP_PROXY** (Bale) and **Telethon proxy** (Telegram MTProto) for you.

You should see one of:

- `[Network] auto: direct Telegram OK …`
- `[Network] auto-detected local proxy …`
- `[Network] using Windows system proxy …`

Optional overrides (only if auto-detect is wrong):

| Variable | Values |
|----------|--------|
| `NETWORK_MODE` | `auto` (default), `direct`, `proxy` |
| `TUN_MODE` | legacy alias: `on` = direct, `off` = proxy only |
| `LOCAL_HTTP_PROXY` | force a specific URL (tried first) |
| `TELEGRAM_PROXY_URL` | e.g. `socks5://127.0.0.1:10808` |
| `NETWORK_TELETHON_PROBE` | `1` on Windows by default: real MTProto test before use (not just TCP) |

**Windows + v2rayN:** If logs showed `WinError 121` while v2rayN was running, that was Python’s default `ProactorEventLoop` breaking proxied Telethon sockets. The forwarder now switches to `WindowsSelectorEventLoopPolicy` automatically.

---

## Quick start

### 1. Clone and install

```bash
git clone <your-repo-url>
cd telegram-to-bale-forwarder
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

Copy an example env file and edit secrets (never commit these):

```bash
# Public channel → one Bale channel
cp .env.public.example .env.public

# OR forum group → per-topic Bale channels
cp .env.private.example .env.private
```

Required in every profile:

```env
API_ID=
API_HASH=
BALE_BOT_TOKEN=
TELEGRAM_PHONE=+98...
```

### 3. Public channel

`.env.public`:

```env
SOURCE_TO_BALE_MAPPING=@YourTelegramChannel->@YourBaleChannel
MODE=daemon
BACKFILL_LIMIT=0
DAEMON_INITIAL_CRAWL=1
DB_PATH=state.db
SEND_TOPIC_BY_TOPIC=0
STRICT_SEND_ORDER=1
RESET_SENT_ON_SEND_START=0
```

Run:

```powershell
# Windows
.\run_public.ps1

# Linux / manual
DOTENV_FILE=.env.public python main.py
```

### 4. Private forum (per-topic Bale channels)

**Step A — list topic IDs and names:**

```powershell
$env:DOTENV_FILE = ".env.private"
python scripts/list_forum_topics.py
```

**Step B — map topics** in `.env.private`:

```env
SOURCE_TO_BALE_MAPPING=https://t.me/+YourInviteHash->@fallback_bale
STRICT_TOPIC_ROUTING_SOURCES=https://t.me/+YourInviteHash
TOPIC_TO_BALE_MAPPING=2->@topic_a;4->@topic_b;6->@topic_c
INCLUDE_SEND_TOPIC_IDS=2,4,6
EXCLUDE_SEND_TOPIC_IDS=1
DB_PATH=state_private.db
```

Use the **exact** Bale `@username` from channel settings (case-sensitive). Verify:

```powershell
python scripts/verify_bale_channels.py
```

Run:

```powershell
.\run_private.ps1
```

---

## Modes

| `MODE` | Description |
|--------|-------------|
| `daemon` | **Recommended.** Startup crawl + send queue + live listener |
| `crawl` | Only enqueue messages into SQLite |
| `send` | Only drain the queue |
| `crawl_then_send` | One-shot crawl then send (no live) |
| `discover_topics` | Print forum `topic_id` + message counts |

---

## Two profiles (do not mix queues)

Run **public** and **private** with separate env files and databases:

| Profile | Env file | DB | Launch |
|---------|----------|-----|--------|
| Public | `.env.public` | `state.db` | `run_public.ps1` |
| Private forum | `.env.private` | `state_private.db` | `run_private.ps1` |

Only one forwarder process at a time (shared `session` file).

---

## Fresh start vs resume

| Goal | Action |
|------|--------|
| **Resume** | Keep `state*.db`, `RESET_SENT_ON_SEND_START=0`, restart |
| **From first** | Delete `state*.db` (+ `-wal`/`-shm`), restart |

---

## Media pipeline (slow networks)

Defaults favor smaller uploads (e.g. Iran → Bale):

1. **Confidence gate (metadata)** may skip download/upload and post Telegram link directly for low-probability cases (size-based).
2. Optional **compress before upload** (`COMPRESS_SMALL_FIRST=1`).
3. Upload to Bale (retries + backoff on transient errors).
4. On failure → **ffmpeg** compress and retry (bucket-specific).
5. For large/fragile albums: skip single huge `sendMediaGroup` and send parts individually.
6. On unrecoverable/low-confidence paths (including HTTP 413 for non-audio buckets) → text + **t.me** link.

Tune via `.env.example` (bitrates, CRF, size thresholds).

### Startup backfill behavior

By default, daemon startup re-scans Telegram history and updates queue rows:

- already-forwarded messages are skipped,
- unsent messages are (re)queued and forwarded in strict order,
- live listener then continues with new incoming messages.

Use `DAEMON_SKIP_CRAWL_IF_QUEUED=1` only if you intentionally want to skip startup crawl when queue already has rows.

---

## Telegram resilience

On disconnect, the daemon reconnects and continues (no manual restart):

- `DAEMON_SUPERVISOR=1`
- `TELEGRAM_RECOVER_DELAY_SECONDS=15`
- `TELEGRAM_OP_MAX_RETRIES=8`

---

## Project layout

```
├── main.py                 # Forwarder core
├── cli.py                  # Optional CLI helper
├── setup.py                # Interactive .env wizard
├── run_public.ps1          # Public profile (Windows)
├── run_private.ps1         # Private profile (Windows)
├── .env.example            # All options documented
├── .env.public.example
├── .env.private.example
├── scripts/                # Topics list, Bale verify, E2E tests
├── requirements.txt
└── LICENSE
```

---

## Testing

See `scripts/TEST_PLAN.md` and:

```powershell
py scripts/generate_test_env.py
.\scripts\run_e2e_from_zero.ps1
```

---

## Troubleshooting

| Problem | Check |
|---------|--------|
| Bale `404 no such group or user` | Exact `@username` from Bale; bot is channel admin |
| Stuck on one message | `forwarder_*.log`; strict order waits for that message |
| `database is locked` | Only one process using `session` |
| ffmpeg / Opus errors | Set `FFMPEG_PATH` or `pip install imageio-ffmpeg` |
| Wrong send order (public) | `SEND_TOPIC_BY_TOPIC=0` for channels |

---

## Publishing to GitHub

1. Run `.\scripts\pre_publish_check.ps1` (or review `git status` manually).
2. Confirm `.gitignore` excludes `.env*`, `session*`, `*.db`, `temp/`, logs.
3. Do **not** commit credentials or `state.db`.
4. See [docs/OPEN_SOURCE_PLAN.md](docs/OPEN_SOURCE_PLAN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

> **Deprecated (do not use):** `install.sh`, `setup.sh`, `cli.py` — old single-channel install. Use `run_public.ps1` or `run_private.ps1` instead.

---

## License

MIT — see [LICENSE](LICENSE).

---

<p align="right"><a href="./README.fa.md">فارسی</a></p>
