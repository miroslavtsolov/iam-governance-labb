"""A tiny fake SaaS application used as a *target system* for the lab. It has its own users and entitlements (like any SaaS app you would onboard as a "source")."""
import json, os, sqlite3
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB = os.environ.get("FINAPP_DB", os.path.join(os.path.dirname(__file__), "finapp.db"))

ENTITLEMENTS = [
    ("ledger_read",    "Read general ledger",          0),
    ("ledger_post",    "Post journal entries",         0),
    ("vendor_create",  "Create / edit vendors",        1),
    ("payment_approve","Approve outgoing payments",    1),
    ("crm_read",       "Read CRM records",             0),
    ("crm_edit",       "Edit CRM records",             0),
    ("hr_records",     "Access HR records",            1),
    ("admin_console",  "Application administrator",    1),
]

# username, email, employee_id (None = missing, forces email-based correlation), entitlements, disabled
SEED = [
    ("anna.petrova",   "anna.petrova@corp.example",   "1001", ["ledger_read", "ledger_post", "vendor_create", "payment_approve"], 0),
    ("boris.ivanov",   "boris.ivanov@corp.example",   None,   ["ledger_read", "ledger_post"], 0),
    ("clara.dimitrova","clara.dimitrova@corp.example","1003", ["admin_console"], 0),
    ("daniel.georgiev","daniel.georgiev@corp.example","1004", ["admin_console", "ledger_read"], 0),
    ("elena.todorova", "elena.todorova@corp.example", "1005", ["hr_records"], 0),
    ("filip.stoyanov", "filip.stoyanov@corp.example", "1006", ["crm_read", "crm_edit"], 0),
    ("svc.legacy",     "legacy@corp.example",         None,   ["admin_console"], 0),   # orphan
    ("tmp.contractor", "tmp.contractor@vendor.example", None, ["ledger_read"], 0),      # orphan
]

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init(reset=False):
    if reset and os.path.exists(DB):
        os.remove(DB)
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, email TEXT, display_name TEXT,
        employee_id TEXT, department TEXT, disabled INTEGER DEFAULT 0, entitlements TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS entitlements(id TEXT PRIMARY KEY, description TEXT, privileged INTEGER);
    """)
    if not c.execute("SELECT 1 FROM entitlements").fetchone():
        c.executemany("INSERT INTO entitlements VALUES (?,?,?)", ENTITLEMENTS)
        for u, e, emp, ents, dis in SEED:
            c.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                      (u, e, u.replace(".", " ").title(), emp, None, dis, json.dumps(ents)))
        c.commit()
    c.close()

def row_to_user(r):
    d = dict(r); d["entitlements"] = json.loads(d["entitlements"]); d["disabled"] = bool(d["disabled"]); return d

app = FastAPI(title="FinApp (lab target system)")
init(reset=os.environ.get("FINAPP_RESET") == "1")

class NewUser(BaseModel):
    username: str
    email: str
    display_name: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    entitlements: List[str] = []

class Patch(BaseModel):
    add: List[str] = []
    remove: List[str] = []
    disabled: Optional[bool] = None

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/users")
def users():
    c = conn(); r = [row_to_user(x) for x in c.execute("SELECT * FROM users ORDER BY username")]; c.close(); return r

@app.get("/users/{username}")
def user(username: str):
    c = conn(); r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone(); c.close()
    if not r: raise HTTPException(404, "not found")
    return row_to_user(r)

@app.get("/entitlements")
def entitlements():
    c = conn(); r = [dict(x) | {"privileged": bool(x["privileged"])} for x in c.execute("SELECT * FROM entitlements")]; c.close(); return r

@app.post("/users", status_code=201)
def create(u: NewUser):
    c = conn()
    if c.execute("SELECT 1 FROM users WHERE username=?", (u.username,)).fetchone():
        raise HTTPException(409, "exists")
    c.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
              (u.username, u.email, u.display_name or u.username, u.employee_id, u.department, 0, json.dumps(u.entitlements)))
    c.commit(); c.close(); return user(u.username)

@app.patch("/users/{username}")
def patch(username: str, p: Patch):
    c = conn(); r = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not r: raise HTTPException(404, "not found")
    ents = set(json.loads(r["entitlements"])); ents |= set(p.add); ents -= set(p.remove)
    dis = r["disabled"] if p.disabled is None else int(p.disabled)
    c.execute("UPDATE users SET entitlements=?, disabled=? WHERE username=?", (json.dumps(sorted(ents)), dis, username))
    c.commit(); c.close(); return user(username)

@app.delete("/users/{username}", status_code=204)
def delete(username: str):
    c = conn(); c.execute("DELETE FROM users WHERE username=?", (username,)); c.commit(); c.close()
