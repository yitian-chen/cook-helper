"""One current menu for the single-user app. Commit turns using optimistic concurrency."""
import json
from fastapi import HTTPException
from . import store

def fresh(version):
    return dict(revision=0,preferences_version=version,items=[],extra_foods=[],questions=[],menu=None,
                ready=False,timeline=None,messages=[])

def active():
    prefs=store.preferences()
    value=store.get("v2:active")
    if value and value["preferences_version"]==prefs["version"]:
        return value
    state=fresh(prefs["version"])
    if value:
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
        old=read("v2:active")
        current=old.get("revision",0) if old.get("preferences_version")==prefs else 0
        if prefs!=state["preferences_version"] or current!=revision:
            raise HTTPException(409,"餐单已更新，请刷新查看最新进度")
        if kitchen is not None:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",("v2:kitchen",json.dumps(kitchen)))
        state["revision"]=revision+1
        db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",("v2:active",json.dumps(state,ensure_ascii=False)))
    return state
