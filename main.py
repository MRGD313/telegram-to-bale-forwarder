from telethon import TelegramClient, events
from telethon import functions
from telethon.sessions import SQLiteSession
from telethon.extensions import html as tg_html
from telethon.helpers import add_surrogate, del_surrogate, within_surrogate
from telethon.tl.types import (
    MessageEntityMentionName,
    MessageEntityTextUrl,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGame,
    MessageMediaGeo,
    MessageMediaInvoice,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaVenue,
    MessageMediaWebPage,
)
from dotenv import load_dotenv
import os
import ssl
import sys
import time
import requests
import asyncio
import subprocess

# Telethon + PySocks through local HTTP/SOCKS proxy fails on Windows ProactorEventLoop
# (OSError WinError 121). Selector policy is required for v2rayN / system-proxy setups.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import math
import mimetypes
import sqlite3
import json
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(os.getenv("DOTENV_FILE", ".env"), override=True)


def _normalize_proxy_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        return f"http://{url}"
    return url


# Telegram DC endpoint used only for reachability probes (not auth).
_TELEGRAM_PROBE_HOST = "149.154.167.51"
_TELEGRAM_PROBE_PORT = 443
_NETWORK_PROBE_TIMEOUT = float(os.getenv("NETWORK_PROBE_TIMEOUT", "4"))


def _build_telethon_proxy(proxy_url):
    """
    Telethon MTProto does not use HTTP_PROXY env; route via local v2rayN/clash port.
    proxy_url examples: socks5://127.0.0.1:10808  http://127.0.0.1:10808
    """
    if not proxy_url:
        return None
    from urllib.parse import urlparse

    p = urlparse(proxy_url.strip())
    scheme = (p.scheme or "socks5").lower()
    host = p.hostname or "127.0.0.1"
    port = p.port or 10808
    try:
        import socks
    except ImportError:
        print(
            "[Network] PySocks missing — install requirements.txt (Telegram proxy disabled).",
            flush=True,
        )
        return None
    kind = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
        "https": socks.HTTP,
    }.get(scheme, socks.SOCKS5)
    if p.username:
        return (kind, host, port, True, p.username, p.password or "")
    # Remote DNS through proxy (v2rayN / filtered networks).
    if kind in (socks.SOCKS5, socks.SOCKS4):
        return (kind, host, port, True)
    return (kind, host, port)


def _probe_direct_telegram(timeout=None):
    """True when TCP to a Telegram DC works without a local proxy."""
    import socket

    timeout = _NETWORK_PROBE_TIMEOUT if timeout is None else timeout
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((_TELEGRAM_PROBE_HOST, _TELEGRAM_PROBE_PORT))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _probe_proxy_reaches_telegram(proxy_url, timeout=None):
    proxy = _build_telethon_proxy(_normalize_proxy_url(proxy_url))
    if proxy is None:
        return False
    import socks

    timeout = _NETWORK_PROBE_TIMEOUT if timeout is None else timeout
    kind, host, port = proxy[0], proxy[1], proxy[2]
    extra = proxy[4:] if len(proxy) > 4 else ()
    s = socks.socksocket()
    if extra:
        s.set_proxy(kind, host, port, True, *extra)
    else:
        s.set_proxy(kind, host, port)
    s.settimeout(timeout)
    try:
        s.connect((_TELEGRAM_PROBE_HOST, _TELEGRAM_PROBE_PORT))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _probe_telethon_connect(proxy_url):
    """Real MTProto connect test (socket-only probe is not enough for v2rayN)."""
    try:
        api_id = int(os.getenv("API_ID"))
        api_hash = os.getenv("API_HASH")
    except (TypeError, ValueError):
        return False
    if not api_hash:
        return False
    proxy = _build_telethon_proxy(_normalize_proxy_url(proxy_url))
    if proxy is None:
        return False
    from telethon.sessions import MemorySession

    test = TelegramClient(
        MemorySession(),
        api_id,
        api_hash,
        proxy=proxy,
        connection_retries=3,
        timeout=45,
    )
    loop = asyncio.new_event_loop()
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.wait_for(test.connect(), timeout=25))
        return test.is_connected()
    except Exception as exc:
        print(f"[Network] Telethon probe failed {proxy_url!r}: {exc!r}", flush=True)
        return False
    finally:
        try:
            if test.is_connected():
                loop.run_until_complete(test.disconnect())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        # Probe must not leave a closed loop as the thread default (breaks client.loop.run_until_complete).
        asyncio.set_event_loop(asyncio.new_event_loop())


def _probe_telethon_direct_connect():
    """Real MTProto direct-connect test (no proxy)."""
    try:
        api_id = int(os.getenv("API_ID"))
        api_hash = os.getenv("API_HASH")
    except (TypeError, ValueError):
        return False
    if not api_hash:
        return False
    from telethon.sessions import MemorySession

    test = TelegramClient(
        MemorySession(),
        api_id,
        api_hash,
        proxy=None,
        connection_retries=3,
        timeout=45,
    )
    loop = asyncio.new_event_loop()
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.wait_for(test.connect(), timeout=25))
        return test.is_connected()
    except Exception as exc:
        print(f"[Network] direct MTProto probe failed: {exc!r}", flush=True)
        return False
    finally:
        try:
            if test.is_connected():
                loop.run_until_complete(test.disconnect())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        asyncio.set_event_loop(asyncio.new_event_loop())


def _apply_process_http_proxy(http_url):
    http_url = _normalize_proxy_url(http_url)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[key] = http_url
    # Bale (tapi.bale.ai) is domestic — routing it through v2ray often causes SSLEOFError on uploads.
    _bale_no_proxy_hosts = ("tapi.bale.ai", "bale.ai", ".bale.ai", "localhost", "127.0.0.1")
    existing = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for host in _bale_no_proxy_hosts:
        if host not in parts:
            parts.append(host)
    merged = ",".join(parts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def _clear_process_http_proxy():
    """Remove inherited process proxy vars when direct route is selected."""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)


def _proxy_scheme_variants(proxy_url):
    from urllib.parse import urlparse

    p = urlparse(_normalize_proxy_url(proxy_url))
    host = p.hostname or "127.0.0.1"
    port = p.port or 10808
    # v2rayN mixed port: try HTTP before SOCKS (Telethon MTProto often works better on HTTP here).
    return [f"http://{host}:{port}", f"socks5://{host}:{port}"]


