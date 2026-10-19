#!/usr/bin/env bash
# Starts FinApp (target system, :8001) and the SailPoint connector dev server (spcx, :3000).
# Usage: ./scripts/start.sh [--reset]   (--reset wipes FinApp data and the engine's state)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run
./scripts/stop.sh >/dev/null 2>&1 || true
. .venv/bin/activate

if [ "${1:-}" = "--reset" ]; then
  rm -rf engine/out app/finapp.db
  export FINAPP_RESET=1
fi

# build the connector first (synchronously), then start both servers in their own process groups
( cd connector && npm run build >/dev/null )

( cd app; setsid nohup uvicorn finapp:app --port 8001 > ../.run/finapp.log 2>&1 < /dev/null & echo $! > ../.run/finapp.pid )
( cd connector; setsid nohup npx spcx dist/index.js 3000 > ../.run/spcx.log 2>&1 < /dev/null & echo $! > ../.run/spcx.pid )

for _ in $(seq 1 30); do curl -sf localhost:8001/health >/dev/null && break; sleep 1; done
for _ in $(seq 1 30); do
  curl -s -X POST localhost:3000 -H 'Content-Type: application/json' \
    -d '{"type":"std:test-connection","input":{},"config":{"baseUrl":"http://localhost:8001"}}' | grep -q output && break
  sleep 1
done
echo "FinApp:    http://localhost:8001/docs"
echo "Connector: http://localhost:3000  (POST commands, see README)"
echo "std:test-connection -> OK"
