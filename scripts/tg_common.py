"""Small shared helpers for one-off Telethon scripts.

These scripts are separate from the long-running forwarder daemon. They reuse the
same credentials/session conventions so they work both locally and in CI.
"""

import os
import socket
import sys
from urllib.parse import urlparse


def load_env(env_file=None):
    """Load env vars without overriding values already provided by the process.

    CI secrets are exposed as environment variables; `override=False` keeps them
    authoritative even if a local .env file also exists.
    """
    from dotenv import load_dotenv

    load_dotenv(env_file or os.getenv("DOTENV_FILE", ".env"), override=False)


def prepare_windows():
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


def _port_open(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def proxy_from_env():
    """Return a Telethon proxy tuple from TELEGRAM_PROXY_URL / LOCAL_HTTP_PROXY.

    Supports socks5://, socks4://, http:// (and bare host:port, assumed HTTP).
    Returns None for a direct connection.
    """
    raw = (
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("LOCAL_HTTP_PROXY", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )
    if not raw or raw.lower() in ("direct", "none", "off", "0"):
        return None
    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    host = parsed.hostname or "127.0.0.1"
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (1080 if scheme.startswith("socks") else 8080)

    if not _port_open(host, port):
        print(f"[Proxy] {host}:{port} is not reachable; using direct connection", flush=True)
        return None

    try:
        import socks
    except ImportError:
        print("[Proxy] PySocks is missing; install requirements.txt", flush=True)
        return None

    kind = {
        "socks5": socks.SOCKS5,
        "socks5h": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
        "https": socks.HTTP,
    }.get(scheme, socks.HTTP)

    if parsed.username:
        return (kind, host, port, True, parsed.username, parsed.password or "")
    if kind in (socks.SOCKS5, socks.SOCKS4):
        return (kind, host, port, True)
    return (kind, host, port)