def _windows_system_proxy_url():
    """Read Windows IE/system proxy (v2rayN 'System Proxy' sets this)."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            try:
                enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            except OSError:
                enabled = 0
            if not enabled:
                return ""
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
        if not server:
            return ""
        if "=" in server:
            for part in server.split(";"):
                part = part.strip()
                if part.lower().startswith(("http=", "https=")):
                    server = part.split("=", 1)[1].strip()
                    break
        return _normalize_proxy_url(server)
    except OSError:
        return ""


def _default_local_proxy_candidates():
    """Common local ports: v2rayN, Clash, Qv2ray, etc."""
    ports = (
        10808,
        10809,
        7890,
        7891,
        1080,
        8080,
        20170,
        20171,
        2080,
        33210,
        9050,
    )
    out = []
    for port in ports:
        out.append(f"socks5://127.0.0.1:{port}")
        out.append(f"http://127.0.0.1:{port}")
    return out


def _explicit_proxy_candidates():
    seen = set()
    out = []
    for name in (
        "TELEGRAM_PROXY_URL",
        "LOCAL_HTTP_PROXY",
        "LOCAL_PROXY_URL",
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ):
        raw = os.getenv(name, "").strip()
        if not raw:
            continue
        url = _normalize_proxy_url(raw)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _network_mode_from_env():
    """
    auto (default): probe direct Telegram, else find local/system proxy.
    direct / tun / on: never use proxy.
    proxy / off: skip direct probe; search local proxy only.
    """
    raw = (os.getenv("NETWORK_MODE") or os.getenv("TUN_MODE") or "auto").strip().lower()
    if raw in ("auto", ""):
        return "auto"
    if raw in ("direct", "tun", "on", "1", "true", "yes"):
        return "direct"
    if raw in ("proxy", "off", "0", "false", "no"):
        return "proxy"
    return "auto"


def _activate_proxy(working_proxy_url):
    """Apply the same proxy URL that passed the reachability probe (do not switch HTTP→SOCKS)."""
    from urllib.parse import urlparse

    override = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    proxy_url = _normalize_proxy_url(override or working_proxy_url)
    p = urlparse(proxy_url)
    scheme = (p.scheme or "http").lower()
    if scheme.startswith("socks"):
        http_url = f"http://{p.hostname}:{p.port}"
        tg_url = proxy_url
    else:
        http_url = proxy_url
        tg_url = override or proxy_url
    _apply_process_http_proxy(http_url)
    telethon_proxy = _build_telethon_proxy(tg_url)
    print(
        f"[Network] using proxy HTTP={http_url!r} Telegram={tg_url!r}",
        flush=True,
    )
    return telethon_proxy


def _discover_working_proxy(candidates):
    seen = set()
    ordered = []
    for base in candidates:
        for variant in _proxy_scheme_variants(base):
            if variant not in seen:
                seen.add(variant)
                ordered.append(variant)
    socket_ok = []
    for variant in ordered:
        if _probe_proxy_reaches_telegram(variant):
            socket_ok.append(variant)
        telethon_probe = os.getenv("NETWORK_TELETHON_PROBE", "").strip().lower()
        if not telethon_probe and sys.platform == "win32" and socket_ok:
            telethon_probe = "1"
        if socket_ok and telethon_probe in ("1", "true", "yes"):
            if _probe_telethon_connect(variant):
                return _activate_proxy(variant)
            print(
                f"[Network] TCP OK via {variant!r} but Telethon MTProto failed; trying next…",
                flush=True,
            )
    if socket_ok:
        pick = socket_ok[0]
        print(
            f"[Network] using {pick!r} (TCP reachability OK; MTProto may need v2rayN connected).",
            flush=True,
        )
        return _activate_proxy(pick)
    return None


def detect_outbound_network():
    """
    Zero-config networking for Iran/VPN/v2rayN users.
    Optional overrides: NETWORK_MODE=auto|direct|proxy, or legacy TUN_MODE=on|off.
    Explicit LOCAL_HTTP_PROXY / TELEGRAM_PROXY_URL are tried first when set.
    """
    mode = _network_mode_from_env()

    if mode == "direct":
        _clear_process_http_proxy()
        print("[Network] direct routing (forced).", flush=True)
        return None

    if mode == "auto":
        # Prefer direct route when available (e.g. SoftEther LAN/TUN), even if stale proxy settings exist.
        if _probe_direct_telegram():
            _clear_process_http_proxy()
            print("[Network] auto: direct Telegram OK (TUN/VPN or open route).", flush=True)
            return None
        if _probe_telethon_direct_connect():
            _clear_process_http_proxy()
            print("[Network] auto: direct MTProto connect OK (using direct route).", flush=True)
            return None

    explicit = _explicit_proxy_candidates()
    if explicit:
        found = _discover_working_proxy(explicit)
        if found is not None:
            print("[Network] using proxy from environment.", flush=True)
            return found
        print("[Network] configured proxy unreachable; continuing auto-detect…", flush=True)

    candidates = []
    sys_proxy = _windows_system_proxy_url()
    if sys_proxy:
        candidates.append(sys_proxy)
        print(f"[Network] Windows system proxy: {sys_proxy!r}", flush=True)
    candidates.extend(_default_local_proxy_candidates())

    found = _discover_working_proxy(candidates)
    if found is not None:
        print("[Network] auto-detected local proxy (v2rayN/Clash-style).", flush=True)
        return found

    print(
        "[Network] WARN: cannot reach Telegram (direct or local proxy). "
        "Start v2rayN/Clash or enable TUN; optional: TELEGRAM_PROXY_URL=socks5://127.0.0.1:PORT",
        flush=True,
    )
    return None


_telethon_proxy = detect_outbound_network()

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
bale_bot_token = os.getenv("BALE_BOT_TOKEN")
telegram_phone = os.getenv("TELEGRAM_PHONE")

# Semicolon-separated: source->bale_chat_id. Optional per-row link style: source->bale|footer
# (see BALE_PLAIN_LINK_APPEND). Bale id may be @username or -100…; trailing |inline|newline|footer only if matched.
source_to_bale_mapping_raw = os.getenv("SOURCE_TO_BALE_MAPPING", "")
topic_to_bale_mapping_raw = os.getenv("TOPIC_TO_BALE_MAPPING", "")
strict_topic_routing_sources_raw = os.getenv("STRICT_TOPIC_ROUTING_SOURCES", "")

# daemon: crawl (optional) + send queue + listen for new messages
# crawl | send | crawl_then_send | discover_topics | live
mode = os.getenv("MODE", "daemon").strip().lower()
backfill_limit = int(os.getenv("BACKFILL_LIMIT", "20"))
per_message_delay_seconds = float(os.getenv("PER_MESSAGE_DELAY_SECONDS", "1.0"))
download_timeout_seconds = float(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "180"))
download_retry_delay_seconds = float(os.getenv("DOWNLOAD_RETRY_DELAY_SECONDS", "30"))
upload_timeout_seconds = float(os.getenv("UPLOAD_TIMEOUT_SECONDS", "240"))
# Large uploads: extra read timeout grows with file size (seconds per MB, capped).
upload_sec_per_mb = float(os.getenv("UPLOAD_SEC_PER_MB", "180"))
max_upload_timeout_seconds = float(os.getenv("MAX_UPLOAD_TIMEOUT_SECONDS", "7200"))
# Legacy: optional pre-upload re-encode when file >= this size (pipeline prefers direct upload first).
audio_reencode_min_mb = float(os.getenv("AUDIO_REENCODE_MIN_MB", "15"))
audio_reencode_enable = os.getenv("AUDIO_REENCODE_ENABLE", "1").strip().lower() in ("1", "true", "yes")
# Iran/Bale: smaller files upload more reliably; quality tuned for phone playback (not archival).
audio_opus_bitrate = os.getenv("AUDIO_OPUS_BITRATE", "64k").strip() or "64k"
audio_opus_bitrate_floor = os.getenv("AUDIO_OPUS_BITRATE_FLOOR", "40k").strip() or "40k"
# After direct Bale upload fails: ffmpeg compress then retry (audio/video/image).
media_reencode_on_fail = os.getenv("MEDIA_REENCODE_ON_FAIL", "1").strip().lower() in ("1", "true", "yes")
# Compress before first upload when file is over threshold (avoids timeout on large originals).
compress_small_first = os.getenv("COMPRESS_SMALL_FIRST", "1").strip().lower() in ("1", "true", "yes")
compress_before_upload_mb_audio = float(os.getenv("COMPRESS_BEFORE_UPLOAD_MB_AUDIO", "1.5"))
compress_before_upload_mb_video = float(os.getenv("COMPRESS_BEFORE_UPLOAD_MB_VIDEO", "2"))
compress_before_upload_mb_image = float(os.getenv("COMPRESS_BEFORE_UPLOAD_MB_IMAGE", "0.8"))
video_reencode_max_height = int(os.getenv("VIDEO_REENCODE_MAX_HEIGHT", "480"))
video_reencode_crf = int(os.getenv("VIDEO_REENCODE_CRF", "30"))
video_reencode_preset = (os.getenv("VIDEO_REENCODE_PRESET", "fast").strip() or "fast")
# Large videos: lower read timeout scale + cap (avoids 30+ min waits on stuck uploads).
video_upload_sec_per_mb = float(os.getenv("VIDEO_UPLOAD_SEC_PER_MB", "40"))
video_upload_read_max_seconds = float(os.getenv("VIDEO_UPLOAD_READ_MAX_SECONDS", "600"))
# Never upload raw Telegram video above this MB (compress-first or link fallback).
video_skip_raw_upload_above_mb = float(os.getenv("VIDEO_SKIP_RAW_UPLOAD_ABOVE_MB", "5"))
# One fast ffmpeg pass instead of 3-step ladder (saves time on lecture-sized files).
video_fast_single_pass_above_mb = float(os.getenv("VIDEO_FAST_SINGLE_PASS_ABOVE_MB", "10"))
video_try_document_first = os.getenv("VIDEO_TRY_DOCUMENT_FIRST", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Skip slow tiers when estimated Bale success is low (size × ratio vs timeouts).
upload_confidence_enabled = os.getenv("UPLOAD_CONFIDENCE", "1").strip().lower() in ("1", "true", "yes")
video_post_compress_estimate_ratio = float(os.getenv("VIDEO_POST_COMPRESS_ESTIMATE_RATIO", "0.14"))
upload_estimate_skip_to_link_mb = float(os.getenv("UPLOAD_ESTIMATE_SKIP_TO_LINK_MB", "16"))
upload_estimate_max_read_seconds = float(os.getenv("UPLOAD_ESTIMATE_MAX_READ_SECONDS", "420"))
# Above this source MB: skip ffmpeg + multipart, post Telegram link only (0 = disable).
upload_video_max_encode_source_mb = float(os.getenv("UPLOAD_VIDEO_MAX_ENCODE_SOURCE_MB", "48"))
upload_video_low_confidence_mb = float(os.getenv("UPLOAD_VIDEO_LOW_CONFIDENCE_MB", "22"))
upload_low_confidence_max_attempts = max(1, int(os.getenv("UPLOAD_LOW_CONFIDENCE_MAX_ATTEMPTS", "2")))
upload_low_confidence_read_cap = float(os.getenv("UPLOAD_LOW_CONFIDENCE_READ_CAP", "360"))
upload_audio_low_confidence_mb = float(os.getenv("UPLOAD_AUDIO_LOW_CONFIDENCE_MB", "28"))
upload_audio_low_confidence_max_attempts = max(
    1, int(os.getenv("UPLOAD_AUDIO_LOW_CONFIDENCE_MAX_ATTEMPTS", "4"))
)
upload_audio_sec_per_mb = float(os.getenv("UPLOAD_AUDIO_SEC_PER_MB", "90"))
upload_audio_read_max_seconds = float(os.getenv("UPLOAD_AUDIO_READ_MAX_SECONDS", "1200"))
# 0 = never metadata link-only for audio; large voices still download+compress+upload.
upload_audio_max_link_only_mb = float(os.getenv("UPLOAD_AUDIO_MAX_LINK_ONLY_MB", "0"))
# Global hard gate: if source media is above this size, skip upload pipeline and use Telegram link.
# Set 0 to disable.
upload_link_only_above_mb = float(os.getenv("UPLOAD_LINK_ONLY_ABOVE_MB", "60"))
# For huge non-video files, skip brittle upload chain and post Telegram link directly.
upload_image_max_link_only_mb = float(os.getenv("UPLOAD_IMAGE_MAX_LINK_ONLY_MB", "20"))
upload_other_max_link_only_mb = float(os.getenv("UPLOAD_OTHER_MAX_LINK_ONLY_MB", "35"))
# Images above this size skip sendPhoto path and use sendDocument-only attempts.
upload_image_document_only_mb = float(os.getenv("UPLOAD_IMAGE_DOCUMENT_ONLY_MB", "3"))
# Comma-separated buckets allowed to use t.me link fallback: default video only (not text/image/voice).
_bale_link_buckets_raw = os.getenv("BALE_FALLBACK_TELEGRAM_LINK_BUCKETS", "video").strip().lower()
BALE_FALLBACK_TELEGRAM_LINK_BUCKETS = frozenset(
    b.strip() for b in _bale_link_buckets_raw.replace(";", ",").split(",") if b.strip()
)
# After sendMediaGroup returns 502/504, avoid per-part fallback (often duplicates if Bale accepted the group).
bale_album_gateway_fail_retry_only = os.getenv("BALE_ALBUM_GATEWAY_FAIL_RETRY_ONLY", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Target under Bale 50 MB with margin; smaller = faster upload from Iran.
compress_target_max_mb = float(os.getenv("COMPRESS_TARGET_MAX_MB", "18"))
compress_min_ratio = float(os.getenv("COMPRESS_MIN_SIZE_RATIO", "0.90"))
image_reencode_max_edge = int(os.getenv("IMAGE_REENCODE_MAX_EDGE", "1280"))
image_jpeg_q = int(os.getenv("IMAGE_JPEG_Q", "4"))
ffmpeg_bin = os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"
# After compress retry fails: post caption + https://t.me/... link on Bale.
bale_fallback_telegram_link = os.getenv("BALE_FALLBACK_TELEGRAM_LINK", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
db_path = os.getenv("DB_PATH", "state.db").strip() or "state.db"
max_retries = int(os.getenv("MAX_RETRIES", "3"))
# Bale uploads (sendPhoto/sendDocument/...): retries per endpoint; 5xx responses wait with exponential backoff.
bale_media_upload_attempts = max(1, int(os.getenv("BALE_MEDIA_UPLOAD_ATTEMPTS", "5")))
bale_5xx_backoff_seconds = float(os.getenv("BALE_5XX_BACKOFF_SECONDS", "2"))
# Telegram uses proxy; Bale API should usually go direct (set 1 only if Bale needs your VPN).
bale_use_proxy = os.getenv("BALE_USE_PROXY", "0").strip().lower() in ("1", "true", "yes")
audio_try_document_first = os.getenv("AUDIO_TRY_DOCUMENT_FIRST", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Very large voices: split by duration, opus each part, chained replies on Bale.
audio_split_enable = os.getenv("AUDIO_SPLIT_ENABLE", "1").strip().lower() in ("1", "true", "yes")
audio_split_above_mb = float(os.getenv("AUDIO_SPLIT_ABOVE_MB", "95"))
audio_split_chunk_mb = float(os.getenv("AUDIO_SPLIT_CHUNK_MB", "45"))
audio_split_min_parts = max(2, int(os.getenv("AUDIO_SPLIT_MIN_PARTS", "2")))
audio_split_max_parts = max(audio_split_min_parts, int(os.getenv("AUDIO_SPLIT_MAX_PARTS", "8")))
if not bale_use_proxy:
    print(
        "[Network] Bale API (tapi.bale.ai) uses direct connection (not v2ray proxy).",
        flush=True,
    )
# Many Telegram-compatible APIs accept images as files more reliably than as "photos".
send_image_as_document_first = os.getenv("SEND_IMAGE_AS_DOCUMENT_FIRST", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Photo-only Telegram albums: send as one Bale gallery (sendMediaGroup). Falls back per-image if it fails.
bale_photo_album_media_group = os.getenv("BALE_PHOTO_ALBUM_MEDIA_GROUP", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Document-only Telegram albums (e.g. PDF groups): send as one Bale sendMediaGroup when enabled.
bale_document_album_media_group = os.getenv("BALE_DOCUMENT_ALBUM_MEDIA_GROUP", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Large homogeneous albums are more likely to fail with SSL EOF on one huge multipart POST.
# If estimated total source size is above these limits, skip sendMediaGroup and upload parts sequentially.
bale_photo_album_media_group_max_total_mb = float(
    os.getenv("BALE_PHOTO_ALBUM_MEDIA_GROUP_MAX_TOTAL_MB", "24")
)
bale_document_album_media_group_max_total_mb = float(
    os.getenv("BALE_DOCUMENT_ALBUM_MEDIA_GROUP_MAX_TOTAL_MB", "32")
)
# Extra guard for very large single files inside album chunks.
bale_document_album_media_group_max_file_mb = float(
    os.getenv("BALE_DOCUMENT_ALBUM_MEDIA_GROUP_MAX_FILE_MB", "20")
)
# sendMediaGroup TCP connect timeout (seconds). Large multipart document uploads often need 60–120+.
bale_media_group_connect_seconds = float(os.getenv("BALE_MEDIA_GROUP_CONNECT_SECONDS", "90"))
# Max files per document sendMediaGroup (2–10). Default 4 reduces multipart size vs 10 to avoid write timeouts.
bale_document_media_group_max_files = int(os.getenv("BALE_DOCUMENT_MEDIA_GROUP_MAX_FILES", "4"))
bale_document_media_group_max_files = max(2, min(10, bale_document_media_group_max_files))
# Multiplier applied to computed read timeout for document groups only (many PDFs in one POST).
bale_document_media_group_timeout_multiplier = float(
    os.getenv("BALE_DOCUMENT_MEDIA_GROUP_TIMEOUT_MULTIPLIER", "2.0")
)
if bale_document_media_group_timeout_multiplier < 1.0:
    bale_document_media_group_timeout_multiplier = 1.0
# If uploads still fail, retry once per endpoint without caption (rules out caption/multipart issues).
bale_retry_media_without_caption = os.getenv("BALE_RETRY_MEDIA_WITHOUT_CAPTION", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# After image upload failures, re-encode to baseline JPEG via ffmpeg and retry once (Bale sometimes rejects Telegram's JPEG variant).
bale_reencode_image_on_failure = os.getenv("BALE_REENCODE_IMAGE_ON_FAILURE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# BALE_TEXT_FORMAT:
#   plain_links — append bare https/tg URLs (no parse_mode); safest when Bale ignores HTML.
#   html / markdown — real hyperlinks via parse_mode (same idea as Bale «ایجاد پیوند» in clients).
#   rich — try html hyperlinks first, then fall back to plain_links (good for Web + Android).
# Aliases: plain -> plain_links; auto|hybrid -> rich.
bale_text_format = os.getenv("BALE_TEXT_FORMAT", "plain_links").strip().lower()
if bale_text_format == "plain":
    bale_text_format = "plain_links"
elif bale_text_format in ("auto", "hybrid"):
    bale_text_format = "rich"
if bale_text_format not in ("plain_links", "html", "markdown", "rich"):
    print(
        f"[WARN] BALE_TEXT_FORMAT must be plain_links|html|markdown|rich; "
        f"got {bale_text_format!r} — using plain_links",
        flush=True,
    )
    bale_text_format = "plain_links"
# plain_links only: how to append resolved URLs (MessageEntityTextUrl / MentionName).
#   inline   — " (https://…)" after each span (default).
#   newline  — put each URL on its own line (often linkifies better than inline in Bale).
#   footer   — one deduped URL list at end of caption (best for RTL / Persian with weak clients).
bale_plain_link_append = os.getenv("BALE_PLAIN_LINK_APPEND", "inline").strip().lower()
if bale_plain_link_append not in ("inline", "newline", "footer"):
    bale_plain_link_append = "inline"
# Safe upper bound for Bale text chunking when server returns "message is too long".
bale_text_chunk_chars = max(400, int(os.getenv("BALE_TEXT_CHUNK_CHARS", "3500")))
# If true, plain_links inline mode inserts U+200E before " (url)" so RTL captions flow correctly;
# the mark is NOT placed before "https" inside the URL (Bale Web fails to autolink that pattern).
bale_link_ltr_mark = os.getenv("BALE_LINK_LTR_MARK", "1").strip().lower() in ("1", "true", "yes")
send_batch_size = int(os.getenv("SEND_BATCH_SIZE", "50"))
strict_send_order = os.getenv("STRICT_SEND_ORDER", "1").strip().lower() in ("1", "true", "yes")
# If true, send queue is ordered by topic_id first, then oldest→newest within each topic.
send_topic_by_topic = os.getenv("SEND_TOPIC_BY_TOPIC", "1").strip().lower() in ("1", "true", "yes")
# Semicolon or comma separated Telegram forum topic IDs to never forward (e.g. 1 = General).
exclude_send_topic_ids_raw = os.getenv("EXCLUDE_SEND_TOPIC_IDS", "")
exclude_send_null_topic = os.getenv("EXCLUDE_SEND_NULL_TOPIC", "0").strip().lower() in ("1", "true", "yes")
# If non-empty, MODE=send only processes queue rows whose topic_id is in this set (forum testing).
include_send_topic_ids_raw = os.getenv("INCLUDE_SEND_TOPIC_IDS", "")
# If true, each MODE=send run resets sent+failed rows to pending so replay starts from the first message.
reset_sent_on_send_start = os.getenv("RESET_SENT_ON_SEND_START", "0").strip().lower() in ("1", "true", "yes")
# Strict order: on failure at X, wait and retry X (never send X+1) until success or MAX_RETRIES.
send_auto_retry_on_failure = os.getenv("SEND_AUTO_RETRY_ON_FAILURE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
send_retry_delay_seconds = float(os.getenv("SEND_RETRY_DELAY_SECONDS", "90"))
# MODE=daemon: run full crawl once on startup before first send (BACKFILL_LIMIT=0 = all history).
daemon_initial_crawl = os.getenv("DAEMON_INITIAL_CRAWL", "1").strip().lower() in ("1", "true", "yes")
# Default is OFF: every fresh daemon start re-crawls Telegram and only unsent rows are forwarded.
daemon_skip_crawl_if_queued = os.getenv("DAEMON_SKIP_CRAWL_IF_QUEUED", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
# If set (e.g. @tebbecartest), all Bale sends use this chat — topic routing skipped for send/live/backfill.
force_bale_chat = os.getenv("FORCE_BALE_CHAT", "").strip()
# Telegram: survive disconnects without exiting (unattended daemon).
telegram_connection_retries = int(os.getenv("TELEGRAM_CONNECTION_RETRIES", "0"))
telegram_request_retries = int(os.getenv("TELEGRAM_REQUEST_RETRIES", "10"))
telegram_retry_delay = int(os.getenv("TELEGRAM_RETRY_DELAY", "2"))
telegram_timeout = int(os.getenv("TELEGRAM_TIMEOUT", "30"))
if _telethon_proxy and telegram_timeout < 90:
    telegram_timeout = 90
    print(f"[Network] proxy active: TELEGRAM_TIMEOUT={telegram_timeout}s", flush=True)
if _telethon_proxy and telegram_connection_retries <= 0:
    telegram_connection_retries = 20
    print(f"[Network] proxy active: TELEGRAM_CONNECTION_RETRIES={telegram_connection_retries}", flush=True)
telegram_op_max_retries = max(1, int(os.getenv("TELEGRAM_OP_MAX_RETRIES", "8")))
telegram_recover_delay_seconds = float(os.getenv("TELEGRAM_RECOVER_DELAY_SECONDS", "15"))
telegram_recover_max_attempts = max(1, int(os.getenv("TELEGRAM_RECOVER_MAX_ATTEMPTS", "12")))
daemon_supervisor = os.getenv("DAEMON_SUPERVISOR", "1").strip().lower() in ("1", "true", "yes")
# Unattended: on network/Bale/Telegram outages, wait with backoff and retry forever (no manual restart).
send_network_retry_unlimited = os.getenv("SEND_NETWORK_RETRY_UNLIMITED", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
network_retry_base_seconds = float(os.getenv("NETWORK_RETRY_BASE_SECONDS", "60"))
network_retry_max_seconds = float(os.getenv("NETWORK_RETRY_MAX_SECONDS", "600"))
telegram_download_timeout_retry = os.getenv("TELEGRAM_DOWNLOAD_TIMEOUT_RETRY", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
telegram_session_busy_timeout = float(os.getenv("TELEGRAM_SESSION_SQLITE_BUSY_TIMEOUT", "30"))

_send_queue_lock = None
_daemon_crawl_done = False
_telegram_api_lock = None
_telegram_api_lock_depth = 0
_send_network_hold_count = 0
_last_send_failure_is_network = False


def _telegram_api_lock_get():
    """Serialize all Telegram client I/O (downloads, reconnect) to avoid session sqlite lock races."""
    global _telegram_api_lock
    if _telegram_api_lock is None:
        _telegram_api_lock = asyncio.Lock()
    return _telegram_api_lock


async def _telegram_api_acquire():
    global _telegram_api_lock_depth
    await _telegram_api_lock_get().acquire()
    _telegram_api_lock_depth += 1


async def _telegram_api_release():
    global _telegram_api_lock_depth
    _telegram_api_lock_depth = max(0, _telegram_api_lock_depth - 1)
    _telegram_api_lock_get().release()


def _note_send_failure(network):
    global _last_send_failure_is_network
    _last_send_failure_is_network = bool(network)


def _reset_network_hold_counter():
    global _send_network_hold_count
    _send_network_hold_count = 0


def _network_retry_delay_seconds():
    global _send_network_hold_count
    delay = min(
        network_retry_max_seconds,
        network_retry_base_seconds * (2 ** min(_send_network_hold_count, 6)),
    )
    _send_network_hold_count += 1
    return delay


class ResilientSQLiteSession(SQLiteSession):
    """Telethon session with sqlite busy_timeout + WAL (avoids 'database is locked' on recover)."""

    def __init__(self, session_id=None, busy_timeout_ms=30000):
        self._busy_timeout_ms = max(5000, int(busy_timeout_ms))
        super().__init__(session_id)

    def _cursor(self):
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.filename,
                check_same_thread=False,
                timeout=self._busy_timeout_ms / 1000.0,
            )
            self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        return self._conn.cursor()


_conn_retries = None if telegram_connection_retries <= 0 else telegram_connection_retries
_session_busy_ms = int(max(5.0, telegram_session_busy_timeout) * 1000)
client = TelegramClient(
    ResilientSQLiteSession("session", busy_timeout_ms=_session_busy_ms),
    api_id,
    api_hash,
    proxy=_telethon_proxy,
    connection_retries=_conn_retries,
    retry_delay=telegram_retry_delay,
    request_retries=telegram_request_retries,
    timeout=telegram_timeout,
    auto_reconnect=True,
)
BALE_API = f"https://tapi.bale.ai/bot{bale_bot_token}"

os.makedirs("temp", exist_ok=True)

# Audio extensions we may re-encode before Bale upload (large voice / MPEG).
AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".mpeg",
        ".mpga",
        ".m4a",
        ".aac",
        ".wav",
        ".flac",
        ".ogg",
        ".oga",
        ".opus",
    }
)


def _is_timeout_like(exc):
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or type(exc).__name__ == "TimeoutError"


def _is_sqlite_busy(exc):
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _is_network_error(exc):
    """Connectivity loss (Iran/VPN/Wi‑Fi drops, Bale/Telegram timeouts, session sqlite busy)."""
    if exc is None:
        return False
    if _is_timeout_like(exc) or _is_sqlite_busy(exc):
        return True
    if isinstance(
        exc,
        (
            ConnectionError,
            OSError,
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            asyncio.IncompleteReadError,
        ),
    ):
        return True
    try:
        from requests.exceptions import RequestException

        if isinstance(exc, RequestException):
            return True
    except ImportError:
        pass
    msg = (repr(exc) + " " + str(exc)).lower()
    needles = (
        "server closed the connection",
        "not connected",
        "connection aborted",
        "connection reset",
        "forcibly closed",
        "network is unreachable",
        "network name is no longer available",
        "network connection was aborted",
        "semaphore timeout",
        "read timed out",
        "connect timeout",
        "write operation timed out",
        "max retries exceeded",
        "ssl",
        "unexpected_eof",
        "0 bytes read",
        "httpsconnectionpool",
        "database is locked",
    )
    return any(n in msg for n in needles)


def _is_telegram_session_desync(exc):
    """Telegram closes or invalidates DC session (often overlaps with overlapping session files)."""
    msg = (repr(exc) + " " + str(exc)).lower()
    return "wrong session" in msg or "security error while unpacking" in msg or (
        "security error" in msg and "unpacking" in msg
    )


def _is_telegram_transient(exc):
    """Network-level errors suitable for reconnect+retry (includes timeouts when retry enabled)."""
    if _is_telegram_session_desync(exc):
        return True
    if _is_network_error(exc):
        return True
    msg = str(exc).lower()
    return "server closed the connection" in msg or "not connected" in msg


def _should_reconnect_telegram(exc):
    """Reconnect only for real disconnects — not session sqlite lock (disconnect makes that worse)."""
    if _is_sqlite_busy(exc):
        return False
    return _is_telegram_transient(exc)


async def _sleep_session_busy(context, attempt=1):
    delay = min(60.0, telegram_session_busy_timeout + attempt * 2.0)
    print(f"[Telegram] session busy ({context}); waiting {delay:.0f}s (no disconnect)…", flush=True)
    await asyncio.sleep(delay)


async def _telegram_disconnect_silent():
    try:
        await client.disconnect()
    except Exception:
        pass


async def recover_telegram(reason, extra_sleep=0.0):
    """Disconnect, wait, reconnect (for unattended daemon). Caller must hold _telegram_api_lock."""
    print(f"[Telegram] Recovering: {reason}", flush=True)
    await _telegram_disconnect_silent()
    pause = telegram_recover_delay_seconds + extra_sleep
    await asyncio.sleep(pause)
    last = None
    for attempt in range(1, telegram_recover_max_attempts + 1):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start(phone=telegram_phone)
            print("[Telegram] Reconnected.", flush=True)
            return
        except Exception as e:
            last = e
            print(
                f"[Telegram] Reconnect {attempt}/{telegram_recover_max_attempts}: {e!r}",
                flush=True,
            )
            if _is_sqlite_busy(e):
                await asyncio.sleep(min(45.0, telegram_session_busy_timeout + attempt * 3.0))
            else:
                await asyncio.sleep(telegram_recover_delay_seconds)
    raise ConnectionError(f"Telegram reconnect failed after {telegram_recover_max_attempts} tries: {last!r}")


async def recover_telegram_guarded(reason, extra_sleep=0.0):
    """recover_telegram with API lock (for send loop / daemon; not for use inside telegram_call)."""
    await _telegram_api_acquire()
    try:
        await recover_telegram(reason, extra_sleep=extra_sleep)
    finally:
        await _telegram_api_release()


async def telegram_call(op_name, coro_factory):
    """Run a Telegram API coroutine; reconnect and retry on transient disconnects."""
    await _telegram_api_acquire()
    try:
        last = None
        for attempt in range(1, telegram_op_max_retries + 1):
            try:
                if not client.is_connected():
                    await client.connect()
                return await coro_factory()
            except Exception as e:
                last = e
                if _is_timeout_like(e):
                    print(
                        f"[Telegram] {op_name} timeout ({attempt}/{telegram_op_max_retries}): {e!r}",
                        flush=True,
                    )
                    if telegram_download_timeout_retry and attempt < telegram_op_max_retries:
                        # Large media downloads: avoid 10min NETWORK_HOLD backoff per chunk attempt.
                        if op_name.startswith("download"):
                            delay = download_retry_delay_seconds
                        else:
                            delay = _network_retry_delay_seconds()
                        print(
                            f"[Telegram] {op_name}: retry in {delay:.0f}s",
                            flush=True,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
                if _is_telegram_session_desync(e):
                    print(
                        f"[Telegram] {op_name} session desync ({attempt}/{telegram_op_max_retries}): {e!r}",
                        flush=True,
                    )
                    if attempt >= 3:
                        raise ConnectionError(
                            f"Telegram session still invalid after {attempt} desync recoveries: {e!r}",
                        ) from e
                    await recover_telegram(op_name, extra_sleep=30.0 if attempt == 1 else 45.0)
                    continue
                if _is_sqlite_busy(e):
                    print(
                        f"[Telegram] {op_name} session locked ({attempt}/{telegram_op_max_retries}): {e!r}",
                        flush=True,
                    )
                    if attempt < telegram_op_max_retries:
                        await _sleep_session_busy(op_name, attempt)
                        continue
                    raise
                if not _is_telegram_transient(e):
                    raise
                print(
                    f"[Telegram] {op_name} failed ({attempt}/{telegram_op_max_retries}): {e!r}",
                    flush=True,
                )
                if attempt < telegram_op_max_retries and _should_reconnect_telegram(e):
                    await recover_telegram(op_name)
        raise last
    finally:
        await _telegram_api_release()


async def iter_messages_resilient(entity, **kwargs):
    """iter_messages with reconnect if Telegram drops mid-crawl."""
    while True:
        await _telegram_api_acquire()
        try:
            try:
                async for msg in client.iter_messages(entity, **kwargs):
                    yield msg
                return
            except Exception as e:
                if _is_sqlite_busy(e):
                    await _sleep_session_busy("iter_messages")
                    continue
                if not _is_telegram_transient(e):
                    raise
                if _should_reconnect_telegram(e):
                    await recover_telegram(f"iter_messages: {e!r}")
                else:
                    await _sleep_session_busy("iter_messages")
        finally:
            await _telegram_api_release()


def is_audio_path(file_path):
    ext = os.path.splitext(file_path)[-1].lower()
    return ext in AUDIO_EXTENSIONS


def file_size_mb(file_path):
    return os.path.getsize(file_path) / (1024.0 * 1024.0)


def effective_upload_read_timeout(file_path):
    """Bale upload read timeout: scales with file size, capped."""
    try:
        mb = file_size_mb(file_path)
    except OSError:
        return upload_timeout_seconds
    bucket = _media_bucket(file_path)
    if bucket == "video":
        scaled = max(upload_timeout_seconds, mb * video_upload_sec_per_mb)
        return min(
            max_upload_timeout_seconds,
            video_upload_read_max_seconds,
            scaled,
        )
    if bucket == "audio":
        scaled = max(upload_timeout_seconds, mb * upload_audio_sec_per_mb)
        return min(max_upload_timeout_seconds, upload_audio_read_max_seconds, scaled)
    return min(max_upload_timeout_seconds, max(upload_timeout_seconds, mb * upload_sec_per_mb))


def telegram_link_fallback_allowed(bucket, src_mb=None):
    """Whether tier-3 t.me link fallback is allowed (text/image/voice excluded by default)."""
    if not bale_fallback_telegram_link:
        return False
    if bucket is None:
        return False
    if bucket not in BALE_FALLBACK_TELEGRAM_LINK_BUCKETS:
        return False
    if bucket == "audio" and upload_audio_max_link_only_mb > 0 and src_mb is not None:
        return src_mb >= upload_audio_max_link_only_mb
    return True


def queue_row_already_sent(tg_chat_id, tg_message_id):
    return db_get_queue_status(tg_chat_id, tg_message_id) == "sent"


def bale_row_already_forwarded(bale_chat_id, tg_chat_id, tg_message_id):
    if queue_row_already_sent(tg_chat_id, tg_message_id):
        return True
    return db_bale_map_get(bale_chat_id, tg_chat_id, tg_message_id) is not None


def bale_connect_timeout(file_path=None):
    """TCP connect timeout for Bale multipart uploads (scales slightly with file size)."""
    base = 30.0
    if file_path:
        try:
            base = max(base, 20.0 + min(90.0, file_size_mb(file_path) * 3.0))
        except OSError:
            pass
    return base


def bale_should_retry(status_code=None, exc=None):
    """True when another upload attempt may help (transient/network/rate-limit/server errors)."""
    if exc is not None:
        return True
    if status_code is None:
        return False
    return status_code >= 500 or status_code in (408, 429)


def bale_retry_sleep(attempt_index):
    return min(45.0, bale_5xx_backoff_seconds * (2**attempt_index))


def _bale_request_kwargs():
    """Force direct connection to Bale unless BALE_USE_PROXY=1."""
    if bale_use_proxy:
        return {}
    # `requests` may still consult environment/session state; force no proxy and close each connection.
    # This avoids flaky TLS EOFs seen when system proxy is active for Telegram but Bale must go direct.
    return {
        "proxies": {"http": "", "https": ""},
        "headers": {"Connection": "close"},
    }


def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _ffmpeg_cmd_base():
    return [ffmpeg_bin]


def _ffmpeg_available():
    from shutil import which

    if os.path.isabs(ffmpeg_bin) and os.path.isfile(ffmpeg_bin):
        return True
    return which(ffmpeg_bin) is not None


_ffmpeg_version_logged = False


def _log_ffmpeg_once():
    global _ffmpeg_version_logged
    if _ffmpeg_version_logged:
                    return
    _ffmpeg_version_logged = True
    if not _ffmpeg_available():
        print("[Compress] ffmpeg not on PATH — tier-2 compress disabled", flush=True)
        return
    try:
        r = subprocess.run(
            _ffmpeg_cmd_base() + ["-version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        line = (r.stdout or r.stderr or "").splitlines()[0] if r.returncode == 0 else "unknown"
        print(f"[Compress] using {line}", flush=True)
    except Exception as e:
        print(f"[Compress] ffmpeg detected but -version failed: {e!r}", flush=True)


def _compress_output_acceptable(src_mb, out_path):
    """Use compressed file if under Bale cap and meaningfully smaller (or source was over cap)."""
    try:
        out_mb = file_size_mb(out_path)
    except OSError:
        return False
    if out_mb > compress_target_max_mb:
        return False
    if src_mb > compress_target_max_mb:
        return out_mb < src_mb
    return out_mb < src_mb * compress_min_ratio


def transcode_to_opus_ogg(src_path, dst_path, bitrate=None, application=None):
    """
    Opus in OGG — Bale sendVoice-friendly. voip for speech; audio for music/podcasts.
    VBR on; mono 48 kHz matches Telegram voice conventions.
    """
    bitrate = (bitrate or audio_opus_bitrate).strip()
    ext = os.path.splitext(src_path)[-1].lower()
    if application is None:
        application = "voip" if ext in (".oga", ".ogg", ".opus") else "audio"
    cmd = _ffmpeg_cmd_base() + [
        "-y",
        "-i",
        src_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-application",
        application,
        "-vbr",
        "on",
        "-b:a",
        bitrate,
        dst_path,
        "-loglevel",
        "error",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and r.stderr:
        print(f"[WARN][Compress] opus stderr: {r.stderr[:200]!r}", flush=True)
    return r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0


def reencode_image_for_bale(src_path, dst_path, max_edge=None):
    """Baseline JPEG (yuv420p) + optional downscale for huge images."""
    max_edge = max_edge or image_reencode_max_edge
    vf = f"scale='min({max_edge},iw)':min({max_edge},ih):force_original_aspect_ratio=decrease"
    cmd = _ffmpeg_cmd_base() + [
        "-y",
        "-i",
        src_path,
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        "-q:v",
        str(max(2, min(8, int(image_jpeg_q)))),
        dst_path,
        "-loglevel",
        "error",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0


def transcode_video_for_bale(src_path, dst_path, max_height=None, crf=None, preset=None):
    """H.264/AAC MP4, yuv420p, faststart — widely accepted by Bale as video/document."""
    max_height = max_height or video_reencode_max_height
    crf = crf if crf is not None else video_reencode_crf
    preset = (preset or video_reencode_preset).strip() or "fast"
    cmd = _ffmpeg_cmd_base() + [
        "-y",
        "-i",
        src_path,
        "-vf",
        f"scale=-2:{int(max_height)}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-threads",
        "0",
        "-crf",
        str(int(crf)),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        dst_path,
        "-loglevel",
        "error",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0


def _audio_ladder_bitrates():
    """Smallest acceptable first for upload success; floor keeps speech intelligible."""
    rates = [audio_opus_bitrate]
    for extra in ("56k", "48k", audio_opus_bitrate_floor):
        e = extra.strip().lower()
        if e not in [r.strip().lower() for r in rates]:
            rates.append(e)
    return rates


def _compress_audio_ladder(file_path, src_mb, base):
    """Try opus bitrates until under COMPRESS_TARGET_MAX_MB or best reduction."""
    bitrates = _audio_ladder_bitrates()
    best_path = None
    best_mb = src_mb
    for br in bitrates:
        out_path = f"{base}.bale-retry.{br.strip().lower()}.opus.ogg"
        if not transcode_to_opus_ogg(file_path, out_path, bitrate=br):
            continue
        try:
            out_mb = file_size_mb(out_path)
        except OSError:
            safe_remove(out_path)
            continue
        if out_mb < best_mb:
            safe_remove(best_path)
            best_path, best_mb = out_path, out_mb
        else:
            safe_remove(out_path)
        if out_mb <= compress_target_max_mb and out_mb < src_mb * compress_min_ratio:
            return best_path, best_path
    if best_path and _compress_output_acceptable(src_mb, best_path):
        return best_path, best_path
    safe_remove(best_path)
    return None, None


def _compress_video_ladder(file_path, src_mb, base):
    """480p-class H.264 — readable on phone; ladder shrinks further if needed."""
    h0 = video_reencode_max_height
    c0 = video_reencode_crf
    if src_mb >= video_fast_single_pass_above_mb:
        crf_fast = min(34, c0 + 4)
        out_path = f"{base}.bale-fast.{h0}p.crf{crf_fast}.mp4"
        if transcode_video_for_bale(
            file_path, out_path, max_height=h0, crf=crf_fast, preset="fast"
        ):
            try:
                out_mb = file_size_mb(out_path)
            except OSError:
                safe_remove(out_path)
                return None, None
            if _compress_output_acceptable(src_mb, out_path) or out_mb < src_mb * 0.92:
                print(
                    f"[Compress] video fast-pass {src_mb:.1f}MB -> {out_mb:.1f}MB "
                    f"({h0}p crf{crf_fast})",
                    flush=True,
                )
                return out_path, out_path
            safe_remove(out_path)
        print("[WARN][Compress] video fast-pass failed; trying ladder", flush=True)
    attempts = [
        (h0, c0),
        (h0, min(34, c0 + 4)),
        (min(360, h0), min(36, c0 + 6)),
    ]
    best_path = None
    best_mb = src_mb
    for height, crf in attempts:
        out_path = f"{base}.bale-retry.{height}p.crf{crf}.mp4"
        if not transcode_video_for_bale(file_path, out_path, max_height=height, crf=crf):
            continue
        try:
            out_mb = file_size_mb(out_path)
        except OSError:
            safe_remove(out_path)
            continue
        if out_mb < best_mb:
            safe_remove(best_path)
            best_path, best_mb = out_path, out_mb
        else:
            safe_remove(out_path)
        if _compress_output_acceptable(src_mb, out_path):
            return out_path, out_path
    if best_path and _compress_output_acceptable(src_mb, best_path):
        return best_path, best_path
    safe_remove(best_path)
    return None, None


def _media_bucket(file_path):
    ext = os.path.splitext(file_path)[-1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        return "image"
    if ext in (".mp4", ".mov", ".mkv", ".webm"):
        return "video"
    if is_audio_path(file_path) or ext in (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac"):
        return "audio"
    return "other"


def should_compress_small_first(file_path):
    """Pre-upload compress for files that usually timeout when sent raw from Iran."""
    if not compress_small_first or not media_reencode_on_fail:
        return False
    try:
        mb = file_size_mb(file_path)
    except OSError:
        return False
    bucket = _media_bucket(file_path)
    if bucket == "audio":
        if audio_needs_immediate_split(file_path):
            return False
        # MP3/M4A via sendVoice through a proxy often fails; opus upload is smaller and more reliable.
        ext = os.path.splitext(file_path)[-1].lower()
        if ext in (".mp3", ".m4a", ".mpeg", ".mpga", ".wav", ".flac"):
            return mb >= min(compress_before_upload_mb_audio, 0.5)
        return mb >= compress_before_upload_mb_audio
    if bucket == "video":
        if mb >= video_skip_raw_upload_above_mb:
            return True
        return mb >= compress_before_upload_mb_video
    if bucket == "image":
        return mb >= compress_before_upload_mb_image
    return False


def compress_media_for_bale_retry(file_path):
    """
    Second tier: re-encode after direct upload failed.
    Returns (compressed_path, path_to_delete) or (None, None).
    """
    _log_ffmpeg_once()
    if not media_reencode_on_fail or not _ffmpeg_available():
        return None, None
    ext = os.path.splitext(file_path)[-1].lower()
    image_exts = (".jpg", ".jpeg", ".png", ".webp")
    video_exts = (".mp4", ".mov", ".mkv", ".webm")
    try:
        mb = file_size_mb(file_path)
    except OSError:
        mb = 0.0
    base, _ = os.path.splitext(file_path)
    if is_audio_path(file_path) or ext in (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac"):
        out_path, del_path = _compress_audio_ladder(file_path, mb, base)
        if out_path:
            print(f"[Compress] audio {mb:.1f}MB -> {file_size_mb(out_path):.1f}MB opus", flush=True)
            return out_path, del_path
        print("[WARN][Compress] audio ladder exhausted", flush=True)
        return None, None
    if ext in video_exts:
        out_path, del_path = _compress_video_ladder(file_path, mb, base)
        if out_path:
            print(f"[Compress] video {mb:.1f}MB -> {file_size_mb(out_path):.1f}MB h264", flush=True)
            return out_path, del_path
        print("[WARN][Compress] video ladder exhausted", flush=True)
        return None, None
    if ext in image_exts:
        out_path = base + ".bale-retry.jpg"
        if reencode_image_for_bale(file_path, out_path) and _compress_output_acceptable(mb, out_path):
            print(
                f"[Compress] image {mb:.1f}MB -> {file_size_mb(out_path):.1f}MB jpeg",
                flush=True,
            )
            return out_path, out_path
        safe_remove(out_path)
        print("[WARN][Compress] image re-encode failed or not smaller", flush=True)
        return None, None
    return None, None


def audio_split_part_count(src_mb):
    """How many time-based segments to use for a large audio file."""
    if src_mb <= audio_split_above_mb:
        return 0
    n = int(math.ceil(src_mb / max(1.0, audio_split_chunk_mb)))
    n = max(audio_split_min_parts, n)
    return min(audio_split_max_parts, n)


def audio_needs_immediate_split(file_path):
    """Large audio: never try single-file compress/upload (too slow / fails on Bale)."""
    if not audio_split_enable or _media_bucket(file_path) != "audio":
        return False
    try:
        return file_size_mb(file_path) > audio_split_above_mb
    except OSError:
        return False


def _ffprobe_duration_seconds(file_path):
    if not _ffmpeg_available():
        return None
    cmd = _ffmpeg_cmd_base() + [
        "-hide_banner",
        "-i",
        file_path,
        "-f",
        "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (r.stderr or "") + (r.stdout or "")
    for line in text.splitlines():
        if "Duration:" in line:
            part = line.split("Duration:", 1)[1].split(",")[0].strip()
            h, m, s = part.split(":")
            return float(h) * 3600.0 + float(m) * 60.0 + float(s)
    return None


def _ffmpeg_split_audio_segment(src_path, dst_path, start_sec, duration_sec):
    """Cut a time range only (step 1 of split → compress → upload)."""
    cmd = _ffmpeg_cmd_base() + [
        "-y",
        "-ss",
        f"{max(0.0, start_sec):.3f}",
        "-i",
        src_path,
        "-t",
        f"{max(0.1, duration_sec):.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        dst_path,
        "-loglevel",
        "error",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(600, int(duration_sec * 4)))
    except subprocess.TimeoutExpired:
        return False
    if r.returncode != 0 and r.stderr:
        print(f"[WARN][Split] cut stderr: {r.stderr[:200]!r}", flush=True)
    return r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0


def audio_part_caption(full_caption, part_index, part_count):
    """Part 1: full Telegram caption + label; later parts: short continuation label only."""
    label = f"🔊 بخش {part_index} از {part_count}"
    if part_index == 1:
        body = (full_caption or "").strip()
        if body:
            return f"{body}\n\n{label}", True
        return label, False
    return f"{label} — ادامه", False


def upload_audio_split_parts(
    file_path,
    bale_chat_id,
    caption,
    caption_parse_mode,
    reply_bale,
    upload_max_attempts=None,
    read_timeout_cap=None,
):
    """
    Oversized audio: split (time cut) → compress (opus ladder) → upload each part.
    Returns Bale message_id of part 1 (stored in bale_msg_map), or None.
    """
    _log_ffmpeg_once()
    if not audio_split_enable or not _ffmpeg_available():
        return None
    if _media_bucket(file_path) != "audio":
        return None
    try:
        src_mb = file_size_mb(file_path)
    except OSError:
        return None
    part_count = audio_split_part_count(src_mb)
    if part_count < 2:
        return None

    duration = _ffprobe_duration_seconds(file_path)
    if not duration or duration < 2.0:
        print("[WARN][Split] cannot read audio duration; skip split", flush=True)
        return None

    print(
        f"[Split] audio {src_mb:.1f}MB duration={duration:.0f}s -> {part_count} parts "
        f"(~{duration / part_count:.0f}s each); pipeline: cut -> compress -> upload",
        flush=True,
    )

    base, _ = os.path.splitext(file_path)
    part1_mid = None
    reply_to = reply_bale
    to_delete = []

    try:
        for idx in range(part_count):
            part_no = idx + 1
            start = duration * idx / part_count
            seg_dur = duration / part_count
            seg_path = f"{base}.part{part_no}of{part_count}.m4a"
            to_delete.append(seg_path)

            if not _ffmpeg_split_audio_segment(file_path, seg_path, start, seg_dur):
                print(f"[ERR][Split] cut failed part {part_no}/{part_count}", flush=True)
                return None
            try:
                cut_mb = file_size_mb(seg_path)
            except OSError:
                cut_mb = 0.0
            print(f"[Split] part {part_no}/{part_count} cut {cut_mb:.2f}MB", flush=True)

            compressed_path, compress_delete = compress_media_for_bale_retry(seg_path)
            if compress_delete and compress_delete not in to_delete:
                to_delete.append(compress_delete)
            upload_path = compressed_path or seg_path
            if compressed_path:
                try:
                    up_mb = file_size_mb(upload_path)
                except OSError:
                    up_mb = 0.0
                print(
                    f"[Split] part {part_no}/{part_count} compress -> opus {up_mb:.2f}MB",
                    flush=True,
                )
            else:
                print(
                    f"[WARN][Split] part {part_no}/{part_count} compress failed; upload cut as-is",
                    flush=True,
                )

            part_caption, use_parse_mode = audio_part_caption(caption, part_no, part_count)
            cap_mode = caption_parse_mode if use_parse_mode else None
            read_to = effective_upload_read_timeout(upload_path)
            if read_timeout_cap is not None:
                read_to = min(read_to, read_timeout_cap)

            print(f"[Split] part {part_no}/{part_count} upload", flush=True)
            mid = send_media_to_bale(
                bale_chat_id,
                upload_path,
                part_caption,
                read_to,
                cap_mode,
                bool(compressed_path),
                reply_to,
                upload_max_attempts=upload_max_attempts,
            )
            if mid is None:
                print(f"[ERR][Split] Bale upload failed part {part_no}/{part_count}", flush=True)
                return None
            if part1_mid is None:
                part1_mid = mid
            reply_to = mid
            if part_no < part_count:
                time.sleep(per_message_delay_seconds)
        print(f"[OK][Split] {part_count} parts uploaded; leader bale_msg_id={part1_mid}", flush=True)
        return part1_mid
    finally:
        for p in to_delete:
            safe_remove(p)


def telegram_message_link(message, source_key=None):
    """Public: t.me/channel/msg_id. Private supergroup: t.me/c/chat_id/msg_id."""
    msg_id = int(message.id)
    if source_key and source_key.startswith("@"):
        return f"https://t.me/{source_key.lstrip('@')}/{msg_id}"
    chat = getattr(message, "chat", None)
    username = getattr(chat, "username", None) if chat else None
    if username:
        return f"https://t.me/{username}/{msg_id}"
    chat_id = int(message.chat_id)
    cid = str(chat_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    return f"https://t.me/c/{cid}/{msg_id}"


def build_telegram_link_fallback_text(caption, tg_link):
    link_line = f"📎 مشاهده در تلگرام:\n{tg_link}"
    if caption and str(caption).strip():
        return f"{caption}\n\n{link_line}"
    return link_line


def message_telegram_file_size_mb(message):
    """Approximate file size from Telethon (bytes), or None if unknown."""
    fobj = getattr(message, "file", None)
    if not fobj:
        return None
    sz = getattr(fobj, "size", None)
    if sz is None or int(sz) <= 0:
        return None
    return int(sz) / (1024.0 * 1024.0)


def upload_media_bucket_from_message(msg_kind, ext):
    ext_l = (ext or "").lower()
    video_exts = (".mp4", ".mov", ".mkv", ".webm")
    image_exts = (".jpg", ".jpeg", ".png", ".webp")
    if ext_l in video_exts or (msg_kind and "video" in str(msg_kind).lower()):
        return "video"
    if ext_l in image_exts:
        return "image"
    mk = str(msg_kind).lower() if msg_kind else ""
    if "voice" in mk or "audio" in mk or ext_l in AUDIO_EXTENSIONS or ext_l in (".oga", ".opus"):
        return "audio"
    # Treat any other downloadable file (pdf/doc/zip/...) as "other" so confidence gates
    # can skip expensive download/upload and jump directly to Telegram-link fallback.
    return "other"


def upload_tier_plan_from_bucket_mb(bucket, mb):
    """Same return shape as compute_upload_tier_plan; mb must be > 0."""
    if not upload_confidence_enabled or bucket is None:
        return False, "", None, None
    if upload_link_only_above_mb > 0 and mb >= upload_link_only_above_mb:
        return (
            True,
            f"source_mb={mb:.1f}>={upload_link_only_above_mb} (UPLOAD_LINK_ONLY_ABOVE_MB)",
            None,
            None,
        )
    if bucket == "video":
        est_mb = mb * video_post_compress_estimate_ratio
        est_read_sec = est_mb * video_upload_sec_per_mb
        if upload_video_max_encode_source_mb > 0 and mb >= upload_video_max_encode_source_mb:
            return (
                True,
                f"source_mb={mb:.1f}>={upload_video_max_encode_source_mb} (UPLOAD_VIDEO_MAX_ENCODE_SOURCE_MB)",
                None,
                None,
            )
        if est_mb > upload_estimate_skip_to_link_mb:
            return (
                True,
                f"est_compressed_mb={est_mb:.1f}>{upload_estimate_skip_to_link_mb} "
                f"(ratio={video_post_compress_estimate_ratio})",
                None,
                None,
            )
        if est_read_sec > upload_estimate_max_read_seconds:
            return (
                True,
                f"est_read_s={est_read_sec:.0f}>{upload_estimate_max_read_seconds}",
                None,
                None,
            )
        if mb >= upload_video_low_confidence_mb:
            return (
                False,
                "",
                upload_low_confidence_max_attempts,
                upload_low_confidence_read_cap,
            )
    elif bucket == "audio":
        if upload_audio_max_link_only_mb > 0 and mb >= upload_audio_max_link_only_mb:
            return (
                True,
                f"audio_mb={mb:.1f}>={upload_audio_max_link_only_mb} (UPLOAD_AUDIO_MAX_LINK_ONLY_MB)",
                None,
                None,
            )
        if mb >= upload_audio_low_confidence_mb:
            return (
                False,
                "",
                upload_audio_low_confidence_max_attempts,
                upload_low_confidence_read_cap,
            )
    elif bucket == "image":
        if upload_image_max_link_only_mb > 0 and mb >= upload_image_max_link_only_mb:
            return (
                True,
                f"image_mb={mb:.1f}>={upload_image_max_link_only_mb} (UPLOAD_IMAGE_MAX_LINK_ONLY_MB)",
                None,
                None,
            )
    elif bucket == "other":
        if upload_other_max_link_only_mb > 0 and mb >= upload_other_max_link_only_mb:
            return (
                True,
                f"other_mb={mb:.1f}>={upload_other_max_link_only_mb} (UPLOAD_OTHER_MAX_LINK_ONLY_MB)",
                None,
                None,
            )
    return False, "", None, None


def compute_upload_tier_plan(file_path):
    """
    Returns (link_only, reason, max_upload_attempts_or_None, read_timeout_cap_or_None).
    link_only=True skips compress + multipart (Telegram link only).
    """
    bucket = _media_bucket(file_path)
    try:
        mb = file_size_mb(file_path)
    except OSError:
        return False, "", None, None
    return upload_tier_plan_from_bucket_mb(bucket, mb)


def send_telegram_link_fallback(
    bale_chat_id,
    message,
    caption,
    caption_parse_mode,
    source_key,
    reply_bale,
    bucket=None,
    src_mb=None,
    force=False,
):
    """Third tier: text post on Bale with link to the original Telegram message."""
    if not force and not telegram_link_fallback_allowed(bucket, src_mb):
        print(
            f"[BaleUpload] Telegram link fallback disabled for bucket={bucket!r} "
            f"(allowed buckets: {sorted(BALE_FALLBACK_TELEGRAM_LINK_BUCKETS)})",
            flush=True,
        )
        return None
    if force:
        print(
            f"[BaleUpload] confidence gate: forcing Telegram link fallback for bucket={bucket!r}",
            flush=True,
        )
    tg_link = telegram_message_link(message, source_key)
    text = build_telegram_link_fallback_text(caption, tg_link)
    mid = send_text_to_bale(bale_chat_id, text, caption_parse_mode, reply_bale)
    if mid is not None:
        print(
            f"[OK][Fallback][TelegramLink] chat={bale_chat_id} tg_msg={message.id} link={tg_link}",
            flush=True,
        )
    return mid


def upload_media_to_bale_with_tiers(
    message,
    bale_chat_id,
    file_path,
    caption,
    caption_parse_mode,
    source_key,
    reply_bale,
):
    """
    Tier 1: upload (small files: original; larger: compress-first when COMPRESS_SMALL_FIRST).
    Tier 2: ffmpeg compress + upload (if tier 1 was original and failed).
    Tier 3: Bale text with Telegram deep link.
    Returns Bale message_id or None.
    """
    link_only, plan_reason, plan_max_attempts, plan_read_cap = compute_upload_tier_plan(file_path)
    bucket = _media_bucket(file_path)
    try:
        src_mb_hint = file_size_mb(file_path)
    except OSError:
        src_mb_hint = None
    if link_only:
        print(f"[BaleUpload] confidence: skip chain -> Telegram link ({plan_reason})", flush=True)
        return send_telegram_link_fallback(
            bale_chat_id,
            message,
            caption,
            caption_parse_mode,
            source_key,
            reply_bale,
            bucket=bucket,
            src_mb=src_mb_hint,
            force=True,
        )

    def _fallback_on_413(stage):
        # Force Telegram-link fallback only for this specific message when Bale says 413.
        # This does NOT enable link fallback globally for the whole audio bucket.
        if bucket not in ("video", "image", "other", "audio"):
            return None
        if not _media_upload_failed_with_413():
            return None
        print(
            f"[BaleUpload] {stage}: Bale returned 413 (Request Entity Too Large) -> Telegram link",
            flush=True,
        )
        return send_telegram_link_fallback(
            bale_chat_id,
            message,
            caption,
            caption_parse_mode,
            source_key,
            reply_bale,
            bucket=bucket,
            src_mb=src_mb_hint,
            force=True,
        )

    def _read_for(path):
        t = effective_upload_read_timeout(path)
        if plan_read_cap is not None:
            t = min(t, plan_read_cap)
        return t

    def _try_split(reason):
        if bucket != "audio":
            return None
        mid = upload_audio_split_parts(
            file_path,
            bale_chat_id,
            caption,
            caption_parse_mode,
            reply_bale,
            upload_max_attempts=plan_max_attempts,
            read_timeout_cap=plan_read_cap,
        )
        if mid is not None:
            print(f"[BaleUpload] split upload OK ({reason})", flush=True)
        return mid

    if audio_needs_immediate_split(file_path):
        try:
            mb = file_size_mb(file_path)
        except OSError:
            mb = 0.0
        print(
            f"[BaleUpload] large audio {mb:.1f}MB > {audio_split_above_mb}MB "
            f"-> split only (skip single-file compress/upload)",
            flush=True,
        )
        return _try_split(f"source>{audio_split_above_mb}MB")

    upload_path = file_path
    extra_delete = None
    preprocessed = False

    if should_compress_small_first(file_path):
        try:
            mb = file_size_mb(file_path)
            print(
                f"[BaleUpload] small-first: {bucket} {mb:.2f}MB "
                f"-> compress before upload (Iran/Bale-friendly)",
                flush=True,
            )
        except OSError:
            print("[BaleUpload] small-first: compress before upload", flush=True)
        compressed_path, extra_delete = compress_media_for_bale_retry(file_path)
        if compressed_path:
            upload_path = compressed_path
            preprocessed = True
        elif bucket == "audio":
            mid = _try_split("compress ladder exhausted")
            if mid is not None:
                safe_remove(extra_delete)
                return mid
        elif (
            bucket == "video"
            and os.path.isfile(file_path)
            and file_size_mb(file_path) >= video_skip_raw_upload_above_mb
        ):
            print(
                "[BaleUpload] large video: compress failed, skipping raw upload -> Telegram link",
                flush=True,
            )
            return send_telegram_link_fallback(
                bale_chat_id,
                message,
                caption,
                caption_parse_mode,
                source_key,
                reply_bale,
                bucket=bucket,
                src_mb=src_mb_hint,
            )

    read_to = _read_for(upload_path)
    mid = send_media_to_bale(
        bale_chat_id,
        upload_path,
        caption,
        read_to,
        caption_parse_mode,
        preprocessed,
        reply_bale,
        upload_max_attempts=plan_max_attempts,
    )
    if mid is not None:
        safe_remove(extra_delete)
        return mid
    fallback_mid = _fallback_on_413("direct upload")
    if fallback_mid is not None:
        safe_remove(extra_delete)
        return fallback_mid

    if not preprocessed:
        compressed_path, extra_delete = compress_media_for_bale_retry(file_path)
        if compressed_path:
            try:
                read_c = _read_for(compressed_path)
                print("[BaleUpload] retrying after compress", flush=True)
                mid = send_media_to_bale(
                    bale_chat_id,
                    compressed_path,
                    caption,
                    read_c,
                    caption_parse_mode,
                    True,
                    reply_bale,
                    upload_max_attempts=plan_max_attempts,
                )
                if mid is not None:
                    return mid
                fallback_mid = _fallback_on_413("post-compress upload")
                if fallback_mid is not None:
                    return fallback_mid
            finally:
                safe_remove(extra_delete)
    else:
        safe_remove(extra_delete)

    if bucket == "audio":
        mid = _try_split("upload+compress failed")
        if mid is not None:
            return mid

    return send_telegram_link_fallback(
        bale_chat_id,
        message,
        caption,
        caption_parse_mode,
        source_key,
        reply_bale,
        bucket=bucket,
        src_mb=src_mb_hint,
    )


def prepare_large_audio(file_path):
    """
    Returns (path_to_upload, extra_path_to_delete_or_None).

    Note: Re-encoding is not bit-identical to the source. Opus at a high bitrate
    is usually transparent for voice while shrinking huge Telegram voice/MPEG files.
    """
    if not audio_reencode_enable or not is_audio_path(file_path):
        return file_path, None
    try:
        mb = file_size_mb(file_path)
    except OSError:
        return file_path, None
    if mb < audio_reencode_min_mb:
        return file_path, None
    base, _ = os.path.splitext(file_path)
    out_path = base + ".reencoded.opus.ogg"
    if transcode_to_opus_ogg(file_path, out_path):
        try:
            new_mb = file_size_mb(out_path)
            print(
                f"[Audio] Re-encoded large file {mb:.1f}MB -> {new_mb:.1f}MB opus_bitrate={audio_opus_bitrate}",
                flush=True,
            )
        except OSError:
            pass
        return out_path, out_path
    print("[WARN][Audio] ffmpeg re-encode failed; sending original", flush=True)
    return file_path, None


_PLAIN_LINK_APPEND_ALIASES = frozenset({"inline", "newline", "footer"})


def parse_mapping(raw_mapping):
    mapping = {}
    plain_link_append_by_source = {}
    for entry in (raw_mapping or "").split(";"):
        item = entry.strip()
        if not item:
            continue
        if "->" in item:
            source, bale_chat_id = item.split("->", 1)
        elif ":" in item and not item.startswith("http://") and not item.startswith("https://"):
            source, bale_chat_id = item.split(":", 1)
        else:
            raise ValueError(
                f"Invalid mapping item: {item}. Use either source->bale_chat_id or source:bale_chat_id"
            )
        source = source.strip()
        bale_chat_id = bale_chat_id.strip()
        plain_override = None
        if "|" in bale_chat_id:
            left, right = bale_chat_id.rsplit("|", 1)
            cand = right.strip().lower()
            if cand in _PLAIN_LINK_APPEND_ALIASES:
                bale_chat_id = left.strip()
                plain_override = cand
        if not source or not bale_chat_id:
            raise ValueError(f"Invalid mapping item: {item}")
        mapping[source] = bale_chat_id
        if plain_override:
            plain_link_append_by_source[source] = plain_override
    if not mapping:
        raise ValueError("SOURCE_TO_BALE_MAPPING is empty or invalid.")
    return mapping, plain_link_append_by_source


SOURCE_TO_BALE, SOURCE_PLAIN_LINK_APPEND = parse_mapping(source_to_bale_mapping_raw)
SOURCE_CHATS = list(SOURCE_TO_BALE.keys())


def plain_link_append_for_source(source_key):
    """Effective BALE_PLAIN_LINK_APPEND for this Telegram source mapping key (or global default)."""
    if not source_key:
        return bale_plain_link_append
    return SOURCE_PLAIN_LINK_APPEND.get(source_key, bale_plain_link_append)


def parse_topic_mapping(raw_mapping):
    """
    topic_id->@bale_dest forwards that forum topic.
    topic_id->null|none|skip|empty means do not forward (same as omitting from map when strict routing is on).
    """
    mapping = {}
    explicit_skip = set()
    for entry in (raw_mapping or "").split(";"):
        item = entry.strip()
        if not item:
            continue
        if "->" not in item:
            raise ValueError(f"Invalid TOPIC_TO_BALE_MAPPING item: {item}. Use topic_id->bale_chat_id")
        topic_id_raw, bale_chat_id = item.split("->", 1)
        topic_id = int(topic_id_raw.strip())
        bale_chat_id = bale_chat_id.strip()
        if not bale_chat_id or bale_chat_id.lower() in ("null", "none", "skip", "-"):
            explicit_skip.add(topic_id)
            continue
        mapping[topic_id] = bale_chat_id
    return mapping, explicit_skip


TOPIC_TO_BALE, TOPIC_EXPLICIT_SKIP = parse_topic_mapping(topic_to_bale_mapping_raw)
STRICT_TOPIC_ROUTING_SOURCES = set(
    s.strip() for s in strict_topic_routing_sources_raw.split(";") if s.strip()
)


def parse_int_set(raw):
    out = set()
    for token in (raw or "").replace(",", ";").split(";"):
        token = token.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            pass
    return out


EXCLUDE_SEND_TOPIC_IDS = parse_int_set(exclude_send_topic_ids_raw) | TOPIC_EXPLICIT_SKIP
INCLUDE_SEND_TOPIC_IDS = parse_int_set(include_send_topic_ids_raw)

def db_connect():
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
              source TEXT NOT NULL,
              bale_chat_id TEXT NOT NULL,
              tg_chat_id INTEGER NOT NULL,
              tg_message_id INTEGER NOT NULL,
              topic_id INTEGER,
              msg_date TEXT,
              msg_kind TEXT,
              status TEXT NOT NULL DEFAULT 'pending',
              retries INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (tg_chat_id, tg_message_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status_date ON queue(status, msg_date);")
        existing_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(queue)").fetchall()
        }
        if "topic_id" not in existing_cols:
            conn.execute("ALTER TABLE queue ADD COLUMN topic_id INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_queue_topic_order ON queue(status, topic_id, msg_date, tg_message_id);"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bale_msg_map (
              bale_chat_id TEXT NOT NULL,
              tg_chat_id INTEGER NOT NULL,
              tg_message_id INTEGER NOT NULL,
              bale_message_id INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (bale_chat_id, tg_chat_id, tg_message_id)
            )
            """
        )


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_upsert_pending(conn, source, bale_chat_id, tg_chat_id, tg_message_id, topic_id, msg_date, msg_kind):
    """
    Insert new queue row, or refresh topic_id/msg_date/msg_kind/source/bale_chat_id on conflict
    (so re-crawl picks up fixed get_topic_id without losing send status).
    Returns 'insert' or 'update'.
    """
    now = utc_now_iso()
    existed = conn.execute(
        "SELECT 1 FROM queue WHERE tg_chat_id=? AND tg_message_id=?",
        (tg_chat_id, tg_message_id),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO queue(
          source, bale_chat_id, tg_chat_id, tg_message_id, topic_id,
          msg_date, msg_kind, status, retries, last_error,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?)
        ON CONFLICT(tg_chat_id, tg_message_id) DO UPDATE SET
          source = excluded.source,
          bale_chat_id = excluded.bale_chat_id,
          topic_id = excluded.topic_id,
          msg_date = excluded.msg_date,
          msg_kind = excluded.msg_kind,
          updated_at = excluded.updated_at
        """,
        (source, bale_chat_id, tg_chat_id, tg_message_id, topic_id, msg_date, msg_kind, now, now),
    )
    return "update" if existed else "insert"


def db_mark_sent(tg_chat_id, tg_message_id):
    now = utc_now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE queue
            SET status='sent', updated_at=?, last_error=NULL
            WHERE tg_chat_id=? AND tg_message_id=?
            """,
            (now, tg_chat_id, tg_message_id),
        )


