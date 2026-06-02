# Changelog

## Unreleased

### Added
- Daemon mode with SQLite queue (crawl, send, live)
- Forum topic routing (`TOPIC_TO_BALE_MAPPING`)
- Strict send order and auto-retry
- Media compression pipeline (ffmpeg) and Telegram link fallback
- Telegram disconnect recovery and daemon supervisor
- Public/private profiles (`run_public.ps1`, `run_private.ps1`)
- Scripts: `list_forum_topics`, `verify_bale_channels`, E2E tests
- Open-source docs: `docs/OPEN_SOURCE_PLAN.md`, `docs/ARCHITECTURE.md`

### Changed
- Deprecated `install.sh` / old `setup.sh` flow (see README)
- Daemon startup now re-crawls history by default (`DAEMON_SKIP_CRAWL_IF_QUEUED=0`) to catch messages that arrived while offline.
- Media reliability tiers expanded:
  - size-based metadata gate can skip directly to Telegram-link fallback,
  - oversized photo/document albums bypass `sendMediaGroup` and send per-part,
  - non-audio HTTP 413 upload failures force immediate Telegram-link fallback.
