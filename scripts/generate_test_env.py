"""Build .env.test.public and .env.test.private for E2E (credentials from your local profile files)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMPRESS_KEYS = (
    "FFMPEG_PATH",
    "COMPRESS_SMALL_FIRST",
    "COMPRESS_BEFORE_UPLOAD_MB_AUDIO",
    "COMPRESS_BEFORE_UPLOAD_MB_VIDEO",
    "COMPRESS_BEFORE_UPLOAD_MB_IMAGE",
    "COMPRESS_TARGET_MAX_MB",
    "AUDIO_OPUS_BITRATE",
    "AUDIO_OPUS_BITRATE_FLOOR",
    "VIDEO_REENCODE_MAX_HEIGHT",
    "VIDEO_REENCODE_CRF",
    "IMAGE_REENCODE_MAX_EDGE",
    "IMAGE_JPEG_Q",
)

CREDENTIAL_KEYS = (
    "API_ID",
    "API_HASH",
    "BALE_BOT_TOKEN",
    "TELEGRAM_PHONE",
    "SOURCE_TO_BALE_MAPPING",
    "TOPIC_TO_BALE_MAPPING",
    "STRICT_TOPIC_ROUTING_SOURCES",
)


def bundled_ffmpeg_path():
    tools = os.path.join(ROOT, "tools")
    if os.path.isdir(tools):
        for root, _dirs, files in os.walk(tools):
            if "ffmpeg.exe" in files:
                return os.path.join(root, "ffmpeg.exe")
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return ""


def compress_settings(base_env):
    out = {k: base_env[k] for k in COMPRESS_KEYS if k in base_env}
    ff = bundled_ffmpeg_path()
    if ff:
        out["FFMPEG_PATH"] = ff
    return out


def load_env(path):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def first_existing(*paths):
    for p in paths:
        if os.path.isfile(p):
            return p
    return None


def merge_credentials(target, *sources):
    for src in sources:
        for k in CREDENTIAL_KEYS:
            if k in src and src[k] and k not in target:
                target[k] = src[k]


def write_env(path, data, header):
    lines = [f"# {header}", ""]
    for k, v in sorted(data.items(), key=lambda x: (x[0] not in (
        "API_ID", "API_HASH", "BALE_BOT_TOKEN", "TELEGRAM_PHONE",
        "SOURCE_TO_BALE_MAPPING", "TOPIC_TO_BALE_MAPPING", "STRICT_TOPIC_ROUTING_SOURCES",
        "DB_PATH", "MODE", "BACKFILL_LIMIT",
    ), x[0])):
        lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    pub_paths = [
        first_existing(os.path.join(ROOT, ".env.public"), os.path.join(ROOT, ".env.public.example")),
        os.path.join(ROOT, ".env.example"),
    ]
    priv_paths = [
        first_existing(os.path.join(ROOT, ".env.private"), os.path.join(ROOT, ".env.private.example")),
        os.path.join(ROOT, ".env.example"),
    ]
    pub_base = {}
    priv_base = {}
    for p in pub_paths:
        if p:
            merge_credentials(pub_base, load_env(p))
    for p in priv_paths:
        if p:
            merge_credentials(priv_base, load_env(p))

    if not pub_base.get("API_ID") or not pub_base.get("BALE_BOT_TOKEN"):
        raise SystemExit(
            "Missing credentials. Copy .env.public.example to .env.public and fill API_ID, "
            "API_HASH, BALE_BOT_TOKEN, TELEGRAM_PHONE, SOURCE_TO_BALE_MAPPING."
        )

    pub = {k: pub_base[k] for k in CREDENTIAL_KEYS if k in pub_base and pub_base[k]}
    pub.update({
        "TOPIC_TO_BALE_MAPPING": "",
        "STRICT_TOPIC_ROUTING_SOURCES": "",
        "FORCE_BALE_CHAT": "",
        "EXCLUDE_SEND_TOPIC_IDS": "",
        "EXCLUDE_SEND_NULL_TOPIC": "0",
        "INCLUDE_SEND_TOPIC_IDS": "",
        "DB_PATH": "state_test_public.db",
        "MODE": "crawl_then_send",
        "BACKFILL_LIMIT": "8",
        "DAEMON_INITIAL_CRAWL": "0",
        "RESET_SENT_ON_SEND_START": "1",
        "STRICT_SEND_ORDER": "1",
        "SEND_TOPIC_BY_TOPIC": "0",
        "SEND_AUTO_RETRY_ON_FAILURE": "1",
        "SEND_RETRY_DELAY_SECONDS": "30",
        "SEND_BATCH_SIZE": "20",
        "MAX_RETRIES": "3",
        "PER_MESSAGE_DELAY_SECONDS": "0.5",
        "DOWNLOAD_TIMEOUT_SECONDS": "600",
        "UPLOAD_TIMEOUT_SECONDS": "300",
        "UPLOAD_SEC_PER_MB": "180",
        "MAX_UPLOAD_TIMEOUT_SECONDS": "7200",
        "AUDIO_REENCODE_ENABLE": "1",
        "AUDIO_REENCODE_MIN_MB": "10",
        "MEDIA_REENCODE_ON_FAIL": "1",
        "BALE_FALLBACK_TELEGRAM_LINK": "1",
        "BALE_PHOTO_ALBUM_MEDIA_GROUP": "1",
        "BALE_DOCUMENT_ALBUM_MEDIA_GROUP": "1",
        "BALE_TEXT_FORMAT": "plain_links",
    })
    pub.update(compress_settings(pub_base))

    priv = {k: priv_base[k] for k in CREDENTIAL_KEYS if k in priv_base and priv_base[k]}
    priv.update({
        "FORCE_BALE_CHAT": "",
        "EXCLUDE_SEND_TOPIC_IDS": "1",
        "EXCLUDE_SEND_NULL_TOPIC": "0",
        "INCLUDE_SEND_TOPIC_IDS": "4",
        "DB_PATH": "state_test_private.db",
        "MODE": "crawl_then_send",
        "BACKFILL_LIMIT": "12",
        "DAEMON_INITIAL_CRAWL": "0",
        "RESET_SENT_ON_SEND_START": "1",
        "STRICT_SEND_ORDER": "1",
        "SEND_TOPIC_BY_TOPIC": "1",
        "SEND_AUTO_RETRY_ON_FAILURE": "1",
        "SEND_RETRY_DELAY_SECONDS": "30",
        "SEND_BATCH_SIZE": "20",
        "MAX_RETRIES": "3",
        "PER_MESSAGE_DELAY_SECONDS": "0.5",
        "DOWNLOAD_TIMEOUT_SECONDS": "600",
        "UPLOAD_TIMEOUT_SECONDS": "300",
        "UPLOAD_SEC_PER_MB": "180",
        "MAX_UPLOAD_TIMEOUT_SECONDS": "7200",
        "AUDIO_REENCODE_ENABLE": "1",
        "MEDIA_REENCODE_ON_FAIL": "1",
        "BALE_FALLBACK_TELEGRAM_LINK": "1",
        "BALE_PHOTO_ALBUM_MEDIA_GROUP": "1",
        "BALE_DOCUMENT_ALBUM_MEDIA_GROUP": "1",
        "BALE_TEXT_FORMAT": "plain_links",
    })
    priv.update(compress_settings({**priv_base, **pub_base}))

    write_env(os.path.join(ROOT, ".env.test.public"), pub, "E2E: public channel (generated, gitignored)")
    write_env(os.path.join(ROOT, ".env.test.private"), priv, "E2E: private forum subset (generated, gitignored)")
    print("Wrote .env.test.public and .env.test.private (gitignored)")


if __name__ == "__main__":
    main()