def db_get_queue_status(tg_chat_id, tg_message_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT status FROM queue WHERE tg_chat_id=? AND tg_message_id=?",
            (tg_chat_id, tg_message_id),
        ).fetchone()
        return row[0] if row else None


def db_get_queue_retries(tg_chat_id, tg_message_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT retries FROM queue WHERE tg_chat_id=? AND tg_message_id=?",
            (tg_chat_id, tg_message_id),
        ).fetchone()
        return int(row[0]) if row else 0


def queue_enqueue_from_message(source, message):
    """Add or refresh one Telegram message in the send queue (crawl + live)."""
    bale_chat_id = SOURCE_TO_BALE[source]
    msg_date = message.date.isoformat() if getattr(message, "date", None) else None
    with db_connect() as conn:
        db_upsert_pending(
            conn,
            source,
            bale_chat_id,
            int(message.chat_id),
            int(message.id),
            get_topic_id(message),
            msg_date,
            classify_message_kind(message),
        )
        conn.commit()


def _send_queue_lock_get():
    global _send_queue_lock
    if _send_queue_lock is None:
        _send_queue_lock = asyncio.Lock()
    return _send_queue_lock


async def trigger_send_queue():
    """Run send queue once (serialized; safe to call from live handler)."""
    async with _send_queue_lock_get():
        await run_send()


