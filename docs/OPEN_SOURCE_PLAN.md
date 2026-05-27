# Open-source review & refactor plan

**Status:** Review only (no code changes that affect the running forwarder).  
**Date:** 2026-05-19

This document is the publish-readiness plan after a full project review. Apply phases **after** background forwarding finishes (or on a branch).

---

## 1. Executive summary

The **core forwarder (`main.py`)** is feature-rich and production-usable: SQLite queue, daemon mode, forum topic routing, strict order, media compression, Bale retries, Telegram reconnect. It is **ready to share conceptually**, but the repo is **not yet clean** for strangers to clone and run without confusion.

| Area | Verdict |
|------|---------|
| Core logic (`main.py`) | Strong; too large (≈2,780 lines) for long-term maintenance |
| Docs (`README.md`, examples) | Good after recent update |
| Legacy install path (`install.sh`, `setup.sh`, `cli.py`) | **Broken / obsolete** — conflicts with current `.env` schema |
| Helper scripts | One duplicate + one file with **your private URLs/handles** |
| Tests | E2E only; no unit tests |
| Secrets | Correctly gitignored locally; verify before first `git push` |

---

## 2. What works well (keep)

- **Dual profiles:** `.env.public` / `.env.private` + `run_*.ps1`
- **Queue model:** crawl → send → live; resume via `state*.db`
- **Forum:** `TOPIC_TO_BALE_MAPPING`, `INCLUDE_SEND_TOPIC_IDS`, `STRICT_TOPIC_ROUTING_SOURCES`
- **Reliability:** `STRICT_SEND_ORDER`, `SEND_AUTO_RETRY_ON_FAILURE`, Telegram `recover_telegram` + daemon supervisor
- **Media:** compress-small-first, ffmpeg ladder, Bale link fallback
- **Utilities:** `list_forum_topics.py`, `verify_bale_channels.py`, E2E scripts

---

## 3. Issues found (fix before or right after publish)

### 3.1 Security & privacy (critical)

| Item | Risk | Action |
|------|------|--------|
| `.env`, `.env.private`, `.env.mofidot` | Live credentials | Never commit; confirm `git status` |
| `session.session` | Telegram login | Gitignored; never push |
| `state*.db`, `forwarder_*.log` | Message metadata | Gitignored |
| `scripts/list_topics_and_bale.py` | Hardcoded invite link + your Bale @names | **Delete or sanitize** before publish |
| `.env.test.public` / `.env.test.private` | May copy real credentials from `generate_test_env.py` | Gitignore or generate only from examples |

### 3.2 Stale / misleading files (high)

| File | Problem |
|------|---------|
| `install.sh` | Clones `github.com/ach1992/telegram-to-bale` (old upstream) |
| `setup.sh` | Writes `BALE_CHAT_ID`, `SOURCE_CHANNELS` — **not used by `main.py`** |
| `cli.py` | systemd-only; README still mentions `teltobale` |
| `README.fa.md` | Old feature list (`backfill`, single channel); not translated for new architecture |
| `README.md` (old sections) | Removed in new README; FA still outdated |

### 3.3 Code structure (medium — post-forwarder)

| Issue | Detail |
|-------|--------|
| Monolith | ~70 functions in `main.py`; hard for contributors |
| Duplicate logic | `list_forum_topics.py` vs `list_topics_and_bale.py` |
| `setup.py` | Omits `TELEGRAM_PHONE` in generated `.env` |
| `generate_test_env.py` | Depends on `.env.mofidot` (user-specific), not `.env.public.example` |
| `GetForumTopics` pagination | Minor bug in `list_forum_topics.py` (`top_message_id`); fallback scan works |
| Naming | `backfill_limit` used for crawl limit — rename to `CRAWL_LIMIT` in docs/code (optional) |

### 3.4 Missing for a polished OSS repo (nice-to-have)

- `pyproject.toml` or minimal `setup.cfg` for `pip install -e .`
- GitHub Actions: lint + `py_compile` + optional dry-run tests
- `CHANGELOG.md`
- Issue templates (bug / feature)
- English + Persian README parity
- Screenshots / architecture diagram in README

---

## 4. Recommended module split (Phase 2 — no rush)

Split **without behavior change** (mechanical move + imports):

```
forwarder/
  __init__.py
  config.py          # load_dotenv, all os.getenv
  db.py              # sqlite queue
  telegram_client.py # client, recover, telegram_call, iter_messages_resilient
  telegram_topics.py # get_topic_id, collect_album, resolve source
  bale_api.py        # upload, text, media group, retries
  media.py           # ffmpeg, compress, tiers
  text_format.py     # entities, plain links, RTL
  crawl.py           # run_crawl
  send.py            # run_send, process_queue_row
  daemon.py          # run_daemon, live
  main.py            # thin entry: asyncio.run(main())
```

