# Scripts

| Script | Purpose |
|--------|---------|
| `list_forum_topics.py` | Print forum **topic_id** + title (run before `TOPIC_TO_BALE_MAPPING`) |
| `verify_bale_channels.py` | Check Bale bot `getChat` for env mapping destinations |
| `pre_publish_check.ps1` | Pre-push: block secrets / runtime files from git |
| `generate_test_env.py` | Build `.env.test.public` / `.env.test.private` for E2E |
| `verify_test_queue.py` | Assert queue DB after E2E (`public` \| `private`) |
| `run_e2e_from_zero.ps1` | Full E2E: fresh test DBs, crawl+send, verify |
| `diagnose_source.py` | Sample Telegram media types + one Bale upload test |

**List forum topics (private group):**

```powershell
$env:DOTENV_FILE = ".env.private"
py scripts/list_forum_topics.py
```