def db_reset_sent_failed_for_replay():
    """Reset sent/failed to pending so the next send run starts from the beginning of the queue."""
    with db_connect() as conn:
        cur = conn.execute(
            """
            UPDATE queue
            SET status='pending', retries=0, last_error=NULL
            WHERE status IN ('sent', 'failed')
            """
        )
        conn.commit()
        return cur.rowcount


def db_bale_map_put(bale_chat_id, tg_chat_id, tg_message_id, bale_message_id):
    """Remember Bale message_id for a Telegram message (same Bale chat) so replies can use reply_to_message_id."""
    now = utc_now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO bale_msg_map(bale_chat_id, tg_chat_id, tg_message_id, bale_message_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(bale_chat_id, tg_chat_id, tg_message_id) DO UPDATE SET
              bale_message_id = excluded.bale_message_id,
              created_at = excluded.created_at
            """,
            (str(bale_chat_id), tg_chat_id, tg_message_id, int(bale_message_id), now),
        )
        conn.commit()


def db_bale_map_get(bale_chat_id, tg_chat_id, tg_message_id):
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT bale_message_id FROM bale_msg_map
            WHERE bale_chat_id=? AND tg_chat_id=? AND tg_message_id=?
            """,
            (str(bale_chat_id), tg_chat_id, tg_message_id),
        ).fetchone()
        if row:
            return int(row[0])
    return None


