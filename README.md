## IAM Governance Lab

A small lab that demonstrates identity-governance concepts using SailPoint's open-source
Connector SDK ([sp-connector-sdk-js](https://github.com/sailpoint-oss/sp-connector-sdk-js),
installed from npm as `@sailpoint/connector-sdk` and run locally with `spcx`),
plus my own governance engine.

## About this project
   An independent learning project, not affiliated with or endorsed by SailPoint. It uses SailPoint's
   public Connector SDK. The connector, FinApp and the governance engine are my own work, built with
   AI assistance (Claude), then tested and adapted by me.

## Limitations
   - Simulates governance concepts; it is not SailPoint ISC or IdentityIQ and not production-ready.
   - Reviewer decisions are simulated; orphan deletion only demonstrates enforcement.
   - The connector spec is not validated against a real tenant.
   - Lab only: no authentication on FinApp or the connector.

## Architecture

```
HR CSV snapshots (authoritative source)
      │
      ▼
┌────────────────────────────────────────────────────┐
│ govlab.py  (governance engine)                     │
│ correlation · RBAC · JML · SoD ·                   │
│ certification · audit                              │
└─────┬──────────────────────────────────────────────┘
      │  std:account:list / read / create / update /
      │  disable / delete, std:entitlement:list
      ▼
┌────────────────────────────────────────────────────┐
│ finapp-connector (TypeScript)                      │
│ SailPoint SDK, run via spcx                        │
└─────┬──────────────────────────────────────────────┘
      │  REST
      ▼
┌────────────────────────────────────────────────────┐
│ FinApp (FastAPI + SQLite)                          │
│ the "SaaS app" being governed                      │
└────────────────────────────────────────────────────┘

govlab.py also writes: engine/out/report.html + audit.jsonl
```

## One-time setup (fresh Ubuntu 22.04/24.04 VM, 2 vCPU / 4 GB RAM is enough)

```bash
git clone https://github.com/miroslavtsolov/iam-governance-labb && cd iam-governance-labb
./scripts/setup_vm.sh                                 # python venv, Node 20, npm install, build connector
```

## Run the lab / Usage

```bash
./scripts/start.sh --reset      # FinApp on :8001, connector dev server (spcx) on :3000
. .venv/bin/activate

./scripts/demo.sh

python engine/govlab.py run --hr data/hr_snapshot_1.csv            # dry run: plan + findings
python engine/govlab.py run --hr data/hr_snapshot_1.csv --apply    # baseline provisioning
python engine/govlab.py run --hr data/hr_snapshot_2.csv --apply    # mover / leaver / joiner
python engine/govlab.py certify-start                              # create access review campaign
python engine/govlab.py certify-complete                           # reviewers decide, revocations enforced
python engine/govlab.py report                                     # engine/out/report.html
python3 -m http.server -d engine/out 8080                          # view the report in a browser

./scripts/stop.sh
```

### Talk to the connector directly

```bash
curl -s -X POST localhost:3000 -H 'Content-Type: application/json' -d '{
  "type": "std:account:read",
  "input": {"identity": "anna.petrova"},
  "config": {"baseUrl": "http://localhost:8001"}}'
```

Implemented commands: `std:test-connection`, `std:account:list|read|create|update|delete|enable|disable`,
`std:entitlement:list`. FinApp's Swagger UI is at `http://localhost:8001/docs`.

## What the data is designed to show

| Scenario | Where | What happens |
|---|---|---|
| Correlation by `employee_id` and by **email fallback** | Boris has no employee_id on his account | Matched on rule 2 |
| **Orphan accounts** | `svc.legacy`, `tmp.contractor` | Flagged, reviewed by IAM team, removed |
| **Birthright / role-based access** | `config/roles.yaml` | New accounts created with role entitlements |
| **SoD violation** | Anna holds `vendor_create` + `payment_approve` | Detected twice, resolved by certification |
| **Joiner / Mover / Leaver** | HR snapshot 1 → 2 | Lilia joins, Boris moves Finance→Sales, Daniel terminated |
| **Access accumulation after a move** | Boris keeps `ledger_post` | Lifecycle can't see it; the **certification catches it** |
| **Certification + enforcement** | `certify-*` | 22 items, revocations pushed back through the connector |
| **Audit evidence** | `engine/out/audit.jsonl`, report | Who, what, when, why, result |

## Layout

```
app/finapp.py            target SaaS app (FastAPI + SQLite)
connector/               SailPoint Connector SDK project (TypeScript)
engine/govlab.py         governance engine (CLI)
config/roles.yaml        RBAC model
config/sod.yaml          SoD policies
data/hr_snapshot_*.csv   authoritative source, before/after
scripts/                 setup_vm.sh, start.sh, stop.sh, demo.sh
```
