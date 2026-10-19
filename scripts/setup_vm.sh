#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 22.04/24.04 VM.
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl git ca-certificates

# Node.js 20 LTS (the SailPoint connector SDK needs Node >= 18.12)
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

( cd connector && npm install && npm run build )
echo "Setup complete. Next: ./scripts/start.sh"
