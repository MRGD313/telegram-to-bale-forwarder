print("Telegram-to-Bale setup\n")

api_id = input("Telegram API ID: ").strip()
api_hash = input("Telegram API Hash: ").strip()
phone = input("Telegram phone (+989...): ").strip()
bale_token = input("Bale Bot Token: ").strip()
source_to_bale_mapping = input(
    "Mapping (source->bale; …)\n"
    "Example: @channel->@bale_channel or https://t.me/+invite->@bale\n> "
).strip()
db_path = input("DB_PATH [state.db]: ").strip() or "state.db"
delay = input("Delay between sends seconds [1.0]: ").strip() or "1.0"

with open(".env", "w") as f:
    f.write(f"API_ID={api_id}\n")
    f.write(f"API_HASH={api_hash}\n")
    f.write(f"TELEGRAM_PHONE={phone}\n")
    f.write(f"BALE_BOT_TOKEN={bale_token}\n")
    f.write(f"SOURCE_TO_BALE_MAPPING={source_to_bale_mapping}\n")
    f.write(f"DB_PATH={db_path}\n")
    f.write(f"MODE=daemon\n")
    f.write(f"BACKFILL_LIMIT=0\n")
    f.write(f"DAEMON_INITIAL_CRAWL=1\n")
    f.write(f"RESET_SENT_ON_SEND_START=0\n")
    f.write(f"STRICT_SEND_ORDER=1\n")
    f.write(f"SEND_AUTO_RETRY_ON_FAILURE=1\n")
    f.write(f"PER_MESSAGE_DELAY_SECONDS={delay}\n")

print("\n.env created. Run: py main.py  (or .\\run_public.ps1 / .\\run_private.ps1)")
