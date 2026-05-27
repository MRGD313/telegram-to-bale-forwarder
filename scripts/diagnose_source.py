"""
Quick check: Telegram media types for a source + Bale upload test for one voice/photo.
Usage (from project root):
  py scripts/diagnose_source.py @Mofidot @mofidottest
"""
import asyncio
import os
import sys

import requests
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
phone = os.getenv("TELEGRAM_PHONE")
token = os.getenv("BALE_BOT_TOKEN")
BALE_API = f"https://tapi.bale.ai/bot{token}"


async def main():
    tg_source = sys.argv[1] if len(sys.argv) > 1 else "@Mofidot"
    bale_chat = sys.argv[2] if len(sys.argv) > 2 else "@mofidottest"
    force = os.getenv("FORCE_BALE_CHAT", "").strip()
    if force:
        print(f"WARN: FORCE_BALE_CHAT={force!r} in .env — real sends use that, not {bale_chat!r}")

    client = TelegramClient("session", api_id, api_hash)
    await client.start(phone=phone)
    ent = await client.get_entity(tg_source)
    print(f"Telegram source={tg_source} id={ent.id}")

    kinds = {}
    async for m in client.iter_messages(ent, limit=100):
        if m.photo:
            k = "photo"
        elif m.video:
            k = "video"
        elif m.voice:
            k = "voice"
        elif m.audio:
            k = "audio"
        elif m.document:
            k = "document"
        elif m.media:
            k = type(m.media).__name__
        else:
            k = "text"
        kinds[k] = kinds.get(k, 0) + 1

    print("Last 100 message kinds:", kinds)

    for label, pred in (
        ("voice", lambda m: m.voice),
        ("photo", lambda m: m.photo),
    ):
        async for m in client.iter_messages(ent, limit=200):
            if not pred(m):
                continue
            ext = ".oga" if label == "voice" else ".jpg"
            path = f"temp/diag_{label}{ext}"
            await client.download_media(m, path)
            size = os.path.getsize(path)
            endpoint = "sendVoice" if label == "voice" else "sendDocument"
            field = "voice" if label == "voice" else "document"
            with open(path, "rb") as f:
                r = requests.post(
                    f"{BALE_API}/{endpoint}",
                    data={"chat_id": bale_chat},
                    files={field: (os.path.basename(path), f)},
                    timeout=(30, 300),
                )
            print(f"Bale {endpoint} -> {bale_chat}: status={r.status_code} body={r.text[:200]}")
            try:
                os.remove(path)
            except OSError:
                pass
            break


if __name__ == "__main__":
    asyncio.run(main())