def db_bale_map_clear_all():
    with db_connect() as conn:
        conn.execute("DELETE FROM bale_msg_map")
        conn.commit()


def db_mark_failed(tg_chat_id, tg_message_id, error_text):
    now = utc_now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE queue
            SET status='failed',
                retries = retries + 1,
                last_error = ?,
                updated_at = ?
            WHERE tg_chat_id=? AND tg_message_id=?
            """,
            (error_text[:800], now, tg_chat_id, tg_message_id),
        )


def db_mark_skipped(tg_chat_id, tg_message_id, reason):
    now = utc_now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE queue
            SET status='skipped',
                last_error = ?,
                updated_at = ?
            WHERE tg_chat_id=? AND tg_message_id=?
            """,
            (reason[:800], now, tg_chat_id, tg_message_id),
        )


def db_queue_row_count():
    with db_connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        return int(row[0]) if row else 0


def db_fetch_batch(statuses):
    placeholders = ",".join(["?"] * len(statuses))
    source_clause = ""
    source_params = []
    if SOURCE_CHATS:
        src_ph = ",".join(["?"] * len(SOURCE_CHATS))
        source_clause = f" AND source IN ({src_ph})"
        source_params = list(SOURCE_CHATS)
    include_clause = ""
    include_params = []
    if INCLUDE_SEND_TOPIC_IDS:
        inc_ph = ",".join(["?"] * len(INCLUDE_SEND_TOPIC_IDS))
        include_clause = f" AND topic_id IN ({inc_ph})"
        include_params = sorted(INCLUDE_SEND_TOPIC_IDS)
    if send_topic_by_topic:
        order_sql = """
            ORDER BY source ASC,
                     (topic_id IS NULL) ASC,
                     topic_id ASC,
                     COALESCE(msg_date, '') ASC,
                     tg_message_id ASC
        """
    else:
        order_sql = """
            ORDER BY source ASC,
                     COALESCE(msg_date, '') ASC,
                     tg_message_id ASC
        """
    retry_clause = ""
    retry_params = []
    if max_retries > 0:
        retry_clause = " AND retries <= ?"
        retry_params = [max_retries]
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM queue
            WHERE status IN ({placeholders})
              {retry_clause}
              {source_clause}
              {include_clause}
            {order_sql}
            LIMIT ?
            """,
            (*statuses, *retry_params, *source_params, *include_params, send_batch_size),
        ).fetchall()
        return rows


def extract_invite_hash(source):
    plus_token = "/+"
    if plus_token in source:
        return source.split(plus_token, 1)[1].split("?", 1)[0].strip("/")
    return ""


def get_topic_id(message):
    """
    Forum topic id for queue ordering / routing.

    Most messages use reply_to.reply_to_top_id. The *first* post in a topic sometimes
    only has reply_to.forum_topic + reply_to_msg_id (no top_id); treat msg_id as topic anchor.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to:
        top_id = getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "top_msg_id", None)
        if top_id:
            return int(top_id)
        if getattr(reply_to, "forum_topic", None) and getattr(reply_to, "reply_to_msg_id", None):
            return int(reply_to.reply_to_msg_id)

    action = getattr(message, "action", None)
    if action and action.__class__.__name__ == "MessageActionTopicCreate":
        return int(message.id)

    return None


def message_has_text_body(message):
    """True if the message has non-empty plain or formatted text (TL body or Telethon .text)."""
    raw = getattr(message, "message", None)
    if raw is not None and str(raw).strip():
        return True
    if getattr(message, "text", None) and str(message.text).strip():
        return True
    return False


# Telegram sets message.media for link previews etc.; these are not files to download.
_NON_FILE_MEDIA_TYPES = (
    MessageMediaWebPage,
    MessageMediaPoll,
    MessageMediaGeo,
    MessageMediaContact,
    MessageMediaVenue,
    MessageMediaGame,
    MessageMediaInvoice,
)


def message_has_downloadable_file(message):
    """True when we should download bytes and upload to Bale (not link-preview-only media)."""
    if not message.media:
        return False
    if isinstance(message.media, _NON_FILE_MEDIA_TYPES):
        return False
    if message.photo or message.video:
        return True
    if getattr(message, "voice", None) or getattr(message, "audio", None):
        return True
    if getattr(message, "sticker", None):
        return True
    if message.document and message.file:
        return True
    if isinstance(message.media, (MessageMediaPhoto, MessageMediaDocument)):
        return True
    return False


def _extension_from_telegram_file(message):
    if getattr(message, "file", None) and message.file.name:
        ext = os.path.splitext(message.file.name)[1].lower()
        if ext:
            return ext
    return ""


def resolve_media_download_kind_ext(message):
    """
    Return (msg_kind_label, file_extension) for temp download path and logging.
    """
    if message.photo:
        return "photo", ".jpg"
    if message.video:
        return "video", ".mp4"
    if getattr(message, "voice", None):
        return "voice", _extension_from_telegram_file(message) or ".oga"
    if getattr(message, "audio", None):
        mime = getattr(message.file, "mime_type", None) if message.file else None
        ext = _extension_from_telegram_file(message) or mimetypes.guess_extension(mime or "") or ".mp3"
        return f"audio:{mime or 'audio'}", ext
    if getattr(message, "sticker", None):
        return "sticker", _extension_from_telegram_file(message) or ".webp"
    if message.document and message.file:
        mime = message.file.mime_type or ""
        ext = _extension_from_telegram_file(message) or mimetypes.guess_extension(mime) or ""
        if not ext and mime.startswith("audio/"):
            ext = ".mp3"
        if not ext:
            ext = ".bin"
        return f"document:{mime}", ext
    return "other", ".bin"


_LTR_MARK = "\u200e"


def normalize_link_url_for_bale(url: str) -> str:
    """Normalize Telegram hrefs so Bale and autolink parsers see stable https / t.me shapes."""
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("//"):
        u = "https:" + u
    lu = u.lower()
    if lu.startswith("http://t.me/") or lu.startswith("http://telegram.me/"):
        u = "https" + u[4:]
    if lu.startswith("https://telegram.me/"):
        u = "https://t.me/" + u[len("https://telegram.me/") :]
    return u


def normalize_entity_text_urls_for_bale(entities):
    """Shallow copy of entities with MessageEntityTextUrl.url normalized (helps HTML href + plain append)."""
    if not entities:
        return entities
    out = []
    for ent in entities:
        if isinstance(ent, MessageEntityTextUrl):
            nu = normalize_link_url_for_bale(ent.url)
            if nu != ent.url:
                ent = MessageEntityTextUrl(offset=ent.offset, length=ent.length, url=nu)
        out.append(ent)
    return out


def _normalized_url_for_plain_append(url: str) -> str:
    """URL string for plain-text append (never insert bidi marks inside the URL itself)."""
    return normalize_link_url_for_bale(url)


def expand_plain_links_for_bale(raw, entities, plain_link_append=None):
    """
    Plain-text path: resolve MessageEntityTextUrl / MessageEntityMentionName by appending URLs
    so clients can autolink without parse_mode.
    """
    if not entities:
        return raw or ""

    mode = plain_link_append if plain_link_append is not None else bale_plain_link_append
    if mode not in _PLAIN_LINK_APPEND_ALIASES:
        mode = bale_plain_link_append

    if mode == "footer":
        urls = []
        seen = set()
        for ent in sorted(entities, key=lambda e: (getattr(e, "offset", 0), getattr(e, "length", 0))):
            if isinstance(ent, MessageEntityTextUrl):
                key = normalize_link_url_for_bale(ent.url)
                if key and key not in seen:
                    seen.add(key)
                    urls.append(_normalized_url_for_plain_append(ent.url))
            elif isinstance(ent, MessageEntityMentionName):
                key = f"tg://user?id={ent.user_id}"
                if key not in seen:
                    seen.add(key)
                    urls.append(_normalized_url_for_plain_append(key))
        body = del_surrogate(add_surrogate(raw or ""))
        if not urls:
            return body
        return body + "\n\n" + "\n".join(urls)

    ltr = _LTR_MARK if bale_link_ltr_mark else ""
    text = add_surrogate(raw or "")
    insert_at = []
    for i, ent in enumerate(entities):
        if isinstance(ent, MessageEntityTextUrl):
            e = ent.offset + ent.length
            u = _normalized_url_for_plain_append(ent.url)
            if mode == "inline":
                frag = f"{ltr} ({u})" if ltr else f" ({u})"
            else:
                frag = f"\n{u}"
            insert_at.append((e, i, frag))
        elif isinstance(ent, MessageEntityMentionName):
            e = ent.offset + ent.length
            u = _normalized_url_for_plain_append(f"tg://user?id={ent.user_id}")
            if mode == "inline":
                frag = f"{ltr} ({u})" if ltr else f" ({u})"
            else:
                frag = f"\n{u}"
            insert_at.append((e, i, frag))
    insert_at.sort(key=lambda t: (t[0], t[1]))
    while insert_at:
        at, _, what = insert_at.pop()
        while within_surrogate(text, at):
            at += 1
        text = text[:at] + what + text[at:]
    return del_surrogate(text)


def _try_unparse_html_for_bale(raw, entities, message_id=None):
    """Telethon HTML (<a href>…</a> etc.) for Bale parse_mode=HTML. Returns (text, 'HTML') or None."""
    if not entities:
        return None
    try:
        ents = normalize_entity_text_urls_for_bale(entities)
        return tg_html.unparse(raw, ents), "HTML"
    except Exception as e:
        mid = message_id if message_id is not None else "?"
        print(f"[WARN] HTML unparse msg={mid}: {e}", flush=True)
        return None


def _try_unparse_markdown_for_bale(raw, entities, message_id=None):
    if not entities:
        return None
    try:
        from telethon.extensions import markdown as tg_md

        ents = normalize_entity_text_urls_for_bale(entities)
        return tg_md.unparse(raw, ents), "Markdown"
    except Exception as e:
        mid = message_id if message_id is not None else "?"
        print(f"[WARN] Markdown unparse msg={mid}: {e}", flush=True)
        return None


def _resolved_plain_link_mode(plain_link_append):
    pl = plain_link_append if plain_link_append is not None else bale_plain_link_append
    if pl not in _PLAIN_LINK_APPEND_ALIASES:
        pl = bale_plain_link_append
    return pl


def message_to_bale_text(message, plain_link_append=None):
    """
    Build (text, parse_mode) for Bale sendMessage / captions.

    Strategies (BALE_TEXT_FORMAT):
      plain_links — append resolved URLs; no parse_mode (BALE_PLAIN_LINK_APPEND, BALE_LINK_LTR_MARK).
      html / markdown — hyperlinks via Telethon unparsers + parse_mode (Bale «ایجاد پیوند»-class output).
      rich — try html first, then plain_links (best cross-client when HTML works).

    Per-source plain append: SOURCE_TO_BALE_MAPPING … -> bale|footer etc.
    """
    raw = getattr(message, "message", None) or ""
    entities = list(getattr(message, "entities", None) or [])
    pl_mode = _resolved_plain_link_mode(plain_link_append)
    fmt = bale_text_format
    mid = getattr(message, "id", None)

    def plain_with_entities():
        try:
            return expand_plain_links_for_bale(raw, entities, pl_mode), None
        except Exception as e:
            print(f"[WARN] plain link expand msg={mid}: {e}", flush=True)
            return None

    def prefer_telethon_text():
        mt = getattr(message, "text", None)
        if mt is not None and str(mt).strip():
            return str(mt), None
        return raw, None

    if fmt == "rich":
        html_pair = _try_unparse_html_for_bale(raw, entities, mid)
        if html_pair is not None:
            return html_pair
        pl = plain_with_entities()
        if pl is not None:
            return pl
        return prefer_telethon_text()

    if fmt == "html":
        html_pair = _try_unparse_html_for_bale(raw, entities, mid)
        if html_pair is not None:
            return html_pair
    elif fmt == "markdown":
        md_pair = _try_unparse_markdown_for_bale(raw, entities, mid)
        if md_pair is not None:
            return md_pair

    if fmt == "plain_links":
        return expand_plain_links_for_bale(raw, entities, pl_mode), None

    if entities and fmt in ("html", "markdown"):
        pl = plain_with_entities()
        if pl is not None:
            return pl

    return prefer_telethon_text()


def telegram_reply_target_message_id(message):
    """
    Telegram message id this message replies to (same chat only), for Bale reply_to_message_id.

    For forum threads, if only the topic anchor is set (no reply_to_msg_id), use reply_to_top_id.
    """
    rt = getattr(message, "reply_to", None)
    if not rt:
        return None
    if getattr(rt, "reply_to_peer_id", None):
        return None
    rid = getattr(rt, "reply_to_msg_id", None)
    if rid:
        return int(rid)
    if getattr(rt, "forum_topic", None):
        top = getattr(rt, "reply_to_top_id", None)
        if top:
            return int(top)
    return None