Keep a single `python -m forwarder` or `main.py` shim for backward compatibility.

---

## 5. Phased action plan

### Phase 0 — Safe to do anytime (no running process impact)

- [x] `README.md`, `LICENSE`, `SECURITY.md`, `.gitignore`, `.env.*.example`
- [ ] Add this plan: `docs/OPEN_SOURCE_PLAN.md`
- [ ] Pre-push checklist script: `scripts/pre_publish_check.ps1` (grep secrets, list forbidden paths)
- [ ] Mark `install.sh` / `setup.sh` / `cli.py` as **deprecated** in README (one paragraph)

### Phase 1 — Publish minimum (1–2 hours, stop forwarder not required)

Only edit files **not loaded** by running `main.py`:

| Task | Files |
|------|--------|
| Remove user-specific script | Delete `scripts/list_topics_and_bale.py` |
| Gitignore test env outputs | `.env.test.*`, `topics_*.txt` |
| Update `README.fa.md` or add “outdated — see README.md” banner | `README.fa.md` |
| Deprecate old installer | `install.sh` → README warning; or replace with “clone + pip install” |
| Fix `generate_test_env.py` | Read from `.env.*.example` only, never `.env.mofidot` |
| Add `docs/ARCHITECTURE.md` | Mermaid: Telegram → queue → Bale |

**Do not touch:** `main.py`, `session*`, `state_private.db`, while private forwarder runs.

### Phase 2 — After forwarding stable (requires restart to pick up `main.py` changes)

| Task | Notes |
|------|--------|
| Split `main.py` into package | Test with E2E script |
| Unify `setup.py` + examples | Include `TELEGRAM_PHONE`, `MODE=daemon` |
| Replace `setup.sh` / `cli.py` | Windows + Linux docs only, or modern CLI wrapping `run_*.ps1` |
| Rename env `BACKFILL_LIMIT` → `CRAWL_LIMIT` | Alias old name for compatibility |
| Fix `list_forum_topics.py` pagination | Use Telethon API fields correctly |
| Add unit tests | `parse_mapping`, `get_topic_id`, `db_fetch_batch` order |

### Phase 3 — Community

- GitHub Actions CI
- Sample demo video / GIF
- Pin issue: “Bale usernames are case-sensitive”
- Optional: PyPI name `telegram-bale-forwarder`

---

## 6. Pre-publish git checklist

```powershell
git status
# Must NOT appear:
#   .env .env.private .env.mofidot session* *.db forwarder_*.log

git grep -E "API_HASH=|BALE_BOT_TOKEN=[0-9]" -- ':!*.example' ':!docs/*'
# Should return nothing

git add README.md LICENSE SECURITY.md CONTRIBUTING.md .gitignore .env.example .env.*.example
git add main.py requirements.txt setup.py cli.py run_*.ps1 scripts/ docs/
# Review diff manually
```

---

## 7. Mapping reference (for your forum — not for repo)

Your current private mapping (local `.env.private` only):

| topic_id | Telegram topic | Bale |
|----------|----------------|------|
| 2 | منابع و ترجمه منابع | @references_iomi |
| 4 | خلاصه نکات مفید | @highlights_iomi |
| 6 | خلاصه منابع | @refsummary_iomi |
| 1519 | نکات نظردهی | @judgement_iomi |
| 2782 | کیس های علمی | @causation_iomi |

Others excluded via `INCLUDE_SEND_TOPIC_IDS` + strict routing.

---

## 8. Suggested repo metadata (GitHub)

- **Name:** `telegram-bale-forwarder` or `telegram-to-bale`
- **Description:** Forward Telegram channels and forum topics to Bale with strict order, albums, and media compression.
- **Topics:** `telegram`, `bale`, `telethon`, `forwarder`, `python`, `iran`
- **License:** MIT (already in repo)

---

## 9. Decision log

| Decision | Rationale |
|----------|-----------|
| Keep single `main.py` until forwarder idle | Avoid restart / import side effects |
| Deprecate ach1992 install.sh | Wrong schema + wrong remote |
| Prefer `list_forum_topics.py` | Generic; no hardcoded handles |
| Two env profiles in repo | Matches real public + private use cases |
| No commit of `.env.mofidot` | User-specific production config |

---

*Next step when you approve: execute **Phase 1** only (docs + scripts cleanup, zero `main.py` edits).*
