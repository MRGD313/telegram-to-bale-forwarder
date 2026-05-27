# Security

- **Never commit** `.env`, `.env.private`, `.env.public`, `session*`, or `*.db` files.
- Rotate **Telegram API hash** and **Bale bot token** if they were ever pushed to a public repo.
- The bot only needs access to Telegram sources you join and Bale channels where it is admin.
- Run **one** forwarder process at a time; two `main.py` instances share `session` and cause **wrong session ID** / security errors.