def _bale_json_ok_payload(body_text):
    """Parse Bale API JSON; return result object/list when ok=true (any HTTP status)."""
    if not body_text or not str(body_text).strip().startswith("{"):
        return None
    try:
        j = json.loads(body_text)
        if j.get("ok"):
            return j.get("result")
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def _bale_response_message_id(status_code, body_text):
    res = _bale_json_ok_payload(body_text)
    if isinstance(res, dict):
        mid = res.get("message_id")
        if mid is not None:
            return int(mid)
    return None


def _parse_bale_send_media_group_ids(status_code, body_text):
    """Bale/Telegram-style sendMediaGroup returns result: [ { message_id, ... }, ... ]."""
    res = _bale_json_ok_payload(body_text)
    if not isinstance(res, list) or not res:
        return None
    out = []
    for m in res:
        if not isinstance(m, dict):
            return None
        mid = m.get("message_id")
        if mid is None:
            return None
        out.append(int(mid))
    return out


def _bale_http_looks_like_gateway_timeout(status_code):
    return status_code in (502, 504)


def _post_send_media_group_once(
    bale_chat_id,
    file_paths,
    media_type,
    caption,
    caption_parse_mode,
    reply_to_message_id,
    read_timeout,
    connect_timeout,
):
    """POST /sendMediaGroup with attach://f0..fN and type photo|document per Telegram Bot API."""
    n = len(file_paths)
    if n < 2 or n > 10:
        raise ValueError("sendMediaGroup requires 2..10 files")
    if media_type not in ("photo", "document"):
        raise ValueError("media_type must be photo or document")
    media = []
    files = {}
    handles = []
    try:
        for i, path in enumerate(file_paths):
            key = f"f{i}"
            media.append({"type": media_type, "media": f"attach://{key}"})
            fh = open(path, "rb")
            handles.append(fh)
            files[key] = (os.path.basename(path), fh)
        if caption:
            media[0]["caption"] = caption
        if caption_parse_mode and caption:
            media[0]["parse_mode"] = caption_parse_mode
        data = {"chat_id": str(bale_chat_id), "media": json.dumps(media, ensure_ascii=False)}
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(int(reply_to_message_id))
        r = requests.post(
            f"{BALE_API}/sendMediaGroup",
            data=data,
            files=files,
            timeout=(float(connect_timeout), float(read_timeout)),
            **_bale_request_kwargs(),
        )
        return r.status_code, r.text
    finally:
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass


def _media_group_paths_total_mb(file_paths):
    total = 0.0
    for path in file_paths:
        try:
            total += os.path.getsize(path) / (1024.0 * 1024.0)
        except OSError:
            pass
    return total


def send_media_group_to_bale(
    bale_chat_id,
    file_paths,
    media_type,
    caption=None,
    caption_parse_mode=None,
    reply_to_message_id=None,
):
    """
    Upload 2–10 files as one Bale sendMediaGroup (media_type: photo | document).
    Returns list of Bale message_id in order, or None.
    """
    n = len(file_paths)
    if n < 2 or n > 10:
        return None
    total_mb = _media_group_paths_total_mb(file_paths)
    try:
        per_file_cap = max(
            (effective_upload_read_timeout(p) for p in file_paths),
            default=upload_timeout_seconds,
        )
    except OSError:
        per_file_cap = upload_timeout_seconds

    read_timeout = max(per_file_cap, upload_timeout_seconds + upload_sec_per_mb * total_mb)
    read_timeout += 45.0 * n
    if media_type == "document":
        read_timeout *= bale_document_media_group_timeout_multiplier
        read_timeout = max(read_timeout, 180.0 + upload_sec_per_mb * total_mb * 0.75)
    read_timeout = min(max_upload_timeout_seconds, int(read_timeout))

    connect_timeout = max(30.0, float(bale_media_group_connect_seconds))
    if media_type == "document":
        connect_timeout = max(connect_timeout, 60.0)

    print(
        f"[MediaGroup] type={media_type} n={n} total_mb={total_mb:.2f} "
        f"connect={connect_timeout:.0f}s read={read_timeout}s",
        flush=True,
    )

    for attempt in range(bale_media_upload_attempts):
        try:
            code, body = _post_send_media_group_once(
                bale_chat_id,
                file_paths,
                media_type,
                caption,
                caption_parse_mode,
                reply_to_message_id,
                read_timeout,
                connect_timeout,
            )
            ids = _parse_bale_send_media_group_ids(code, body)
            if ids is not None and len(ids) == n:
                if code != 200:
                    print(
                        f"[WARN][MediaGroup] HTTP {code} but Bale ok=true; treating as success ids={ids}",
                        flush=True,
                    )
                print(
                    f"[OK][MediaGroup] type={media_type} chat={bale_chat_id} n={n} bale_msg_ids={ids}",
                    flush=True,
                )
                return ids
            snippet = _bale_error_body_snippet(body)
            print(
                f"[ERR][MediaGroup] type={media_type} attempt={attempt + 1}/{bale_media_upload_attempts} "
                f"chat={bale_chat_id} status={code} body={snippet!r}",
                flush=True,
            )
            if (
                bale_should_retry(code)
                and attempt + 1 < bale_media_upload_attempts
                and not _bale_http_looks_like_gateway_timeout(code)
            ):
                delay = bale_retry_sleep(attempt)
                print(f"[BaleUpload] HTTP {code}; sleeping {delay:.1f}s before retry", flush=True)
                time.sleep(delay)
        except Exception as e:
            print(
                f"[ERR][MediaGroup] type={media_type} attempt={attempt + 1}/{bale_media_upload_attempts} "
                f"chat={bale_chat_id} error={e}",
                flush=True,
            )
            if bale_should_retry(exc=e) and attempt + 1 < bale_media_upload_attempts:
                time.sleep(bale_retry_sleep(attempt))
    return None


def _album_all_documents(parts):
    """True if every part is a document with a file (no photo primary — avoids photo album branch)."""
    if not parts:
        return False
    for p in parts:
        if not getattr(p, "document", None) or not getattr(p, "file", None):
            return False
        if getattr(p, "photo", None):
            return False
    return True


def _message_file_size_mb(msg):
    f = getattr(msg, "file", None)
    size = getattr(f, "size", None)
    if size is None:
        return None
    try:
        b = int(size)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    return b / (1024.0 * 1024.0)


def _album_can_use_media_group(parts, media_kind):
    """
    For large albums, avoid one huge multipart sendMediaGroup and use per-part tiered upload.
    This mirrors audio reliability strategy (preempt brittle paths), without splitting.
    """
    if len(parts) <= 1:
        return False
    if media_kind == "photo":
        max_total = bale_photo_album_media_group_max_total_mb
    elif media_kind == "document":
        max_total = bale_document_album_media_group_max_total_mb
    else:
        return False

    sizes = [_message_file_size_mb(p) for p in parts]
    known = [s for s in sizes if s is not None]
    if not known:
        return True
    total_mb = sum(known)
    if total_mb > max_total:
        print(
            f"[Album][MediaGroup] skip kind={media_kind}: estimated total {total_mb:.2f}MB > "
            f"{max_total:.2f}MB; using per-part upload",
            flush=True,
        )
        return False
    if media_kind == "document":
        max_file = max(known)
        if max_file > bale_document_album_media_group_max_file_mb:
            print(
                f"[Album][MediaGroup] skip kind=document: largest file {max_file:.2f}MB > "
                f"{bale_document_album_media_group_max_file_mb:.2f}MB; using per-part upload",
                flush=True,
            )
            return False
    return True


async def _forward_homogeneous_media_group_album(parts, bale_chat_id, source_key, media_kind):
    """
    Send a Telegram album of only photos or only documents to Bale as sendMediaGroup.
    Photo chunks: up to 10; document chunks: up to bale_document_media_group_max_files (default 4).
    media_kind: 'photo' | 'document'
    """
    pl = plain_link_append_for_source(source_key)
    reply_tg = telegram_reply_target_message_id(parts[0])
    reply_bale = None
    if reply_tg is not None:
        reply_bale = db_bale_map_get(bale_chat_id, int(parts[0].chat_id), reply_tg)
        if reply_bale is not None:
            print(
                f"[Reply][Album] tg_reply_to={reply_tg} tg_msg={parts[0].id} -> "
                f"bale_reply_to_message_id={reply_bale}",
                flush=True,
            )
    cap_src = None
    for p in parts:
        if message_has_text_body(p):
            cap_src = p
            break
    if cap_src is None:
        cap_src = parts[0]
    caption_full, caption_mode = message_to_bale_text(cap_src, plain_link_append=pl)
    if not str(caption_full or "").strip():
        caption_full, caption_mode = None, None

    outcomes = []
    i = 0
    n = len(parts)
    first_chunk = True
    while i < n:
        rem = n - i
        if rem == 1:
            outcomes.append(await forward_message_to_bale(parts[i], bale_chat_id, source_key))
            i += 1
            continue
        max_per = 10 if media_kind == "photo" else bale_document_media_group_max_files
        cs = min(max_per, rem)
        chunk = parts[i : i + cs]
        paths = []
        try:
            print(
                f"[Album][MediaGroup] kind={media_kind} grouped_id={getattr(chunk[0], 'grouped_id', None)} "
                f"chunk={len(chunk)} tg_ids={[p.id for p in chunk]}",
                flush=True,
            )
            for p in chunk:
                if media_kind == "photo":
                    fp = f"temp/{int(p.chat_id)}_{int(p.id)}.jpg"
                else:
                    ext = ""
                    if p.file:
                        ext = mimetypes.guess_extension(p.file.mime_type or "") or ""
                    if not ext:
                        ext = ".bin"
                    fp = f"temp/{int(p.chat_id)}_{int(p.id)}{ext}"
                await telegram_call(
                    f"download album part msg={p.id}",
                    lambda p=p, fp=fp: client.download_media(p, fp),
                )
                paths.append(fp)
            cap = caption_full if first_chunk else None
            cmode = caption_mode if first_chunk else None
            rpl = reply_bale if first_chunk else None
            ids = await asyncio.to_thread(
                send_media_group_to_bale,
                bale_chat_id,
                paths,
                media_kind,
                cap,
                cmode,
                rpl,
            )
            if ids is None or len(ids) != len(chunk):
                raise RuntimeError("sendMediaGroup failed or unexpected message_id count")
            for p, mid in zip(chunk, ids):
                db_bale_map_put(bale_chat_id, int(p.chat_id), int(p.id), int(mid))
            outcomes.extend(["ok"] * len(chunk))
        except Exception as e:
            gateway_only = isinstance(e, RuntimeError) and "sendMediaGroup failed" in str(e)
            if gateway_only and bale_album_gateway_fail_retry_only:
                print(
                    f"[ERR][Album][MediaGroup] {e!r}; skip per-part fallback "
                    f"(BALE_ALBUM_GATEWAY_FAIL_RETRY_ONLY — avoids duplicate posts on 502/504)",
                    flush=True,
                )
                raise
            print(f"[ERR][Album][MediaGroup] {e!r}; per-part fallback", flush=True)
            for j, p in enumerate(chunk):
                if bale_row_already_forwarded(bale_chat_id, int(p.chat_id), int(p.id)):
                    print(f"[Album] skip tg_msg={p.id} (already on Bale)", flush=True)
                    outcomes.append("ok")
                    continue
                outcomes.append(
                    await forward_message_to_bale(
                        p,
                        bale_chat_id,
                        source_key,
                        include_caption=(j == 0 and first_chunk),
                    )
                )
            i += cs
            first_chunk = False
            continue
        finally:
            for fp in paths:
                safe_remove(fp)
        i += cs
        first_chunk = False

    await asyncio.sleep(per_message_delay_seconds)
    return outcomes


async def collect_album_messages(entity, root_msg):
    """
    Telegram albums / grouped media are multiple Message objects with the same grouped_id.
    Return all parts sorted by message id (oldest first within the group).
    """
    gid = getattr(root_msg, "grouped_id", None)
    if not gid:
        return [root_msg]
    span = 25
    low = max(1, root_msg.id - span)
    high = root_msg.id + span
    ids = list(range(low, high + 1))
    raw = await telegram_call(
        f"get_messages album ids={root_msg.id}",
        lambda: client.get_messages(entity, ids=ids),
    )
    if raw is None:
        return [root_msg]
    if not isinstance(raw, list):
        raw = [raw]
    album = [m for m in raw if m and getattr(m, "grouped_id", None) == gid]
    if not album:
        return [root_msg]
    album.sort(key=lambda x: x.id)
    return album


def _forward_outcome_success(outcome):
    return outcome in ("ok", "empty")


async def forward_album_parts_to_bale(parts, bale_chat_id, source_key=None):
    """
    Forward each album part. Homogeneous photo or document albums use Bale sendMediaGroup when enabled;
    mixed albums use per-part sends.

    With STRICT_SEND_ORDER=1, the first failed part raises (no later parts in the album or queue).
    sendMediaGroup failure does not fall back to per-part upload (fail fast, preserve order).
    """
    if (
        bale_photo_album_media_group
        and len(parts) > 1
        and all(getattr(p, "photo", None) for p in parts)
        and _album_can_use_media_group(parts, "photo")
    ):
        try:
            return await _forward_homogeneous_media_group_album(parts, bale_chat_id, source_key, "photo")
        except Exception as e:
            print(f"[WARN][Album] photo sendMediaGroup failed ({e!r}); per-part upload", flush=True)
    if (
        bale_document_album_media_group
        and len(parts) > 1
        and _album_all_documents(parts)
        and _album_can_use_media_group(parts, "document")
    ):
        try:
            return await _forward_homogeneous_media_group_album(parts, bale_chat_id, source_key, "document")
        except Exception as e:
            print(f"[WARN][Album] document sendMediaGroup failed ({e!r}); per-part upload", flush=True)
    outcomes = []
    leader_id = parts[0].id
    for part in parts:
        if bale_row_already_forwarded(bale_chat_id, int(part.chat_id), int(part.id)):
            print(f"[Album] skip tg_msg={part.id} (already on Bale)", flush=True)
            outcomes.append("ok")
            continue
        outcome = await forward_message_to_bale(
            part,
            bale_chat_id,
            source_key,
            include_caption=(part.id == leader_id),
        )
        outcomes.append(outcome)
        if strict_send_order and outcome == "failed":
            while len(outcomes) < len(parts):
                outcomes.append("failed")
            return outcomes
    return outcomes


async def resolve_source_for_telethon(source):
    source = source.strip()
    if source.startswith("http://") or source.startswith("https://"):
        invite_hash = extract_invite_hash(source)
        if invite_hash:
            try:
                invite = await telegram_call(
                    f"CheckChatInvite {source}",
                    lambda: client(functions.messages.CheckChatInviteRequest(invite_hash)),
                )
                if hasattr(invite, "chat") and invite.chat:
                    return invite.chat
            except Exception as e:
                print(f"[ERR][Resolve] invite={source} error={e}")
        try:
            return await telegram_call(
                f"get_entity {source}",
                lambda: client.get_entity(source),
            )
        except Exception as e:
            print(f"[ERR][Resolve] source={source} error={e}")
            return source
    return source


