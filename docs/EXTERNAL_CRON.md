# Reliable daily triggers with an external cron

GitHub's `schedule` event is **best-effort**: runs can be delayed by hours or
dropped under load. For a channel that should be checked at a predictable time,
trigger the workflow from a free external cron instead:

```
external cron (17:00 Iran / 13:30 UTC)
        │  HTTPS POST
        ▼
GitHub API: workflow_dispatch
        │
        ▼
.github/workflows/daily-forward.yml  (free GitHub runner)
```

The workflow itself does not change: it already supports `workflow_dispatch`.
The external cron only sends a tiny authenticated request; it never receives
repository secrets or Telegram/Bale credentials.

---

## Option A — cron-job.org (simplest)

### 1. Create a fine-grained GitHub token

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**

- **Token name:** `mofidot-cron`
- **Expiration:** 90 days (or 1 year; rotate it)
- **Resource owner:** your account
- **Repository access:** *Only select repositories* →
  `MRGD313/telegram-to-bale-forwarder`
- **Repository permissions:**
  - **Actions:** Read and write  ← required for dispatch
  - **Metadata:** Read  ← mandatory for fine-grained tokens
  - Nothing else is needed.

Copy the token. Never commit it.

### 2. Create the cron job

Sign up at [cron-job.org](https://cron-job.org) (free) and create a job:

| Field | Value |
|---|---|
| Title | `Mofidot → Bale daily` |
| URL | `https://api.github.com/repos/MRGD313/telegram-to-bale-forwarder/actions/workflows/daily-forward.yml/dispatches` |
| Schedule | every day at **13:30 UTC** = **17:00 Iran** |
| Timezone | UTC (Iran is UTC+3:30 year-round) |
| Request method | `POST` |

**Headers:**

```
Authorization: Bearer <YOUR_FINE_GRAINED_PAT>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

**Body:**

```json
{"ref":"main"}
```

Save the job, then use **Test run**. A successful request returns **HTTP 204**
(No Content).

### 3. Verify

- cron-job.org shows `204` in the execution history.
- GitHub → Actions shows a new run with event **workflow_dispatch**.
- The run should print `[Crawl]`, `[SendQueue]`, and `[Send]` lines.

### 4. Failure alerts

Enable cron-job.org's failure notifications (email). If the token expires or is
revoked, the request returns `401` and no run is created.

---

## Option B — Cloudflare Worker cron (more secure)

Cloudflare's free plan includes Cron Triggers. The token is stored as an
encrypted Worker secret instead of a cron-service dashboard.

### 1. Create the Worker

Cloudflare dashboard → **Workers & Pages → Create → Worker**. Name it e.g.
`mofidot-forwarder-cron`.

Paste this code:

```js
export default {
  async scheduled(event, env, ctx) {
    const url =
      `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "mofidot-cron",
      },
      body: JSON.stringify({ ref: env.REF || "main" }),
    });
    console.log("dispatch", res.status, await res.text());
  },
};
```

### 2. Add variables and secret

Worker → **Settings → Variables and Secrets**:

| Name | Type | Value |
|---|---|---|
| `GITHUB_TOKEN` | Secret | the fine-grained PAT from above |
| `REPO` | Variable | `MRGD313/telegram-to-bale-forwarder` |
| `WORKFLOW` | Variable | `daily-forward.yml` |
| `REF` | Variable | `main` |

### 3. Add the cron trigger

Worker → **Settings → Triggers → Cron Triggers → Add**:

```
30 13 * * *
```

Cloudflare cron uses UTC: `30 13 * * *` = **17:00 Iran**. Deploy the Worker.

### 4. Verify

Use the Worker's **Trigger** button (or wait for the cron) and check GitHub
Actions for a `workflow_dispatch` run. The Worker log shows `dispatch 204`.

---

## Optional: deploy the Worker with Wrangler

```toml
# wrangler.toml
name = "mofidot-forwarder-cron"
main = "src/index.js"
compatibility_date = "2026-11-01"

[vars]
REPO = "MRGD313/telegram-to-bale-forwarder"
WORKFLOW = "daily-forward.yml"
REF = "main"

[triggers]
crons = ["30 13 * * *"]
```

```bash
npx wrangler secret put GITHUB_TOKEN
npx wrangler deploy
```

---

## Security notes

- Use the **smallest possible token**: `Actions: Read and write` + `Metadata: Read`
  for this repository only.
- The token can **trigger** workflows; it cannot read repository secrets, code,
  or Telegram/Bale credentials.
- Set an expiration and rotate the token. Revoke it immediately if leaked.
- The workflow still uses its own GitHub Secrets (`TELEGRAM_SESSION`,
  `BALE_BOT_TOKEN`, ...) on the runner; the external cron never sees them.
- Enable failure notifications in the cron service.

## Keeping the GitHub schedule

The workflow currently keeps its own `schedule` as a fallback. With the
external trigger, that schedule may still fire late; the workflow's
`concurrency` group and `state/state.db` prevent duplicate sends. If you want
only the external trigger, remove the `schedule:` block from
`.github/workflows/daily-forward.yml` after verifying the external cron.

## Troubleshooting

| Result | Meaning |
|---|---|
| `204` | dispatched successfully |
| `401` | token missing, expired, or revoked |
| `403` | token lacks **Actions: write** or repository access |
| `404` | wrong repo, workflow file name, or `ref` |
| `422` | invalid JSON body / missing `ref` |
| `204` but no run | repository variable `ENABLE_DAILY_FORWARD` is not `true`, or Actions is disabled |
