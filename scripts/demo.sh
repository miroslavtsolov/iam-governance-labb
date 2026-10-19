#!/usr/bin/env bash
# Guided demo of the full lab.
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
AUTO=${1:-}
pause(){ [ "$AUTO" = "--auto" ] || read -rp $'\n[Enter to continue] ' _; }
step(){ echo; echo "################ $*"; }

step "0. Reset lab to a known state"
./scripts/start.sh --reset; pause

step "1. The connector, as ISC would call it (std:account:list)"
curl -s -X POST localhost:3000 -H 'Content-Type: application/json' \
  -d '{"type":"std:account:list","input":{},"config":{"baseUrl":"http://localhost:8001"}}' | head -3 | cut -c1-220
pause

step "2. Baseline aggregation + correlation + provisioning from HR snapshot 1"
python engine/govlab.py run --hr data/hr_snapshot_1.csv --apply; pause

step "3. HR changes: mover (Boris), leaver (Daniel), joiner (Lilia)"
python engine/govlab.py run --hr data/hr_snapshot_2.csv --apply; pause

step "4. Access certification: managers review, revocations are enforced via the connector"
python engine/govlab.py certify-start
python engine/govlab.py certify-complete; pause

step "5. Re-run: nothing left to fix. Then the audit report"
python engine/govlab.py run --hr data/hr_snapshot_2.csv | sed -n '/Findings/,$p'
python engine/govlab.py report
echo "Open engine/out/report.html in a browser (python3 -m http.server -d engine/out 8080)"
