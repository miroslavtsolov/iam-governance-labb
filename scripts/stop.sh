#!/usr/bin/env bash
# Stops both servers (each runs in its own process group, so children such as tsc --watch die too).
cd "$(dirname "$0")/.."
for n in finapp spcx; do
  if [ -f ".run/$n.pid" ]; then
    pid=$(cat ".run/$n.pid")
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    rm -f ".run/$n.pid"
  fi
done
echo "stopped"
