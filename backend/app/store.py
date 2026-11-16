import json
import os
import sqlite3
from pathlib import Path
from .domain import ROOT, Preferences

RUNTIME = Path(os.getenv("COOK_RUNTIME", str(ROOT / "runtime")))
RUNTIME.mkdir(parents=True, exist_ok=True)
DB = RUNTIME / "app.sqlite3"

def connect():
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return db

def get(key, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default

def put(key, value):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

def preferences():
    return get("preferences", {"version": 1, **Preferences().model_dump()})

def recover():
    with connect() as db:
        rows = db.execute("SELECT key,value FROM state WHERE key LIKE 'run:%'").fetchall()
    for key, raw in rows:
        run = json.loads(raw)
        if run["status"] in ("queued", "running"):
            run.update(status="failed", error="服务重启中断了任务，请重新发起")
            put(key, run)
