#!/usr/bin/env python3
"""A small identity-governance engine for the lab.

   Simulates the core functions of an IGA platform: it aggregates accounts through a custom
   SaaS connector built on SailPoint's public Connector SDK (run locally with spcx), correlates them to
   identities, applies RBAC + lifecycle (joiner/mover/leaver), detects SoD violations, runs
   access certifications and writes an audit trail.


Usage:
  govlab.py run --hr data/hr_snapshot_1.csv [--apply]
  govlab.py certify-start
  govlab.py certify-complete [--decisions file.csv]
  govlab.py report
"""
import argparse, csv, datetime as dt, html, json, os, sys
import requests, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "engine", "out")
CONNECTOR_URL = os.environ.get("CONNECTOR_URL", "http://localhost:3000")
FINAPP_URL = os.environ.get("FINAPP_URL", "http://localhost:8001")
os.makedirs(OUT, exist_ok=True)
P = lambda n: os.path.join(OUT, n)

# --------------------------------------------------------------------------- helpers
def now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
def load_yaml(p): return yaml.safe_load(open(os.path.join(ROOT, p)))
def load_json(n, default):
    return json.load(open(P(n))) if os.path.exists(P(n)) else default
def save_json(n, obj): json.dump(obj, open(P(n), "w"), indent=2)

