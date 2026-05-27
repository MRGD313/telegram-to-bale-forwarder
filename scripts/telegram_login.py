"""Telegram session login (non-interactive). Usage:
  py scripts/telegram_login.py              # request login code
  py scripts/telegram_login.py 12345        # complete login with code
  py scripts/telegram_login.py 12345 PASS # if 2FA password enabled
"""
import asyncio
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(os.getenv("DOTENV_FILE", ".env.private"), override=True)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


def _local_proxy_port_open(host, port, timeout=2.0):
    import socket

    try:
        with socket.create_connection((host, port), timeout):
            pass
        return True
    except OSError:
        return False


def _proxy_from_env():
    raw = (
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("LOCAL_HTTP_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )
    if not raw and sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                if int(winreg.QueryValueEx(key, "ProxyEnable")[0]):
                    raw = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
                    if "=" in raw:
                        for part in raw.split(";"):
                            if part.lower().startswith(("http=", "https=")):
                                raw = part.split("=", 1)[1].strip()
                                break
        except OSError:
            pass
    if not raw:
        return None
    if raw.lower() in ("direct", "none", "off", "0"):
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    p = urlparse(raw)
    host = p.hostname or "127.0.0.1"
    port = p.port or 10808
    if not _local_proxy_port_open(host, port):
        print(
            f"[Login] WARN: proxy {host}:{port} not reachable; trying direct Telegram",
            flush=True,
        )
        return None
    scheme = (p.scheme or "http").lower()
    import socks

    kind = socks.HTTP if scheme in ("http", "https") else socks.SOCKS5
    if kind == socks.SOCKS5:
        return (kind, host, port, True)
    return (kind, host, port)


async def _run():
    phone = (os.getenv("TELEGRAM_PHONE") or "").strip()
    if not phone:
        print("[ERR] Set TELEGRAM_PHONE in .env.private", flush=True)
        sys.exit(1)

    code = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    password = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
    proxy = _proxy_from_env()
    if proxy:
        print(f"[Login] proxy={proxy[0]} {proxy[1]}:{proxy[2]}", flush=True)
    else:
        print("[Login] direct (no proxy)", flush=True)

    client = TelegramClient(
        "session",
        int(os.getenv("API_ID")),
        os.getenv("API_HASH"),
        proxy=proxy,
        connection_retries=5,
        timeout=90,
    )
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"[OK] Already logged in as {me.id} (@{me.username or 'no-username'})", flush=True)
        await client.disconnect()
        return

    if not code:
        await client.send_code_request(phone)
        print(f"[OK] Login code sent to {phone}.", flush=True)
        print("Paste the code here and I will run: py scripts/telegram_login.py <code> [2fa]", flush=True)
        await client.disconnect()
        return

    await client.start(
        phone=phone,
        code_callback=lambda: code,
        password=lambda: password if password else None,
    )

    me = await client.get_me()
    print(f"[OK] Logged in as {me.id} (@{me.username or 'no-username'})", flush=True)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_run())
