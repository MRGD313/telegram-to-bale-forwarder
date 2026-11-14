# Production setup (free, no server)

This is the end-to-end guide for running the forwarder in production with:

```
free external cron (cron-job.org / Cloudflare)
        │  POST workflow_dispatch
        ▼
GitHub Actions  .github/workflows/daily-forward.yml
        │  Telethon user session
        ▼
Telegram source channel  ──download/compress──►  Bale Bot API ──► Bale channel
        │
        └── state/state.db committed back to the repository
```

No VPS, no Render worker, no webhook, and no 24/7 process. The workflow runs once
per day (or at whatever schedule you choose) and costs **zero** on a public
GitHub repository.

> If you already run the old 24/7 daemon, this guide is an alternative deployment
> model. You can keep the daemon and this workflow in the same repository; they
> should not run against the same `state.db` at the same time.

---

## 0. What you need

- A Telegram account that can read the source channel/group.
- Telegram `API_ID` / `API_HASH` from [my.telegram.org](https://my.telegram.org).
- A Bale bot token from [tapi.bale.ai](https://tapi.bale.ai). The bot must be an
  **administrator with permission to post messages** in the destination Bale
  channel.
- A GitHub account and a fork of this repository.
- A free account at [cron-job.org](https://cron-job.org) (or a Cloudflare account
  if you prefer the Worker cron option).
- A fine-grained GitHub token for the external cron (created below).

---

## 1. Fork, clone, and reset inherited state

1. Fork this repository on GitHub.
2. Enable Actions in the fork (Actions tab → *I understand my workflows, go ahead
   and enable them* if asked).
3. Clone your fork locally:

   ```bash
   git clone https://github.com/<owner>/<repo>.git
   cd <repo>
   pip install -r requirements.txt
   ```

4. **Important:** a fork inherits the upstream repository's `state/state.db`
   (the upstream deployment's queue/mapping). Reset it before your first run:

   ```bash
   # macOS / Linux
   git rm -f state/state.db
   git commit -m "reset state for my deployment"
   git push

   # Windows PowerShell
   git rm -f state/state.db
   git commit -m "reset state for my deployment"
   git push
   ```

   The first workflow run recreates `state/state.db` for your deployment. Do not
   reuse the upstream queue: it belongs to a different Telegram/Bale pair.

---

## 2. Create the Telegram session and choose the starting point

The workflow uses a **StringSession** (not a session file), stored as a GitHub
secret. Create it locally:

```bash
cp .env.public.example .env.public
```

Edit `.env.public`:

```env
API_ID=...
API_HASH=...
BALE_BOT_TOKEN=...
SOURCE_TO_BALE_MAPPING=@your_source_channel->@your_bale_channel
```

Log in once (interactive):

```bash
DOTENV_FILE=.env.public python scripts/telegram_login.py
```

Export the StringSession:

```bash
python scripts/export_session_string.py --env-file .env.public --out session_string.txt
```

Find the latest message id of the source channel (so production starts from
"now" instead of replaying the entire history):

```bash
python scripts/get_channel_latest_id.py --chat @your_source_channel --env-file .env.public
```

The script prints `MIN_TG_MESSAGE_ID=<id>`. Save that number; it becomes a
repository variable below.

> `MIN_TG_MESSAGE_ID=0` means **full history**. Use it only if you intentionally
> want to backfill the whole channel, and test on a private Bale channel first.

**Network note:** if Telegram is filtered, set a proxy for these local scripts:

```bash
export TELEGRAM_PROXY_URL=socks5://127.0.0.1:10808
```

The GitHub-hosted runner does not need a proxy.

---

## 3. Configure GitHub secrets and variables

Repository → **Settings → Secrets and variables → Actions**.

### Secrets

| Secret | Value |
|---|---|
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `BALE_BOT_TOKEN` | Bale bot token |
| `TELEGRAM_SESSION` | Contents of `session_string.txt` |

After adding `TELEGRAM_SESSION`, delete `session_string.txt` locally.

### Variables

| Variable | Required | Example | Notes |
|---|---|---|---|
| `ENABLE_DAILY_FORWARD` | **yes** | `true` | The job stays skipped until this is `true` |
| `SOURCE_TO_BALE_MAPPING` | **yes** | `@source->@destination` | Same syntax as `.env`; multiple sources separated by `;` |
| `MIN_TG_MESSAGE_ID` | recommended | `12345` | Only messages with a **larger** id are forwarded; use the id printed by `scripts/get_channel_latest_id.py` |
| `DB_PATH` | no | `state/state.db` | Default; keep it inside `state/` |
| `TELEGRAM_PHONE` | no | `+98912...` | Not needed with a valid StringSession |
| `TOPIC_TO_BALE_MAPPING` | no | `2->@refs;4->@highlights` | Forum groups |
| `STRICT_TOPIC_ROUTING_SOURCES` | no | `https://t.me/+invite` | Forum sources that must use topic mapping |
| `INCLUDE_SEND_TOPIC_IDS` / `EXCLUDE_SEND_TOPIC_IDS` | no | `2,4,6` | Topic filters |
| `FORCE_BALE_CHAT` | no | `@testchannel` | Send everything to one Bale channel |

### Workflow permissions

Settings → **Actions → General → Workflow permissions** → **Read and write
permissions**.

The workflow commits `state/state.db` back to the repository. If the branch is
protected, allow `github-actions[bot]` to push, or run the workflow from an
unprotected branch.

---

## 4. Configure the external cron

The workflow is **dispatch-only** (`on: workflow_dispatch`). GitHub's own
`schedule` is best-effort and can be delayed by hours; a free external cron calls
the API at the exact time instead.

### 4.1 Create a fine-grained GitHub token

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**:

- **Repository access:** only your fork.
- **Permissions:**
  - **Actions:** Read and write
  - **Metadata:** Read (mandatory)
- **Expiration:** 90 days (rotate it).

This token can only trigger workflows; it cannot read repository secrets or
contents.

### 4.2 cron-job.org

Create a job:

| Field | Value |
|---|---|
| URL | `https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily-forward.yml/dispatches` |
| Method | `POST` |
| Schedule | every day at your chosen time |
| Timezone | `Asia/Tehran` (or UTC) |
| Headers | see below |
| Body | `{"ref":"main"}` |

Headers:

```
Authorization: Bearer <FINE_GRAINED_PAT>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Examples:

- 17:00 Iran → `0 17 * * *` with **Asia/Tehran**, or `30 13 * * *` with **UTC**.
- 19:00 Iran → `0 19 * * *` with **Asia/Tehran**, or `30 15 * * *` with **UTC**.

Enable the job and failure notifications. **Test run** should return
**HTTP 204** (No Content). A `204` creates a GitHub Actions run with event
`workflow_dispatch`.

> The Test run button only tests now; make sure the **daily schedule is saved
> and enabled** for the actual production trigger.

### 4.3 Cloudflare Worker (alternative)

If you prefer to keep the token as an encrypted Cloudflare secret, use a Worker
with a Cron Trigger. The code and `wrangler.toml` are in
[EXTERNAL_CRON.md](EXTERNAL_CRON.md#option-b--cloudflare-worker-cron-more-secure).

---

## 5. First run and verification

1. GitHub → Actions → **Daily forward (Telegram → Bale)** → **Run workflow**.
   Leave the input empty for a normal run.
2. Watch the log for:

   ```
   [Crawl] Done source=... scanned=N queued=N inserted=N updated=N
   [SendQueue] ...
   [Send] ...
   [OK] ...
   [SendQueue] Nothing left to send.
   ```

3. Verify the post in the destination Bale channel.
4. Verify that a new `chore: update forwarder state [skip ci]` commit appeared.
5. Run the external cron **Test run** once; confirm `204` and a new
   `workflow_dispatch` run on GitHub.

If there are no new messages, the run prints `Nothing left to send` and exits
successfully. That is expected.

---

## 6. What a daily run does

1. Connects to Telegram with the StringSession.
2. Fetches messages with `id > MIN_TG_MESSAGE_ID` (server-side `min_id`).
3. Inserts new rows into the SQLite queue; existing rows keep their status.
4. Sends oldest → newest, with `STRICT_SEND_ORDER=1`.
5. For media: downloads from Telegram, compresses when configured, uploads to
   Bale, or posts a Telegram link fallback.
6. Commits `state/state.db` back to the repository.

The state DB makes the next run resumable: already-sent messages are skipped.
It contains **metadata only** (ids, dates, kind, status, errors), not message text
or media.

Cost: GitHub Actions is free for public repositories on standard runners. A
daily run usually takes 10–60 seconds, or a few minutes when it uploads media.
External cron is free. No server is required.

---

## 7. Media and Bale size limits

- Bale accepts multipart uploads up to **50 MB** per file.
- The workflow downloads from Telegram with the **user session**, so Telegram's
  bot-API 20 MB download limit does not apply.
- Default size gate:

  ```env
  UPLOAD_LINK_ONLY_ABOVE_MB=60
  ```

  A source file **≥60 MB** is not downloaded/compressed; the workflow posts the
  caption plus a `https://t.me/...` link instead. This is a safety gate for slow
  links and to avoid blocking the strict-order queue.

- Files below the gate can be compressed:
  - Audio → mono Opus ladder (64k / 56k / 48k / 40k)
  - Video → H.264 480p-class ladder
  - Images → JPEG resize/re-encode

- `BALE_FALLBACK_TELEGRAM_LINK_BUCKETS` (default `video`) controls the final link
  fallback per media type. The size gate above force-links oversized files even
  if their bucket is not listed.

### Changing the large-media behavior

Edit the workflow env (or add repository variables and reference them) if you
want files above 60 MB to go through compression instead of becoming links.
A typical experimental change:

```yaml
UPLOAD_LINK_ONLY_ABOVE_MB: "0"          # disable the source-size link-only gate
AUDIO_OPUS_BITRATE: "48k"
COMPRESS_TARGET_MAX_MB: "40"
BALE_FALLBACK_TELEGRAM_LINK_BUCKETS: "video,audio"
# AUDIO_SPLIT_ABOVE_MB: "60"            # split very large audio into parts
```

Be aware of the trade-offs:

- **Quality:** Opus at 40–64k mono is good for speech, not for music.
- **Strict order:** a large upload failure can block later messages until the
  next run; the size gate exists to prevent that.
- **Bale limit:** the compressed output must stay below 50 MB.
- **Splitting:** if a split part succeeds and a later part fails, a retry can
  re-send the earlier part (duplicate). The split path is a last resort.
- **Time:** compression and uploads must fit inside the workflow's
  `timeout-minutes: 60`.

Test any change on a private/test Bale channel first.

---

## 8. Monitoring and runbook

### Normal monitoring

- **cron-job.org:** enable failure notifications and check the execution history
  for `204`.
- **GitHub Actions:** watch for failed runs (GitHub emails the repository owner
  by default).
- **State commits:** a `chore: update forwarder state` commit is normal after a
  run that changed the queue.
- **Bale:** confirm the destination channel receives the post.

### Failure table

| Symptom | Likely cause | Fix |
|---|---|---|
| cron returns `401` | PAT expired/revoked | Create a new fine-grained PAT and update the cron header |
| cron returns `403` | PAT lacks **Actions: write** or repo access | Recreate the token with the right permissions |
| cron returns `404` | Wrong repo/workflow path or `ref` | Check `/repos/<owner>/<repo>/actions/workflows/daily-forward.yml/dispatches` and `"ref":"main"` |
| cron returns `422` | Invalid JSON body | Use exactly `{"ref":"main"}` |
| `204` but no run | `ENABLE_DAILY_FORWARD` is not `true`, or Actions is disabled | Set the variable, enable Actions |
| Job skipped | Opt-in guard is false | Set `ENABLE_DAILY_FORWARD=true` |
| Bale `403 permission_denied` | Bot is not a channel admin | Add the bot as admin with “post messages” |
| Telegram login prompt / `Session is not authorized` | StringSession expired or revoked | Re-export the session, update the secret |
| `git push` fails in the state step | Workflow permissions or branch protection | Settings → Actions → General → Read and write; allow the bot |
| Run stuck at a message | Strict order + upload failure | Check the log, fix Bale/Telegram, run manually |
| Large media only posts a link | `UPLOAD_LINK_ONLY_ABOVE_MB` gate | See §7 if you want compression |
| Duplicate post | Run crashed after Bale accepted but before state commit, or split partial failure | Inspect `state/state.db`; avoid re-running the same id blindly |
| Scheduled run late/missing | GitHub `schedule` is best-effort | Use the external cron (this guide) |

### Secret rotation

- **Fine-grained PAT:** rotate before expiry; update the cron header.
- **Telegram session:** if revoked, re-run login/export and update
  `TELEGRAM_SESSION`.
- **Bale bot token:** if rotated, update `BALE_BOT_TOKEN`.
- Never commit `.env` files, `session_string.txt`, or tokens.

---

## 9. Security

- `TELEGRAM_SESSION` grants full access to the Telegram account. Treat it like a
  password. If it leaks, terminate the session from Telegram → Settings →
  Devices, then re-export.
- Use a **fine-grained** PAT limited to the fork with only `Actions: Read and
  write` + `Metadata: Read`.
- The external cron only triggers the workflow; it never receives repository
  secrets or the Bale/Telegram credentials.
- `state/state.db` is committed to the repository. On a public fork it exposes
  message ids, dates, kinds, and status — not message text or media. Use a
  private repository if that metadata is sensitive.
- Protect the `main` branch carefully: the workflow needs permission to push
  state commits.

---

## 10. Cost

| Item | Cost |
|---|---|
| GitHub Actions (public repo) | Free (standard runners) |
| GitHub Actions (private repo) | 2,000 minutes/month on the Free plan; daily runs fit easily |
| cron-job.org / Cloudflare Worker | Free |
| Server / Render / VPS | Not required |

---

## 11. Customization

- **Multiple source channels:** separate mappings with `;` in
  `SOURCE_TO_BALE_MAPPING`.
- **Forum groups:** use `TOPIC_TO_BALE_MAPPING` and
  `STRICT_TOPIC_ROUTING_SOURCES`.
- **Test destination:** set `FORCE_BALE_CHAT=@testchannel` while testing.
- **Start from a specific message:** set `MIN_TG_MESSAGE_ID` to the latest id for
  “new only”, or `0` for a full history backfill.
- **Schedule:** change the cron-job.org timezone/time; the workflow does not need
  a code change.
- **GitHub schedule instead of external cron:** add a `schedule:` block to
  `.github/workflows/daily-forward.yml` if you accept delayed runs; see
  [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md).
- **Multiple deployments:** use separate forks and separate `state/state.db`
  files; never share a queue between two Telegram/Bale pairs.

---

## 12. Production checklist

- [ ] Bale bot is an admin with “post messages” in the destination channel.
- [ ] Telegram account can read the source channel.
- [ ] `API_ID`, `API_HASH`, `BALE_BOT_TOKEN`, `TELEGRAM_SESSION` are set as secrets.
- [ ] `ENABLE_DAILY_FORWARD=true`.
- [ ] `SOURCE_TO_BALE_MAPPING` is set.
- [ ] `MIN_TG_MESSAGE_ID` is set to the latest existing message (or intentionally 0).
- [ ] The fork's inherited `state/state.db` was reset.
- [ ] Workflow permissions are **Read and write**.
- [ ] Manual dispatch succeeds and the post arrives in Bale.
- [ ] External cron returns `204` and creates a `workflow_dispatch` run.
- [ ] The daily schedule in the external cron is saved and enabled.
- [ ] Failure notifications are enabled.
- [ ] The StringSession/API/Bale tokens are stored only in secrets.
- [ ] A rotation reminder exists for the PAT and session.

---

## 13. FAQ and limitations

**Why not a webhook?**
Telegram webhooks exist for bots, not for user accounts. Reading a channel with a
user session requires a running client or a scheduled pull. A webhook-based
design would additionally require adding a bot as channel admin and would be
limited to the Bot API's 20 MB download limit.

**Why an external cron?**
GitHub's `schedule` is best-effort and can be delayed by hours. The external cron
calls `workflow_dispatch`, so the run starts at the chosen time.

**Can I use Render?**
Not required. GitHub Actions + external cron is free and serverless. A Render
Cron Job would add a monthly cost and still needs external state.

**Can I forward the whole history?**
Set `MIN_TG_MESSAGE_ID=0` (and be aware of the volume, Bale rate limits, and the
strict-order queue). Test on a private channel first.

**Is `state/state.db` an archive?**
No. It is a forwarding ledger with metadata only. To build a content archive, use
a separate exporter that stores message text/media.

**Why did a large file become a link?**
See §7: `UPLOAD_LINK_ONLY_ABOVE_MB` (default 60 MB) intentionally avoids large
downloads/uploads and queue blocking.

**What happens if the run fails?**
The state DB keeps the failed row. The next daily run retries it. If strict order
is blocked, fix the cause (Bale admin, Telegram session, media size) and trigger a
manual run. Check the failure table in §8.

**Known limitations**

- Once per scheduled run; not real-time.
- Messages deleted from Telegram before the run are lost.
- A crash after Bale accepted a message but before the state commit can duplicate
  that message; this is rare.
- Large media may be link-only depending on size/configuration.
- GitHub Actions or the external cron can have outages.
- The queue is strictly ordered; one permanently failing message can hold later
  messages until it is fixed or marked skipped.
