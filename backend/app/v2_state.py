"""Session-local conversations with shared household settings."""
import json
import time
from datetime import datetime
from uuid import uuid4
from fastapi import HTTPException
from . import store

def fresh(version):
    return dict(revision=0,preferences_version=version,items=[],extra_foods=[],questions=[],menu=None,
                ready=False,timeline=None,messages=[])

DEFAULT = "default"

def state_key(session_id):
    return "v2:active" if session_id == DEFAULT else "v2:chat:" + session_id

def record_session(db, session_id, created_at):
    local = datetime.fromtimestamp(created_at)
    period = "上午" if local.hour < 11 else "中午" if local.hour < 14 else "下午" if local.hour < 18 else "晚上"
    label = f"{local:%Y-%m-%d} {period}"
    number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM chat_names WHERE label=?", (label,)).fetchone()[0]
    db.execute("INSERT INTO chat_names VALUES (?,?,?,?)", (session_id, created_at, label, number))
    return label if number == 1 else f"{label} {number}"

def prepare_names(db):
    db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE IF NOT EXISTS chat_names (
        id TEXT PRIMARY KEY, created_at REAL NOT NULL, label TEXT NOT NULL,
        number INTEGER NOT NULL, UNIQUE(label,number))""")
    # Older versions recorded only the last update. Use it once when importing history.
    missing = db.execute("""
        WITH existing(id,stamp) AS (
            SELECT 'default', (SELECT json_extract(value,'$.updated_at') FROM state WHERE key='v2:active')
            UNION ALL
            SELECT substr(key,9), json_extract(value,'$.updated_at') FROM state WHERE key GLOB 'v2:chat:*'
        )
        SELECT id,stamp FROM existing WHERE id NOT IN (SELECT id FROM chat_names)
        ORDER BY stamp,id
    """).fetchall()
    for session_id, timestamp in missing:
        record_session(db, session_id, timestamp or time.time())

def list_sessions():
    with store.connect() as db:
        prepare_names(db)
        rows = db.execute("SELECT id,label,number FROM chat_names ORDER BY created_at DESC,number DESC").fetchall()
    return [{"id": session_id, "title": label if number == 1 else f"{label} {number}"}
            for session_id, label, number in rows]

def new_session():
    session_id = uuid4().hex
    state = fresh(store.preferences()["version"])
    with store.connect() as db:
        prepare_names(db)
        now = time.time()
        title = record_session(db, session_id, now)
        state.update(session_id=session_id, updated_at=now)
        db.execute("INSERT INTO state VALUES (?,?)", (state_key(session_id), json.dumps(state)))
    return {"id": session_id, "title": title}

def active(session_id=DEFAULT):
    prefs=store.preferences()
    value=store.get(state_key(session_id))
    if value is None and session_id != DEFAULT:
        raise HTTPException(404, "对话不存在")
    if value and value["preferences_version"]==prefs["version"]:
        return {**value, "session_id":session_id}
    state=fresh(prefs["version"])
    state["session_id"]=session_id
    if value:
        state["messages"]=value["messages"]
        for key in ("items","extra_foods","questions"):
            state[key]=value[key]
        if value.get("menu"):
            state["prior_options"]={**value["menu"]["options"],"people":None}
    return state

def save(state,revision,kitchen=None):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        def read(key):
            row=db.execute("SELECT value FROM state WHERE key=?",(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        prefs=read("preferences").get("version",1)
        key=state_key(state["session_id"])
        old=read(key)
        current=old.get("revision",0) if old.get("preferences_version")==prefs else 0
        if prefs!=state["preferences_version"] or current!=revision:
            raise HTTPException(409,"餐单已更新，请刷新查看最新进度")
        if kitchen is not None and kitchen != read("v2:kitchen"):
            # A concurrent turn in another ready session may be scheduling with the old equipment.
            rows = db.execute("SELECT key,value FROM state WHERE (key='v2:active' OR key GLOB 'v2:chat:*') AND key!=?", (key,)).fetchall()
            for other_key, raw in rows:
                other = json.loads(raw)
                if other.get("menu"):
                    other["timeline"] = None
                    other["revision"] += 1
                    db.execute("UPDATE state SET value=? WHERE key=?", (json.dumps(other,ensure_ascii=False),other_key))
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",("v2:kitchen",json.dumps(kitchen)))
        state["updated_at"]=time.time()
        state["revision"]=revision+1
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",(key,json.dumps(state,ensure_ascii=False)))
    return state
