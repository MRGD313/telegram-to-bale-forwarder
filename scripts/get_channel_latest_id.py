"""Print the latest message id of a Telegram channel/group.

Useful for setting MIN_TG_MESSAGE_ID when you want a scheduled run to forward only
messages published after setup (instead of replaying the whole history).

Usage (from the repository root):

    python scripts/get_channel_latest_id.py --chat @your_channel --env-file .env.public
    python scripts/get_channel_latest_id.py --chat @your_channel --env-file .env.public --out .env.minid
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tg_common import load_env, prepare_windows, proxy_from_env  # noqa: E402

from telethon import TelegramClient  # noqa: E402


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
            print("[ERR] Session is not authorized.", file=sys.stderr)
            return 2

        entity = await client.get_entity(args.chat)
        messages = await client.get_messages(entity, limit=1)
        if not messages:
            print("[ERR] No messages found (or no access to this chat).", file=sys.stderr)
            return 1
        latest = messages[0]
        line = f"MIN_TG_MESSAGE_ID={latest.id}"
        print(f"[OK] {args.chat}: latest message id={latest.id} date={latest.date.isoformat()}")
        print(line)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(line + "\n")
            print(f"[OK] Wrote {args.out}")
        return 0
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", required=True, help="Channel/group username, link, or id")
    parser.add_argument("--session", default="session", help="Existing Telethon session name/path")
    parser.add_argument("--env-file", default=None, help="Env file with API_ID / API_HASH")
    parser.add_argument("--out", default="", help="Write MIN_TG_MESSAGE_ID=... to this file")
    args = parser.parse_args()

    prepare_windows()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