def send_text_to_bale(bale_chat_id, text, parse_mode=None, reply_to_message_id=None):
    text = text if text is not None else ""

    def _is_message_too_long(status_code, body_text):
        return int(status_code or 0) == 400 and "message is too long" in str(body_text or "").lower()

    def _split_text_for_bale(raw_text, limit):
        if len(raw_text) <= limit:
            return [raw_text]
        chunks = []
        rest = raw_text
        while len(rest) > limit:
            cut = rest.rfind("\n", 0, limit + 1)
            if cut < int(limit * 0.6):
                cut = rest.rfind(" ", 0, limit + 1)
            if cut < int(limit * 0.6):
                cut = limit
            part = rest[:cut].rstrip()
            if not part:
                part = rest[:limit]
                cut = limit
            chunks.append(part)
            rest = rest[cut:].lstrip()
        if rest:
            chunks.append(rest)
        return chunks

    payload = {"chat_id": bale_chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = int(reply_to_message_id)
    preview = text[:40] if text else ""
    for attempt in range(bale_media_upload_attempts):
        try:
            r = requests.post(
                f"{BALE_API}/sendMessage",
                json=payload,
                timeout=(20, 90),
                **_bale_request_kwargs(),
            )
            mid = _bale_response_message_id(r.status_code, r.text)
            if mid is not None:
                if r.status_code != 200:
                    print(
                        f"[WARN][Text] HTTP {r.status_code} but Bale ok=true bale_msg_id={mid}",
                        flush=True,
                    )
                print(f"[OK][Text] chat={bale_chat_id} text={preview!r} bale_msg_id={mid}")
                return mid
            if _is_message_too_long(r.status_code, r.text):
                chunks = _split_text_for_bale(text, bale_text_chunk_chars)
                if len(chunks) <= 1:
                    print(
                        f"[ERR][Text] Bale says message too long but split produced 1 chunk "
                        f"(len={len(text)}).",
                        flush=True,
                    )
                    return None
                print(
                    f"[Text] message too long -> split into {len(chunks)} chunks "
                    f"(max {bale_text_chunk_chars} chars)",
                    flush=True,
                )
                first_mid = None
                reply_to = reply_to_message_id
                for chunk in chunks:
                    chunk_mid = send_text_to_bale(
                        bale_chat_id,
                        chunk,
                        parse_mode=parse_mode,
                        reply_to_message_id=reply_to,
                    )
                    if chunk_mid is None:
                        return None
                    if first_mid is None:
                        first_mid = chunk_mid
                    reply_to = chunk_mid
                    time.sleep(per_message_delay_seconds)
                return first_mid
            snippet = _bale_error_body_snippet(r.text)
            print(
                f"[ERR][Text] attempt={attempt + 1}/{bale_media_upload_attempts} "
                f"chat={bale_chat_id} status={r.status_code} body={snippet!r}"
            )
            if bale_should_retry(r.status_code) and attempt + 1 < bale_media_upload_attempts:
                time.sleep(bale_retry_sleep(attempt))
        except Exception as e:
            print(
                f"[ERR][Text] attempt={attempt + 1}/{bale_media_upload_attempts} "
                f"chat={bale_chat_id} error={e}"
            )
            if bale_should_retry(exc=e) and attempt + 1 < bale_media_upload_attempts:
                time.sleep(bale_retry_sleep(attempt))
    return None


def _bale_error_body_snippet(response_text, limit=450):
    if not response_text:
        return ""
    return response_text.replace("\n", " ").strip()[:limit]


_last_media_upload_failure_code = None


def _media_upload_fail_reset():
    global _last_media_upload_failure_code
    _last_media_upload_failure_code = None


def _media_upload_fail_mark(status_code):
    global _last_media_upload_failure_code
    try:
        _last_media_upload_failure_code = int(status_code)
    except (TypeError, ValueError):
        return


def _media_upload_failed_with_413():
    return _last_media_upload_failure_code == 413


def _post_bale_file(
    bale_chat_id,
    endpoint,
    field_name,
    file_path,
    caption,
    read_timeout,
    caption_parse_mode=None,
    reply_to_message_id=None,
):
    connect_timeout = bale_connect_timeout(file_path)
    try:
        with open(file_path, "rb") as f:
            files = {field_name: (os.path.basename(file_path), f)}
            data = {"chat_id": bale_chat_id}
            if caption:
                data["caption"] = caption
                if caption_parse_mode:
                    data["parse_mode"] = caption_parse_mode
            if reply_to_message_id is not None:
                data["reply_to_message_id"] = str(int(reply_to_message_id))
            r = requests.post(
                f"{BALE_API}/{endpoint}",
                data=data,
                files=files,
                timeout=(connect_timeout, read_timeout),
                **_bale_request_kwargs(),
            )
        return r.status_code, r.text
    except OSError as e:
        # SSLError subclasses OSError — not a local file read failure.
        if isinstance(e, ssl.SSLError):
            print(f"[ERR][File] SSL/network file={file_path!r} error={e}", flush=True)
        else:
            print(f"[ERR][File] read failed file={file_path!r} error={e}", flush=True)
        return None, ""
    except requests.RequestException as e:
        print(f"[ERR][File] request failed file={file_path!r} error={e}", flush=True)
        return None, ""


def _try_upload_endpoint(
    bale_chat_id,
    endpoint,
    field_name,
    file_path,
    caption,
    read_timeout,
    label,
    caption_parse_mode=None,
    reply_to_message_id=None,
    max_attempts=None,
):
    """POST one Bale multipart upload until success or attempts exhausted. Backoff on HTTP 5xx."""
    n = max_attempts if max_attempts is not None else bale_media_upload_attempts
    n = max(1, n)
    _media_upload_fail_reset()
    for attempt in range(n):
        try:
            code, body = _post_bale_file(
                bale_chat_id,
                endpoint,
                field_name,
                file_path,
                caption,
                read_timeout,
                caption_parse_mode,
                reply_to_message_id,
            )
            mid = _bale_response_message_id(code, body)
            if mid is not None:
                if code != 200:
                    print(
                        f"[WARN][File] HTTP {code} but Bale ok=true; treating as success "
                        f"via={endpoint} bale_msg_id={mid}",
                        flush=True,
                    )
                _media_upload_fail_reset()
                print(f"[OK][File] chat={bale_chat_id} via={endpoint} file={file_path} bale_msg_id={mid}")
                return mid
            if code is None:
                if attempt + 1 < n:
                    time.sleep(bale_retry_sleep(attempt))
                continue
            _media_upload_fail_mark(code)
            snippet = _bale_error_body_snippet(body)
            print(
                f"[ERR][File] {label} attempt={attempt + 1}/{n} "
                f"chat={bale_chat_id} status={code} via={endpoint} body={snippet!r}"
            )
            if (
                bale_should_retry(code)
                and attempt + 1 < n
                and not (_bale_http_looks_like_gateway_timeout(code) and code is not None)
            ):
                time.sleep(bale_retry_sleep(attempt))
            elif _bale_http_looks_like_gateway_timeout(code):
                break
        except Exception as e:
            print(f"[ERR][File] {label} attempt={attempt + 1} chat={bale_chat_id} error={e}")
            if bale_should_retry(exc=e) and attempt + 1 < n:
                time.sleep(bale_retry_sleep(attempt))
    return None


def send_fallback_preview(
    bale_chat_id, image_path, caption, caption_parse_mode=None, reply_to_message_id=None
):
    with open(image_path, "rb") as f:
        files = {"photo": ("preview.jpg", f)}
        data = {"chat_id": bale_chat_id, "caption": caption}
        if caption_parse_mode:
            data["parse_mode"] = caption_parse_mode
        if reply_to_message_id is not None:
            data["reply_to_message_id"] = str(int(reply_to_message_id))
        try:
            r = requests.post(
                f"{BALE_API}/sendPhoto",
                data=data,
                files=files,
                timeout=(20, 180),
                **_bale_request_kwargs(),
            )
            print(
                f"[Fallback Preview] chat={bale_chat_id} status={r.status_code} "
                f"body={_bale_error_body_snippet(r.text)!r}"
            )
            if r.status_code == 200:
                return _bale_response_message_id(r.status_code, r.text)
            return None
        except Exception as e:
            print(f"[ERR][Preview] chat={bale_chat_id} error={e}")
            return None


def send_media_to_bale(
    bale_chat_id,
    file_path,
    caption=None,
    read_timeout=None,
    caption_parse_mode=None,
    _did_image_reencode=False,
    reply_to_message_id=None,
    upload_max_attempts=None,
    read_timeout_cap=None,
):
    """
    Upload local file to Bale. Ordering is intentional:

    - Text uses JSON; media uses multipart. Failures are often Bale-side (HTTP 5xx) or size/timeouts,
      not "forum vs channel" on Telegram — we always download the same way.
    - Images: try sendDocument before sendPhoto when SEND_IMAGE_AS_DOCUMENT_FIRST=1 (often more stable).
    Returns Bale result.message_id on success, or None on failure.
    """
    _media_upload_fail_reset()
    read_timeout = read_timeout if read_timeout is not None else upload_timeout_seconds
    if read_timeout_cap is not None:
        read_timeout = min(read_timeout, read_timeout_cap)
    ext = os.path.splitext(file_path)[-1].lower()
    image_exts = (".jpg", ".jpeg", ".png", ".webp")
    video_exts = (".mp4", ".mov", ".mkv")
    voice_exts = (".ogg", ".opus", ".mp3", ".m4a", ".wav")

    try:
        mb = file_size_mb(file_path)
        print(
            f"[BaleUpload] start chat={bale_chat_id} file={file_path!r} ext={ext} size_mb={mb:.2f}",
            flush=True,
        )
    except OSError:
        print(f"[BaleUpload] start chat={bale_chat_id} file={file_path!r} ext={ext}", flush=True)

    if ext in image_exts:
        image_document_only = False
        if upload_image_document_only_mb > 0:
            try:
                image_document_only = file_size_mb(file_path) >= upload_image_document_only_mb
            except OSError:
                image_document_only = False
        if image_document_only:
            print(
                f"[BaleUpload] image >= {upload_image_document_only_mb:.1f}MB: "
                "skip sendPhoto, use sendDocument only",
                flush=True,
            )
            return _upload_with_caption_variants("sendDocument", "document", "image_as_document")
        strategies = (
            [("sendDocument", "document"), ("sendPhoto", "photo")]
            if send_image_as_document_first
            else [("sendPhoto", "photo"), ("sendDocument", "document")]
        )
        caption_passes = [caption]
        if bale_retry_media_without_caption and caption:
            caption_passes.append(None)
        for cap in caption_passes:
            if cap is None:
                cap_note = "no_caption"
            elif not str(cap).strip():
                cap_note = "empty_caption"
            else:
                cap_note = "caption"
            cap_mode = caption_parse_mode if cap else None
            for endpoint, field in strategies:
                label = f"image/{cap_note}/{field}"
                mid = _try_upload_endpoint(
                    bale_chat_id,
                    endpoint,
                    field,
                    file_path,
                    cap,
                    read_timeout,
                    label,
                    cap_mode,
                    reply_to_message_id,
                    upload_max_attempts,
                )
                if mid is not None:
                    return mid
        if (
            (not _did_image_reencode)
            and bale_reencode_image_on_failure
            and ext in image_exts
            and not media_reencode_on_fail
        ):
            dst = file_path + ".bale-reencode.jpg"
            if reencode_image_for_bale(file_path, dst):
                print("[BaleUpload] re-encoded image with ffmpeg; retrying upload", flush=True)
                try:
                    ok = send_media_to_bale(
                        bale_chat_id,
                        dst,
                        caption,
                        read_timeout,
                        caption_parse_mode,
                        True,
                        reply_to_message_id,
                        upload_max_attempts,
                        read_timeout_cap,
                    )
                finally:
                    safe_remove(dst)
                if ok is not None:
                    return ok
        return None

    def _upload_with_caption_variants(endpoint, field, label_prefix):
        passes = [caption]
        if bale_retry_media_without_caption and caption:
            passes.append(None)
        for cap in passes:
            cap_mode = caption_parse_mode if cap else None
            cap_label = "caption" if cap else "no_caption"
            mid = _try_upload_endpoint(
                bale_chat_id,
                endpoint,
                field,
                file_path,
                cap,
                read_timeout,
                f"{label_prefix}/{cap_label}",
                cap_mode,
                reply_to_message_id,
                upload_max_attempts,
            )
            if mid is not None:
                return mid
        return None

    if ext in video_exts:
        if video_try_document_first:
            mid = _upload_with_caption_variants("sendDocument", "document", "video_as_document")
            if mid is not None:
                return mid
        mid = _upload_with_caption_variants("sendVideo", "video", "video")
        if mid is not None:
            return mid
        if video_try_document_first:
            return None
        return _upload_with_caption_variants("sendDocument", "document", "video_as_document")

    if ext in voice_exts or is_audio_path(file_path):
        if audio_try_document_first:
            mid = _upload_with_caption_variants("sendDocument", "document", "voice_as_document")
            if mid is not None:
                return mid
            mid = _upload_with_caption_variants("sendVoice", "voice", "voice")
            return mid
        mid = _upload_with_caption_variants("sendVoice", "voice", "voice")
        if mid is not None:
            return mid
        return _upload_with_caption_variants("sendDocument", "document", "voice_as_document")

    return _upload_with_caption_variants("sendDocument", "document", "document")


async def forward_message_to_bale(message, bale_chat_id, source_key=None, include_caption=True):
    """
    Returns 'ok' if content was sent to Bale (or attempted as non-empty).
    Returns 'empty' when there is no text and no media (nothing to post).

    source_key: mapping key from SOURCE_TO_BALE_MAPPING (for per-source plain link append).
    include_caption: False for album siblings after the leader (avoid duplicate captions).
    """
    if bale_row_already_forwarded(bale_chat_id, int(message.chat_id), int(message.id)):
        print(
            f"[Skip][AlreadySent] chat={message.chat_id} msg={message.id} bale={bale_chat_id!r}",
            flush=True,
        )
        return "ok"
    if include_caption:
        pl = plain_link_append_for_source(source_key)
        caption, caption_parse_mode = message_to_bale_text(message, plain_link_append=pl)
    else:
        caption, caption_parse_mode = None, None
    msg_date = getattr(message, "date", None)
    msg_kind = "text"

    reply_tg = telegram_reply_target_message_id(message)
    reply_bale = None
    if reply_tg is not None:
        reply_bale = db_bale_map_get(bale_chat_id, int(message.chat_id), reply_tg)
        if reply_bale is not None:
            print(
                f"[Reply] tg_reply_to={reply_tg} tg_msg={message.id} -> bale_reply_to_message_id={reply_bale}",
                flush=True,
            )

    has_file = message_has_downloadable_file(message)
    has_text = message_has_text_body(message)

    if not has_text and not has_file:
        print(f"[Skip][Empty] chat={message.chat_id} msg={message.id} date={msg_date}")
        await asyncio.sleep(per_message_delay_seconds)
        return "empty"

    if not has_file:
        media_label = type(message.media).__name__ if message.media else "none"
        if message.media and not has_text:
            print(
                f"[Skip][NoFile] chat={message.chat_id} msg={message.id} media={media_label} "
                f"(not uploadable; no text body)",
                flush=True,
            )
            await asyncio.sleep(per_message_delay_seconds)
            return "empty"
        print(
            f"[Send] chat={message.chat_id} msg={message.id} kind=text "
            f"(media={media_label} ignored) date={msg_date}",
            flush=True,
        )
        bale_mid = await asyncio.wait_for(
            asyncio.to_thread(
                send_text_to_bale, bale_chat_id, caption, caption_parse_mode, reply_bale
            ),
            timeout=90,
        )
        if bale_mid is None:
            return "failed"
        db_bale_map_put(bale_chat_id, int(message.chat_id), int(message.id), bale_mid)
        await asyncio.sleep(per_message_delay_seconds)
        return "ok"

    msg_kind = "media"
    try:
        msg_kind, ext = resolve_media_download_kind_ext(message)
        if message.media and isinstance(message.media, MessageMediaWebPage):
            print(
                f"[WARN] msg={message.id} had MessageMediaWebPage but also downloadable file; "
                f"uploading as {msg_kind}",
                flush=True,
            )

        mb_hint = message_telegram_file_size_mb(message)
        bucket_hint = upload_media_bucket_from_message(msg_kind, ext)
        if upload_confidence_enabled and mb_hint is not None and bucket_hint:
            link_only_hint, hint_reason, _, _ = upload_tier_plan_from_bucket_mb(bucket_hint, mb_hint)
            if link_only_hint:
                print(
                    f"[BaleUpload] confidence (metadata mb={mb_hint:.2f}): skip Telegram download "
                    f"-> link ({hint_reason})",
                    flush=True,
                )
                bale_mid = await asyncio.wait_for(
                    asyncio.to_thread(
                        send_telegram_link_fallback,
                        bale_chat_id,
                        message,
                        caption,
                        caption_parse_mode,
                        source_key,
                        reply_bale,
                        bucket_hint,
                        mb_hint,
                        True,
                    ),
                    timeout=120,
                )
                if bale_mid is None:
                    return "failed"
                db_bale_map_put(bale_chat_id, int(message.chat_id), int(message.id), bale_mid)
                await asyncio.sleep(per_message_delay_seconds)
                return "ok"

        file_path = f"temp/{message.chat_id}_{message.id}{ext}"
        print(f"[Send] chat={message.chat_id} msg={message.id} kind={msg_kind} date={msg_date}")
        await telegram_call(
            f"download msg={message.id}",
            lambda: asyncio.wait_for(
                client.download_media(message, file_path),
                timeout=download_timeout_seconds,
            ),
        )
        if os.path.exists(file_path):
            read_to = effective_upload_read_timeout(file_path)
            upload_timeout = read_to + 600
            if audio_split_enable and _media_bucket(file_path) == "audio":
                try:
                    mb = file_size_mb(file_path)
                    n = audio_split_part_count(mb)
                    if n >= 2:
                        upload_timeout = read_to * n + 900
                except OSError:
                    pass
            try:
                bale_mid = await asyncio.wait_for(
                    asyncio.to_thread(
                        upload_media_to_bale_with_tiers,
                        message,
                        bale_chat_id,
                        file_path,
                        caption,
                        caption_parse_mode,
                        source_key,
                        reply_bale,
                    ),
                    timeout=upload_timeout,
                )
            finally:
                safe_remove(file_path)
            if bale_mid is None:
                return "failed"
            db_bale_map_put(bale_chat_id, int(message.chat_id), int(message.id), bale_mid)
            await asyncio.sleep(per_message_delay_seconds)
            return "ok"
        print(
            f"[ERR][Forward] downloaded file missing chat={message.chat_id} msg={message.id}",
            flush=True,
        )
        return "failed"
    except asyncio.TimeoutError:
        print(
            f"[ERR][Forward] timeout chat={message.chat_id} msg={message.id} kind={msg_kind} "
            f"download_timeout={download_timeout_seconds}s",
            flush=True,
        )
        return "failed"
    except Exception as e:
        print(
            f"[ERR][Forward] chat={message.chat_id} msg={message.id} error={e!r}",
            flush=True,
        )
        return "failed"


def classify_message_kind(message):
    if message_has_text_body(message) and not message_has_downloadable_file(message):
        return "text"
    if not message_has_downloadable_file(message):
        if message.media:
            return f"media:{type(message.media).__name__}"
        return "other"
    kind, _ext = resolve_media_download_kind_ext(message)
    return kind


async def run_crawl():
    db_init()
    if INCLUDE_SEND_TOPIC_IDS:
        limit_label = (
            f"{backfill_limit} matching topic_id in {sorted(INCLUDE_SEND_TOPIC_IDS)}"
            if backfill_limit > 0
            else f"ALL matching topic_id in {sorted(INCLUDE_SEND_TOPIC_IDS)}"
        )
    else:
        limit_label = "ALL" if backfill_limit <= 0 else str(backfill_limit)
    print(f"[Crawl] Starting. limit={limit_label} oldest_first=True")
    if INCLUDE_SEND_TOPIC_IDS:
        print(f"[Crawl] INCLUDE_SEND_TOPIC_IDS={sorted(INCLUDE_SEND_TOPIC_IDS)}")

    for source in SOURCE_CHATS:
        bale_chat_id = SOURCE_TO_BALE[source]
        resolved_source = await resolve_source_for_telethon(source)
        print(f"[Crawl] source={source} -> bale={bale_chat_id}")

        scanned = 0
        inserted = 0
        updated = 0
        queued = 0
        target = backfill_limit if backfill_limit > 0 else None
        with db_connect() as conn:
            async for msg in iter_messages_resilient(resolved_source, limit=None, reverse=True):
                scanned += 1
                topic_id = get_topic_id(msg)
                if exclude_send_null_topic and topic_id is None:
                    continue
                if topic_id is not None and int(topic_id) in EXCLUDE_SEND_TOPIC_IDS:
                    continue
                if INCLUDE_SEND_TOPIC_IDS:
                    if topic_id is None or int(topic_id) not in INCLUDE_SEND_TOPIC_IDS:
                        if target and scanned >= target * 200:
                            break
                        continue
                res = db_upsert_pending(
                    conn,
                    source,
                    bale_chat_id,
                    int(msg.chat_id),
                    int(msg.id),
                    topic_id,
                    msg.date.isoformat() if getattr(msg, "date", None) else None,
                    classify_message_kind(msg),
                )
                if res == "insert":
                    inserted += 1
                else:
                    updated += 1
                queued += 1

                if queued % 500 == 0:
                    conn.commit()
                    print(
                        f"[Crawl] source={source} scanned={scanned} queued={queued} "
                        f"inserted={inserted} updated={updated}"
                    )
                if target and queued >= target:
                    break

            conn.commit()

        print(
            f"[Crawl] Done source={source}. scanned={scanned} queued={queued} "
            f"inserted={inserted} updated={updated}"
        )


def _resolve_row_bale_chat(row):
    """
    Bale destination for a queue row, or None if the row should be skipped.
    Returns (bale_chat_id, skip_reason_or_None).
    """
    source = row["source"]
    bale_chat_id = row["bale_chat_id"]
    topic_id = row["topic_id"]
    tg_chat_id = int(row["tg_chat_id"])
    tg_message_id = int(row["tg_message_id"])

    row_bale = bale_chat_id
    if force_bale_chat:
        bale_chat_id = force_bale_chat
        if str(row_bale) != str(force_bale_chat):
            print(
                f"[SendQueue] FORCE_BALE_CHAT: queue row mapped to {row_bale!r} "
                f"-> sending to {force_bale_chat!r} (source={source!r} tg_msg={tg_message_id})",
                flush=True,
            )
    elif source in STRICT_TOPIC_ROUTING_SOURCES:
        mapped_bale = TOPIC_TO_BALE.get(int(topic_id)) if topic_id is not None else None
        if not mapped_bale:
            db_mark_skipped(
                tg_chat_id,
                tg_message_id,
                f"Skipped: no Bale mapping for topic_id={topic_id}",
            )
            return None, "topic_unmapped"
        bale_chat_id = mapped_bale

    if exclude_send_null_topic and topic_id is None:
        db_mark_skipped(
            tg_chat_id,
            tg_message_id,
            "Skipped: null topic_id (EXCLUDE_SEND_NULL_TOPIC)",
        )
        return None, "null_topic"
    if topic_id is not None and int(topic_id) in EXCLUDE_SEND_TOPIC_IDS:
        db_mark_skipped(
            tg_chat_id,
            tg_message_id,
            f"Skipped: excluded topic_id={topic_id} (EXCLUDE_SEND_TOPIC_IDS)",
        )
        return None, "excluded_topic"
    return bale_chat_id, None


async def process_queue_row(row, resolved_by_source):
    """
    Send one queue row (album-aware). Returns True when done (sent/skipped).
    Returns False on failure under strict order (caller must not advance to later ids).
    """
    source = row["source"]
    topic_id = row["topic_id"]
    tg_chat_id = int(row["tg_chat_id"])
    tg_message_id = int(row["tg_message_id"])

    bale_chat_id, skip_reason = _resolve_row_bale_chat(row)
    if skip_reason:
        return True

    resolved_source = resolved_by_source.get(source) or await resolve_source_for_telethon(source)

    try:
        st = db_get_queue_status(tg_chat_id, tg_message_id)
        if st not in ("pending", "failed"):
            return True

        msg = await telegram_call(
            f"get_messages msg={tg_message_id}",
            lambda: client.get_messages(resolved_source, ids=tg_message_id),
        )
        if not msg:
            _note_send_failure(False)
            db_mark_failed(tg_chat_id, tg_message_id, "Message not found (maybe deleted)")
            return False

        album = await collect_album_messages(resolved_source, msg)
        if len(album) > 1:
            leader_id = album[0].id
            if msg.id != leader_id:
                db_mark_skipped(
                    tg_chat_id,
                    tg_message_id,
                    f"Album sibling of grouped_id={getattr(msg, 'grouped_id', None)}; "
                    f"leader tg_message_id={leader_id}",
                )
                return True
            print(
                f"[Album] grouped_id={getattr(msg, 'grouped_id', None)} "
                f"parts={len(album)} ids={[p.id for p in album]} leader={leader_id}"
            )

        per_part_timeout = download_timeout_seconds + max_upload_timeout_seconds + 900
        album_timeout = per_part_timeout * max(1, len(album)) + 300

        outcomes = await asyncio.wait_for(
            forward_album_parts_to_bale(album, bale_chat_id, source),
            timeout=album_timeout,
        )
        for part, outcome in zip(album, outcomes):
            if outcome == "empty":
                db_mark_skipped(
                    tg_chat_id,
                    part.id,
                    "No text or media (empty / service without payload)",
                )
            elif _forward_outcome_success(outcome):
                db_mark_sent(tg_chat_id, part.id)
            else:
                _note_send_failure(True)
                return False
        _reset_network_hold_counter()
        return True
    except asyncio.TimeoutError:
        _note_send_failure(True)
        print(
            f"[SendQueue] queue watchdog timeout msg={tg_message_id}; will retry when network allows",
            flush=True,
        )
        return False
    except Exception as e:
        if _is_telegram_transient(e):
            _note_send_failure(True)
            print(
                f"[Telegram] queue row msg={tg_message_id} transient: {e!r}; will retry",
                flush=True,
            )
            try:
                if _is_sqlite_busy(e):
                    await _sleep_session_busy(f"queue msg={tg_message_id}")
                elif _should_reconnect_telegram(e):
                    await recover_telegram_guarded(repr(e))
            except Exception:
                pass
            return False
        _note_send_failure(False)
        db_mark_failed(tg_chat_id, tg_message_id, repr(e))
        return False


async def _strict_failure_wait_or_stop(tg_chat_id, tg_message_id, source, reason):
    """On strict-order failure: retry same message after delay, or stop if retries exhausted."""
    if not strict_send_order:
        return "continue"
    retries = db_get_queue_retries(tg_chat_id, tg_message_id)
    unlimited_network = send_network_retry_unlimited and _last_send_failure_is_network
    if not unlimited_network and max_retries > 0 and retries > max_retries:
        print(
            f"[SendQueue] STRICT_STOP permanent: tg_message_id={tg_message_id} source={source!r} "
            f"retries={retries} > MAX_RETRIES={max_retries}. Fix this message, then restart. "
            f"Later messages will not send until this is resolved.",
            flush=True,
        )
        return "stop"
    if send_auto_retry_on_failure:
        if unlimited_network:
            delay = _network_retry_delay_seconds()
            print(
                f"[SendQueue] NETWORK_HOLD at tg_message_id={tg_message_id} ({reason}). "
                f"Waiting {delay:.0f}s for connectivity (unlimited retries)…",
                flush=True,
            )
        else:
            delay = send_retry_delay_seconds
            max_label = "∞" if max_retries <= 0 else str(max_retries)
            print(
                f"[SendQueue] STRICT_HOLD at tg_message_id={tg_message_id} ({reason}). "
                f"Not sending later messages. Retry {retries}/{max_label} in {delay:.0f}s…",
                flush=True,
            )
        await asyncio.sleep(delay)
        return "retry"
    print(
        f"[SendQueue] STRICT_STOP at tg_message_id={tg_message_id} ({reason}). "
        f"SEND_AUTO_RETRY_ON_FAILURE=0 — restart send after fixing.",
        flush=True,
    )
    return "stop"


_send_startup_logged = False


async def run_send():
    global _send_startup_logged
    db_init()
    if reset_sent_on_send_start:
        n = db_reset_sent_failed_for_replay()
        print(
            f"[SendQueue] RESET_SENT_ON_SEND_START: {n} row(s) reset sent/failed → pending (replay from first)."
        )
        db_bale_map_clear_all()
        print("[SendQueue] Cleared Telegram→Bale message id map (reply threading restarts clean).")
    if not _send_startup_logged:
        print(f"[SendQueue] Starting. batch={send_batch_size} max_retries={max_retries}")
        if send_topic_by_topic:
            print(
                "[SendQueue] Order: per source, topic-by-topic, then oldest→newest within each topic."
            )
        else:
            print("[SendQueue] Order: per source, global oldest→newest.")
        if strict_send_order:
            print(
                "[SendQueue] STRICT_SEND_ORDER=1: never send message X+1 until X succeeds."
            )
            if send_auto_retry_on_failure:
                mr = "∞" if max_retries <= 0 else str(max_retries)
                print(
                    f"[SendQueue] SEND_AUTO_RETRY_ON_FAILURE=1: wait and retry the same message "
                    f"(MAX_RETRIES={mr})."
                )
                if send_network_retry_unlimited:
                    print(
                        f"[SendQueue] SEND_NETWORK_RETRY_UNLIMITED=1: network/Bale outages use "
                        f"backoff {network_retry_base_seconds:.0f}s–{network_retry_max_seconds:.0f}s "
                        f"and never stop for connectivity loss."
                    )
        if force_bale_chat:
            print(f"[SendQueue] FORCE_BALE_CHAT={force_bale_chat!r} — all sends go here.")
        if EXCLUDE_SEND_TOPIC_IDS:
            print(f"[SendQueue] EXCLUDE_SEND_TOPIC_IDS={sorted(EXCLUDE_SEND_TOPIC_IDS)}")
        if INCLUDE_SEND_TOPIC_IDS:
            print(f"[SendQueue] INCLUDE_SEND_TOPIC_IDS={sorted(INCLUDE_SEND_TOPIC_IDS)}")
        if exclude_send_null_topic:
            print("[SendQueue] EXCLUDE_SEND_NULL_TOPIC=1")
        print(f"[SendQueue] Sources: {SOURCE_CHATS}")
        if media_reencode_on_fail or bale_fallback_telegram_link:
            sf = (
                f" small-first audio>={compress_before_upload_mb_audio}MB "
                f"video>={compress_before_upload_mb_video}MB image>={compress_before_upload_mb_image}MB"
                if compress_small_first
                else ""
            )
            print(
                "[SendQueue] Media pipeline (Iran/Bale-sized):"
                + (sf if sf else " upload")
                + f" -> opus {audio_opus_bitrate}"
                f" video {video_reencode_max_height}p crf{video_reencode_crf}"
                f" jpeg q{image_jpeg_q} max{image_reencode_max_edge}px"
                + (" -> compress retry" if media_reencode_on_fail else "")
                + (" -> Telegram t.me link" if bale_fallback_telegram_link else "")
            )
            print(
                f"[SendQueue] Telegram link fallback only for buckets: "
                f"{sorted(BALE_FALLBACK_TELEGRAM_LINK_BUCKETS)} "
                f"(text/image/voice retry upload; no t.me link)."
            )
        _send_startup_logged = True

    resolved_by_source = {}
    for source in SOURCE_CHATS:
        resolved_by_source[source] = await resolve_source_for_telethon(source)

    while True:
        try:
            batch = db_fetch_batch(["pending", "failed"])
            if not batch:
                print("[SendQueue] Nothing left to send.")
                return

            blocked = False
            for row in batch:
                source = row["source"]
                tg_chat_id = int(row["tg_chat_id"])
                tg_message_id = int(row["tg_message_id"])

                ok = await process_queue_row(row, resolved_by_source)
                if ok:
                    continue
                if not strict_send_order:
                    continue

                blocked = True
                action = await _strict_failure_wait_or_stop(
                    tg_chat_id, tg_message_id, source, "send failed"
                )
                if action == "retry":
                    break
                return

            if not blocked:
                continue
        except Exception as e:
            if not _is_network_error(e) and not _is_telegram_transient(e):
                raise
            _note_send_failure(True)
            if _is_sqlite_busy(e):
                await _sleep_session_busy("send loop")
                continue
            print(f"[SendQueue] Network error in send loop: {e!r}", flush=True)
            delay = _network_retry_delay_seconds()
            print(f"[SendQueue] Backing off {delay:.0f}s before resuming send loop…", flush=True)
            await asyncio.sleep(delay)
            if _should_reconnect_telegram(e):
                try:
                    await recover_telegram_guarded(repr(e))
                except Exception as rec_err:
                    print(f"[SendQueue] Recover skipped/failed: {rec_err!r}", flush=True)


async def resolve_event_mapping_source(event):
    """Map incoming Telegram chat to SOURCE_TO_BALE_MAPPING key, or None."""
    source = event.chat.username
    source_key = f"@{source}" if source else str(event.chat_id)
    if source_key in SOURCE_TO_BALE:
        return source_key
    if str(event.chat_id) in SOURCE_TO_BALE:
        return str(event.chat_id)
    for original_source in SOURCE_TO_BALE:
        try:
            entity = await resolve_source_for_telethon(original_source)
            if hasattr(entity, "id") and getattr(entity, "id", None) == event.chat_id:
                return original_source
        except Exception:
            continue
    return None


async def live_queue_handler(event):
    """Enqueue new Telegram message and drain queue (same rules as MODE=send)."""
    try:
        mapping_source = await resolve_event_mapping_source(event)
        if not mapping_source:
            print(f"[Live] No mapping for chat id={event.chat_id}", flush=True)
            return
        queue_enqueue_from_message(mapping_source, event.message)
        print(
            f"[Live] Queued tg_msg={event.message.id} source={mapping_source!r}",
            flush=True,
        )
        await trigger_send_queue()
    except Exception as e:
        print(f"[ERR][Live] tg_msg={getattr(event.message, 'id', '?')} error={e!r}", flush=True)


async def _daemon_network_recover(phase, exc):
    """Backoff and reconnect after crawl/send/live network errors (unattended daemon)."""
    if not _is_network_error(exc) and not _is_telegram_transient(exc):
        raise exc
    if _is_sqlite_busy(exc):
        await _sleep_session_busy(phase)
        return
    delay = _network_retry_delay_seconds()
    print(f"[Daemon] {phase}: network {exc!r}; waiting {delay:.0f}s…", flush=True)
    await asyncio.sleep(delay)
    if _should_reconnect_telegram(exc):
        try:
            await recover_telegram_guarded(f"{phase}: {exc!r}")
        except Exception as rec_err:
            print(f"[Daemon] {phase} recover failed: {rec_err!r}", flush=True)


async def run_daemon():
    """
    Unattended: optional full crawl, send queue with strict order + auto-retry,
    then listen for new messages (queue + same send path).
    """
    global _daemon_crawl_done
    db_init()
    if reset_sent_on_send_start:
        n = db_reset_sent_failed_for_replay()
        print(f"[Daemon] RESET_SENT_ON_SEND_START: {n} row(s) → pending.")
        db_bale_map_clear_all()

    if daemon_initial_crawl and not _daemon_crawl_done:
        qn = db_queue_row_count()
        if daemon_skip_crawl_if_queued and qn > 0:
            print(f"[Daemon] Skip initial crawl — queue already has {qn} row(s).", flush=True)
            _daemon_crawl_done = True
        else:
            if qn > 0:
                print(
                    f"[Daemon] Initial crawl enabled with existing queue rows={qn}; "
                    "rescanning Telegram and keeping already-sent rows skipped.",
                    flush=True,
                )
            while True:
                try:
                    await run_crawl()
                    _daemon_crawl_done = True
                    break
                except Exception as e:
                    await _daemon_network_recover("crawl", e)

    while True:
        try:
            await run_send()
            break
        except Exception as e:
            await _daemon_network_recover("send", e)

    while True:
        try:
            await run_live_watch()
            break
        except Exception as e:
            await _daemon_network_recover("live", e)


async def run_live_watch():
    """Listen for new Telegram messages; enqueue and send with queue rules."""
    resolved = []
    for source in SOURCE_CHATS:
        resolved.append(await resolve_source_for_telethon(source))

    @client.on(events.NewMessage(chats=resolved))
    async def live_handler(event):
        await live_queue_handler(event)

    print("[Live] Listening for new Telegram messages…", flush=True)
    await client.run_until_disconnected()


async def run_discover_topics():
    limit_label = "ALL" if backfill_limit <= 0 else str(backfill_limit)
    print(f"[Topics] Discovering topics. limit={limit_label} oldest_first=True")
    topic_stats = {}
    topic_titles = {}

    for source in SOURCE_CHATS:
        resolved_source = await resolve_source_for_telethon(source)
        print(f"[Topics] source={source}")
        iter_limit = None if backfill_limit <= 0 else backfill_limit
        async for msg in iter_messages_resilient(resolved_source, limit=iter_limit, reverse=True):
            topic_id = get_topic_id(msg)
            key = topic_id if topic_id is not None else 0
            topic_stats[key] = topic_stats.get(key, 0) + 1

            action = getattr(msg, "action", None)
            if action and action.__class__.__name__ == "MessageActionTopicCreate":
                title = getattr(action, "title", None)
                if title and topic_id:
                    topic_titles[topic_id] = title

        for topic_id in sorted(topic_stats.keys()):
            if topic_id == 0:
                print(f"[Topics] topic_id=0 (general/no-topic) count={topic_stats[topic_id]}")
            else:
                title = topic_titles.get(topic_id, "")
                suffix = f" title={title!r}" if title else ""
                print(f"[Topics] topic_id={topic_id} count={topic_stats[topic_id]}{suffix}")


async def main():
    # Provide phone explicitly so first-run can work non-interactively.
    # If the session is already authorized, Telethon will not prompt.
    await client.start(phone=telegram_phone)
    if getattr(client.session, "save_entities", None) is not None:
        client.session.save_entities = False
    resolved = []
    for source in SOURCE_CHATS:
        resolved.append(await resolve_source_for_telethon(source))
    print(f"[Init] mode={mode} sources={SOURCE_CHATS}")
    if SOURCE_PLAIN_LINK_APPEND:
        print(f"[Init] per-source BALE_PLAIN_LINK_APPEND overrides: {SOURCE_PLAIN_LINK_APPEND!r}")

    if mode == "crawl":
        await run_crawl()
        return

    if mode in ("crawl_then_send", "sync"):
        await run_crawl()
        await run_send()
        return

    if mode == "daemon":
        await run_daemon()
        return

    if mode == "discover_topics":
        await run_discover_topics()
        return

    if mode == "send":
        await run_send()
        return

    if mode == "live":
        await run_live_watch()
        return

    print(
        f"[ERR] Unknown MODE={mode!r}. Use: daemon | crawl | send | crawl_then_send | "
        f"discover_topics | live",
        flush=True,
    )


if __name__ == "__main__":
    try:
        if mode == "daemon" and daemon_supervisor:
            while True:
                try:
                    client.loop.run_until_complete(main())
                    break
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    if not _is_network_error(e) and not _is_telegram_transient(e):
                        raise
                    delay = min(
                        network_retry_max_seconds,
                        network_retry_base_seconds * (2 ** min(_send_network_hold_count, 6)),
                    )
                    _send_network_hold_count += 1
                    print(
                        f"[Daemon] Supervisor caught network error {e!r}; restarting in {delay:.0f}s…",
                        flush=True,
                    )
                    try:
                        client.loop.run_until_complete(_telegram_disconnect_silent())
                    except Exception:
                        pass
                    time.sleep(delay)
        else:
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n[Exit] Stopped by user.", flush=True)
    except Exception as e:
        print(f"[FATAL] {e!r}", flush=True)
        sys.exit(1)
