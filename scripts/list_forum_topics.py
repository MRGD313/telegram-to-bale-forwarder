"""
List Telegram forum topic id + title for private group.
Usage (project root):  py scripts/list_forum_topics.py
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.channels import GetForumTopicsRequest
from telethon.tl.types import InputPeerChannel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, os.getenv("DOTENV_FILE", ".env.private")))

SOURCE = os.getenv("SOURCE_TO_BALE_MAPPING", "").split("->")[0].strip() or "https://t.me/+4gIXRFmTh1M3ODA0"

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    client = TelegramClient(os.path.join(ROOT, "session"), int(os.environ["API_ID"]), os.environ["API_HASH"])
    await client.start(phone=os.environ["TELEGRAM_PHONE"])
    ent = await client.get_entity(SOURCE)
    print(f"Group: {getattr(ent, 'title', SOURCE)} (id={ent.id})\n")
    print(f"{'topic_id':>8}  title")
    print("-" * 60)

    # Fast path: official forum topics API
    try:
        peer = await client.get_input_entity(ent)
        if isinstance(peer, InputPeerChannel):
            offset_date = None
            offset_id = 0
            offset_topic = 0
            seen = {}
            while True:
                result = await client(
                    GetForumTopicsRequest(
                        channel=peer,
                        offset_date=offset_date,
                        offset_id=offset_id,
                        offset_topic=offset_topic,
                        limit=100,
                    )
                )
                for t in result.topics:
                    tid = t.id
                    title = getattr(t, "title", None) or ""
                    if tid not in seen:
                        seen[tid] = title
                        print(f"{tid:8}  {title}")
                if not result.topics:
                    break
                last = result.topics[-1]
                offset_topic = last.id
                offset_id = last.top_message_id
                offset_date = last.date
                if len(result.topics) < 100:
                    break
            if seen:
                print(f"\nTotal topics: {len(seen)}")
                await client.disconnect()
                return
    except Exception as e:
        print(f"(GetForumTopics API unavailable: {e!r}; scanning messages…)\n")

    # Fallback: scan messages for TopicCreate actions
    titles = {}
    counts = {}
    async for msg in client.iter_messages(ent, limit=None, reverse=True):
        tid = None
        reply_to = getattr(msg, "reply_to", None)
        if reply_to:
            top_id = getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "top_msg_id", None)
            if top_id:
                tid = int(top_id)
            elif getattr(reply_to, "forum_topic", None) and getattr(reply_to, "reply_to_msg_id", None):
                tid = int(reply_to.reply_to_msg_id)
        action = getattr(msg, "action", None)
        if action and action.__class__.__name__ == "MessageActionTopicCreate":
            tid = int(msg.id)
            title = getattr(action, "title", None)
            if title:
                titles[tid] = title
        if tid is not None:
            counts[tid] = counts.get(tid, 0) + 1

    for tid in sorted(set(titles.keys()) | set(counts.keys())):
        title = titles.get(tid, "(no title found)")
        n = counts.get(tid, 0)
        print(f"{tid:8}  {title}  ({n} msgs)")

    print(f"\nTotal topics: {len(set(titles.keys()) | set(counts.keys()))}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
