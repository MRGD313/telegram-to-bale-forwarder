"""Verify Bale bot can see channels from TOPIC_TO_BALE_MAPPING and SOURCE_TO_BALE_MAPPING."""
import os
import re
import sys

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, os.getenv("DOTENV_FILE", ".env.private")))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def channels_from_env():
    out = []
    for key in ("TOPIC_TO_BALE_MAPPING", "SOURCE_TO_BALE_MAPPING"):
        raw = os.getenv(key, "")
        for part in raw.split(";"):
            part = part.strip()
            if "->" not in part:
                continue
            dest = part.split("->", 1)[1].strip().split("|", 1)[0].strip()
            if dest and dest.lower() != "skip" and dest not in out:
                out.append(dest)
    return out


def main():
    api = f"https://tapi.bale.ai/bot{os.environ['BALE_BOT_TOKEN']}"
    me = requests.get(f"{api}/getMe", timeout=30).json()
    print("Bot:", me.get("result", {}).get("username", me))
    channels = channels_from_env()
    if not channels:
        print("No destinations in TOPIC_TO_BALE_MAPPING / SOURCE_TO_BALE_MAPPING")
        return
    failed = 0
    for ch in channels:
        r = requests.get(f"{api}/getChat", params={"chat_id": ch}, timeout=30).json()
        if r.get("ok"):
            t = r["result"].get("title", "?")
            print(f"OK  {ch} -> {t}")
        else:
            failed += 1
            print(f"FAIL {ch} -> {r.get('description', r)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
