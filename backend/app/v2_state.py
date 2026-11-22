"""Session-local conversations with shared household settings."""
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from . import store

def fresh(version):
    return dict(revision=0,preferences_version=version,items=[],extra_foods=[],questions=[],menu=None,
                ready=False,timeline=None,messages=[])

DEFAULT = "default"

def state_key(session_id):
    return "v2:active" if session_id == DEFAULT else "v2:chat:" + session_id

def list_sessions():
    with store.connect() as db:
        rows = db.execute("""
            SELECT key, COALESCE(json_extract(value,'$.title'),
                                 substr(json_extract(value,'$.messages[0].content'),1,28)),
                   COALESCE(json_extract(value,'$.updated_at'),0)
            FROM state WHERE key='v2:active' OR key GLOB 'v2:chat:*'
            ORDER BY 3 DESC
        """).fetchall()
    sessions = [{"id": DEFAULT if key == "v2:active" else key.removeprefix("v2:chat:"),
                 "title": title or "新对话"} for key, title, _ in rows]
    if not any(session["id"] == DEFAULT for session in sessions):
        sessions.append({"id": DEFAULT, "title": "初始对话"})
    return sessions

def new_session():
    session_id = uuid4().hex
    state = fresh(store.preferences()["version"])
    state.update(session_id=session_id, updated_at=time.time())
    store.put(state_key(session_id), state)
    return {"id": session_id, "title": "新对话"}

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
        state["title"]=value.get("title") or next((m["content"][:28] for m in value["messages"]), None)
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
        if not state.get("title") and state["messages"]:
            state["title"]=state["messages"][0]["content"][:28]
        state["updated_at"]=time.time()
        state["revision"]=revision+1
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",(key,json.dumps(state,ensure_ascii=False)))
    return state
