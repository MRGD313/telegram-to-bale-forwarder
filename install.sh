#!/bin/bash
# DEPRECATED: use README.md (clone repo, pip install -r requirements.txt, copy .env.*.example).
echo "This installer is deprecated. See README.md for current setup."
exit 1

echo "🚀 Cloning Telegram-to-Bale repository..."
git clone https://github.com/ach1992/telegram-to-bale.git

cd telegram-to-bale || {
  echo "❌ Failed to enter project directory."
  exit 1
}

echo "📦 Running inner installer..."
bash setup.sh