def audit(actor, action, target, detail, reason="", result="ok"):
    rec = dict(ts=now(), actor=actor, action=action, target=target, detail=detail, reason=reason, result=result)
    with open(P("audit.jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")
    return rec

# --------------------------------------------------------------------------- connector client
class Connector:
    """Talks to the SailPoint SaaS connector the same way ISC would: one command per call."""
    def call(self, ctype, inp=None):
        body = {"type": ctype, "input": inp or {}, "config": {"baseUrl": FINAPP_URL}}
        r = requests.post(CONNECTOR_URL, json=body, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"{ctype} failed: {r.text[:300]}")
        out = []
        for line in r.text.splitlines():
            if not line.strip(): continue
            msg = json.loads(line)
            if msg.get("type") == "output": out.append(msg["data"])
        return out
    def test(self): return self.call("std:test-connection") is not None
    def accounts(self): return self.call("std:account:list")
    def entitlements(self): return self.call("std:entitlement:list", {"type": "entitlement"})
    def create(self, username, attrs): return self.call("std:account:create", {"identity": username, "attributes": attrs})
    def change(self, username, add=(), remove=()):
        ch = [{"op": "Add", "attribute": "entitlements", "value": list(add)}] if add else []
        ch += [{"op": "Remove", "attribute": "entitlements", "value": list(remove)}] if remove else []
        return self.call("std:account:update", {"identity": username, "changes": ch})
    def disable(self, username): return self.call("std:account:disable", {"identity": username})
    def delete(self, username): return self.call("std:account:delete", {"identity": username})

# --------------------------------------------------------------------------- model
def roles_for(identity, roles_cfg):
    """Role assignment from HR attributes (membership criteria)."""
    if identity["status"] != "active": return []
    res = []
    for name, r in roles_cfg["roles"].items():
        if all(identity.get(k) == v for k, v in r["match"].items()): res.append(name)
    return res

def ents_of_roles(role_names, roles_cfg):
    s = set()
    for n in role_names: s |= set(roles_cfg["roles"][n]["entitlements"])
    return s

def build_identities(hr_path, roles_cfg):
    ids = {}
    for row in csv.DictReader(open(hr_path, newline="")):
        row = {k: (v or "").strip() for k, v in row.items()}
        row["roles"] = roles_for(row, roles_cfg)
        ids[row["employee_id"]] = row
    return ids

def correlate(accounts, identities):
    """Rule 1: employee_id. Rule 2 (fallback): email. Otherwise uncorrelated = orphan."""
    by_email = {i["email"].lower(): i["employee_id"] for i in identities.values()}
    corr, orphans = {}, []
    for a in accounts:
        at = a["attributes"]; emp = at.get("employee_id") or ""
        if emp and emp in identities:
            corr[a["identity"]] = (emp, "employee_id")
        elif at.get("email", "").lower() in by_email:
            corr[a["identity"]] = (by_email[at["email"].lower()], "email")
        else:
            orphans.append(a["identity"])
    return corr, orphans

def sod_violations(accounts, sod_cfg):
    out = []
    for a in accounts:
        have = set(a["attributes"].get("entitlements") or [])
        for p in sod_cfg["policies"]:
            if set(p["conflict"]) <= have:
                out.append(dict(account=a["identity"], policy=p["name"], conflict=p["conflict"]))
    return out

# --------------------------------------------------------------------------- RUN: aggregate + correlate + lifecycle + provision
def cmd_run(args):
    roles_cfg, sod_cfg = load_yaml("config/roles.yaml"), load_yaml("config/sod.yaml")
    cx = Connector(); cx.test()
    identities = build_identities(os.path.join(ROOT, args.hr), roles_cfg)
    prev = load_json("state.json", {}).get("identities", {})
    accounts = cx.accounts()
    corr, orphans = correlate(accounts, identities)
    acct = {a["identity"]: a for a in accounts}
    acct_by_emp = {}
    for u, (emp, _) in corr.items(): acct_by_emp.setdefault(emp, []).append(u)

    print(f"\n=== Aggregation: {len(accounts)} accounts | correlated {len(corr)} | orphans {len(orphans)}")
    for u, (emp, rule) in sorted(corr.items()): print(f"   correlated  {u:18} -> {emp} ({rule})")
    for u in orphans: print(f"   ORPHAN      {u}")

    # lifecycle events from HR diff
    events = []
    for emp, i in identities.items():
        p = prev.get(emp)
        if prev and not p and i["status"] == "active": events.append(("joiner", emp, f"{i['first_name']} {i['last_name']} joins {i['department']}"))
        elif p and p["status"] == "active" and i["status"] != "active": events.append(("leaver", emp, f"{i['first_name']} {i['last_name']} terminated"))
        elif p and (p["department"], p["job_title"]) != (i["department"], i["job_title"]) and i["status"] == "active":
            events.append(("mover", emp, f"{i['first_name']} {i['last_name']}: {p['department']}/{p['job_title']} -> {i['department']}/{i['job_title']}"))
    print(f"\n=== Lifecycle events vs previous snapshot: {len(events) or 'none (baseline)'}")
    for t, emp, d in events: print(f"   {t.upper():7} {emp}  {d}")

    # plan
    plan = []
    for emp, i in identities.items():
        desired = ents_of_roles(i["roles"], roles_cfg)
        existing = acct_by_emp.get(emp, [])
        if i["status"] != "active":
            for u in existing:
                cur = set(acct[u]["attributes"].get("entitlements") or [])
                if cur or not acct[u].get("disabled"):
                    plan.append(dict(op="leaver", account=u, remove=sorted(cur), reason=f"Leaver {emp}: disable + strip access"))
            continue
        if not existing and desired:
            username = i["email"].split("@")[0]
            plan.append(dict(op="create", account=username, emp=emp, email=i["email"], add=sorted(desired),
                             reason=f"Birthright via roles {i['roles']}"))
            continue
        for u in existing:
            cur = set(acct[u]["attributes"].get("entitlements") or [])
            add = desired - cur
            p = prev.get(emp)
            remove = set()
            if p and (p["department"], p["job_title"]) != (i["department"], i["job_title"]):
                old = ents_of_roles(p["roles"], roles_cfg)
                remove = (old - desired) & cur       # mover: take away what the OLD roles gave
            if add: plan.append(dict(op="grant", account=u, add=sorted(add), reason=f"Role(s) {i['roles']}"))
            if remove: plan.append(dict(op="revoke", account=u, remove=sorted(remove), reason="Mover: no longer granted by new roles"))

    print(f"\n=== Provisioning plan ({len(plan)} actions){'  [APPLYING]' if args.apply else '  [dry run - use --apply]'}")
    for a in plan:
        print(f"   {a['op'].upper():7} {a['account']:18} +{a.get('add', [])} -{a.get('remove', [])}  ({a['reason']})")

    if args.apply:
        for a in plan:
            try:
                if a["op"] == "create":
                    cx.create(a["account"], {"email": a["email"], "employee_id": a["emp"], "entitlements": a["add"]})
                elif a["op"] == "leaver":
                    if a["remove"]: cx.change(a["account"], remove=a["remove"])
                    cx.disable(a["account"])
                else:
                    cx.change(a["account"], add=a.get("add", []), remove=a.get("remove", []))
                audit("engine", a["op"], a["account"], {k: a.get(k) for k in ("add", "remove")}, a["reason"])
            except Exception as e:
                audit("engine", a["op"], a["account"], {}, a["reason"], result=f"FAILED: {e}")
                print("   !! failed:", e)
        for t, emp, d in events: audit("engine", f"lifecycle:{t}", emp, {"detail": d}, "HR snapshot diff")

    # post-state analysis (re-aggregate to see the real result)
    accounts = cx.accounts() if args.apply else accounts
    corr, orphans = correlate(accounts, identities)
    acct = {a["identity"]: a for a in accounts}
    viol = sod_violations(accounts, sod_cfg)
    unjustified = []
    for u, (emp, _) in corr.items():
        i = identities[emp]
        have = set(acct[u]["attributes"].get("entitlements") or [])
        extra = have - ents_of_roles(i["roles"], roles_cfg)
        if extra and i["status"] == "active": unjustified.append((u, sorted(extra)))
    print(f"\n=== Findings after provisioning")
    for v in viol: print(f"   SoD VIOLATION  {v['account']}: {v['policy']} {v['conflict']}")
    for u, e in unjustified: print(f"   NOT ROLE-JUSTIFIED  {u}: {e}")
    for u in orphans: print(f"   ORPHAN ACCOUNT  {u}: {acct[u]['attributes'].get('entitlements')}")

    if args.apply:   # only a real run advances the lifecycle baseline
        save_json("state.json", {"identities": {e: dict(department=i["department"], job_title=i["job_title"], status=i["status"], roles=i["roles"]) for e, i in identities.items()}, "hr": args.hr, "ran": now()})
        save_json("identities.json", identities)
    save_json("findings.json", dict(sod=viol, unjustified=unjustified, orphans=orphans, events=events, plan=plan,
                                    corr={u: list(v) for u, v in corr.items()}, applied=args.apply))

# --------------------------------------------------------------------------- CERTIFICATION
def cmd_certify_start(args):
    roles_cfg, sod_cfg = load_yaml("config/roles.yaml"), load_yaml("config/sod.yaml")
    cx = Connector(); accounts = cx.accounts(); ents = {e["identity"]: e["attributes"] for e in cx.entitlements()}
    identities = load_json("identities.json", {})
    if not identities: sys.exit("Run `govlab.py run` first.")
    corr, orphans = correlate(accounts, identities)
    viol = {(v["account"], e) for v in sod_violations(accounts, sod_cfg) for e in v["conflict"]}
    items = []
    for a in accounts:
        u = a["identity"]; have = a["attributes"].get("entitlements") or []
        if u in orphans:
            items.append(dict(id=len(items) + 1, type="orphan-account", account=u, entitlement="(whole account)", reviewer="iam-team",
                              justified=False, privileged=False, sod=False, decision=None)); continue
        emp = corr[u][0]; ident = identities[emp]
        if ident["status"] != "active": continue
        justified = ents_of_roles(ident["roles"], roles_cfg)
        for e in have:
            items.append(dict(id=len(items) + 1, type="entitlement", account=u, entitlement=e, reviewer=ident["manager_id"],
                              justified=e in justified, privileged=bool(ents.get(e, {}).get("privileged")),
                              sod=(u, e) in viol, decision=None))
    camp = dict(name=f"FinApp access review {dt.date.today()}", created=now(), status="active", items=items)
    save_json("certification.json", camp)
    audit("engine", "certification:start", camp["name"], {"items": len(items)}, "Periodic review")
    print(f"Campaign '{camp['name']}' created with {len(items)} review items for {len({i['reviewer'] for i in items})} reviewers.")
    save_json("certification.json", camp)

def cmd_certify_complete(args):
    camp = load_json("certification.json", None)
    if not camp: sys.exit("No campaign. Run certify-start first.")
    manual = {}
    if args.decisions:
        for r in csv.DictReader(open(args.decisions)): manual[int(r["id"])] = r["decision"].strip().lower()
    cx = Connector(); revoked = 0
    for it in camp["items"]:
        # default reviewer policy: keep what a role justifies, revoke the rest.
        # (SoD-flagged items are shown to the reviewer as a warning; revoking the
        #  unjustified side of the conflict resolves it while role access is kept.)
        d = manual.get(it["id"]) or ("certify" if it["justified"] else "revoke")
        it["decision"] = d
        if d != "revoke": audit(f"reviewer:{it['reviewer']}", "certify", f"{it['account']}:{it['entitlement']}", {}, "Kept"); continue
        try:
            if it["type"] == "orphan-account": cx.delete(it["account"])
            else: cx.change(it["account"], remove=[it["entitlement"]])
            revoked += 1
            why = "Orphan account" if it["type"] == "orphan-account" else ("SoD conflict" if it["sod"] else "Not justified by any role")
            audit(f"reviewer:{it['reviewer']}", "revoke", f"{it['account']}:{it['entitlement']}", {}, why)
        except Exception as e:
            audit(f"reviewer:{it['reviewer']}", "revoke", f"{it['account']}:{it['entitlement']}", {}, "Enforcement", result=f"FAILED: {e}")
    camp["status"] = "completed"; camp["completed"] = now()
    save_json("certification.json", camp)
    print(f"Campaign completed: {len(camp['items'])} items, {revoked} revocations enforced through the connector.")

# --------------------------------------------------------------------------- REPORT
def cmd_report(args):
    f = load_json("findings.json", {}); camp = load_json("certification.json", None)
    logs = [json.loads(l) for l in open(P("audit.jsonl"))] if os.path.exists(P("audit.jsonl")) else []
    cx = Connector(); accounts = cx.accounts()
    sod_cfg = load_yaml("config/sod.yaml"); identities = load_json("identities.json", {})
    corr, orphans = correlate(accounts, identities) if identities else ({}, [])
    viol_now = sod_violations(accounts, sod_cfg)
    esc = lambda s: html.escape(str(s))
    def table(head, rows):
        return "<table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>" + "".join(
            "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows) + "</table>"
    items = camp["items"] if camp else []
    done = [i for i in items if i["decision"]]
    kpis = [("Accounts", len(accounts)), ("Correlated", len(corr)), ("Orphans now", len(orphans)),
            ("SoD violations now", len(viol_now)), ("Review items", len(items)),
            ("Completion", f"{100*len(done)//len(items)}%" if items else "n/a"),
            ("Revoke rate", f"{100*sum(1 for i in done if i['decision']=='revoke')//len(done)}%" if done else "n/a")]
    body = "".join(f"<div class=k><b>{esc(v)}</b><span>{esc(k)}</span></div>" for k, v in kpis)
    page = f"""<!doctype html><meta charset=utf-8><title>FinApp governance report</title>
<style>body{{font:14px system-ui;margin:2rem auto;max-width:1100px;color:#222}}h2{{margin-top:2rem}}
.k{{display:inline-block;margin:0 1rem 1rem 0;padding:.8rem 1.2rem;border:1px solid #ccc;border-radius:8px}}
.k b{{display:block;font-size:1.6rem}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}}th{{background:#f3f3f3}}</style>
<h1>FinApp governance report</h1><p>Generated {now()} UTC</p>{body}
<h2>Current accounts</h2>{table(['Account','Identity','Entitlements'],[[a['identity'],(corr.get(a['identity']) or ['ORPHAN'])[0],', '.join(a['attributes'].get('entitlements') or [])+(' [DISABLED]' if a.get('disabled') else '')] for a in accounts])}
<h2>Open SoD violations</h2>{table(['Account','Policy','Conflict'],[[v['account'],v['policy'],' + '.join(v['conflict'])] for v in viol_now]) if viol_now else '<p>None</p>'}
<h2>Certification campaign</h2>{table(['#','Type','Account','Entitlement','Reviewer','Role-justified','SoD','Decision'],[[i['id'],i['type'],i['account'],i['entitlement'],i['reviewer'],i['justified'],i['sod'],i['decision']] for i in items]) if items else '<p>No campaign yet</p>'}
<h2>Audit trail ({len(logs)} events)</h2>{table(['Time (UTC)','Actor','Action','Target','Reason','Result'],[[l['ts'],l['actor'],l['action'],l['target'],l['reason'],l['result']] for l in logs])}"""
    open(P("report.html"), "w").write(page)
    print("Report written:", P("report.html"))

# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("--hr", required=True); r.add_argument("--apply", action="store_true"); r.set_defaults(fn=cmd_run)
    sp.add_parser("certify-start").set_defaults(fn=cmd_certify_start)
    c = sp.add_parser("certify-complete"); c.add_argument("--decisions"); c.set_defaults(fn=cmd_certify_complete)
    sp.add_parser("report").set_defaults(fn=cmd_report)
    a = ap.parse_args(); a.fn(a)
