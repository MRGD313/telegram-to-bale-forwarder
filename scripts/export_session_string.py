"""Export an authorized Telethon StringSession for CI (GitHub Actions, cron hosts).

Usage (from the repository root):

    python scripts/export_session_string.py --env-file .env.public
    python scripts/export_session_string.py --env-file .env.public --out session_string.txt

The session file must already be logged in (the forwarder creates it on first run).
The output is a credential: never commit it or paste it into logs.

On GitHub: Settings > Secrets and variables > Actions > New repository secret
  Name:  TELEGRAM_SESSION
  Value: the string printed/written by this script
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tg_common import load_env, prepare_windows, proxy_from_env  # noqa: E402

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402


async def _run(args):
    load_env(args.env_file)
    api_id = int(os.environ["API_ID"])
    api_hash = os.environ["API_HASH"]

    client = TelegramClient(
        args.session,
        api_id,
        api_hash,
        proxy=proxy_from_env(),
        connection_retries=5,
        timeout=90,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(
                "[ERR] This session is not authorized yet. Run the forwarder (or "
                "scripts/telegram_login.py) once, then re-run this script.",
                file=sys.stderr,
            )
            return 2

        value = StringSession.save(client.session)
        me = await client.get_me()
        print(f"[OK] Session for {me.id} (@{me.username or 'no-username'})", file=sys.stderr)
    finally:
        await client.disconnect()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(value + "\n")
        print(f"[OK] Wrote {args.out} — keep it secret and never commit it.", file=sys.stderr)
    else:
        print(value)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default="session", help="Existing Telethon session name/path")
    parser.add_argument("--env-file", default=None, help="Env file with API_ID / API_HASH")
    parser.add_argument("--out", default="", help="Write the session string to this file")
    args = parser.parse_args()

    prepare_windows()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
