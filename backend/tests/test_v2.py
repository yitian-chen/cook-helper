from collections import Counter
from fastapi.testclient import TestClient
import pytest

from app import store
from app.cooking import Kitchen, STEPS, schedule
from app.menu import CATALOG, MenuOptions, make_menu, meal_reports, portions
from app.domain import Preferences

KITCHEN = dict(cooks=1,burner=1,pot=1,wok=1,steamer=1,rice_cooker=1,oven=1)

def test_aggregate_and_unknown():
    dishes=[{"ingredients":[dict(ingredient_id="egg",amount=2,unit="piece"),
                            dict(ingredient_id="tomato",amount=150,unit="g")]}]*2
    inventory=[dict(ingredient_id="egg",amount=3,unit="piece"),dict(ingredient_id="tomato",amount=2,unit="piece")]
    shopping,nutrition=meal_reports(dict(dishes=dishes,people=2),inventory)
    assert [(i["ingredient_id"],i["missing"]) for i in shopping["buy"]]==[("egg",1)]
    assert shopping["available_unmeasured"][0]["required"]==300
    assert nutrition["per_person"]["kcal"]==round(nutrition["total"]["kcal"]/2,1)

def test_menu_policy_and_swap():
    items=[dict(ingredient_id=i,amount=2,unit="piece") for i in ("tomato","egg","bok_choy")]
    prefs=Preferences()
    menu=make_menu(items,prefs,MenuOptions(),1)
    assert [d["course"] for d in menu["dishes"]]==["main","side","staple"]
    swap=make_menu(items,prefs,MenuOptions(),2,menu,menu["dishes"][0]["id"],"tofu")
    assert swap["dishes"][1:]==menu["dishes"][1:]
    assert any(i["ingredient_id"]=="tofu" for i in swap["dishes"][0]["ingredients"])
    options=MenuOptions(people=4,staple=False,soup=True,dishes=2)
    custom=make_menu(items,Preferences(allergens=["egg"]),options,3)
    assert [d["course"] for d in custom["dishes"]]==["main","soup"]
    assert all(i["ingredient_id"]!="egg" for d in custom["dishes"] for i in d["ingredients"])
    assert len(make_menu(items,prefs,MenuOptions(people=1),1)["dishes"])==1

def check_resources(timeline,capacity):
    by_id={s["id"]:s for s in timeline["steps"]}
    for s in by_id.values():
        assert all(by_id[i]["end"]<=s["start"] for i in s["after"])
    for minute in range(timeline["minutes"]):
        active=[s for s in by_id.values() if s["start"]<=minute<s["end"]]
        assert sum(s["hands"] for s in active)<=capacity["cooks"]
        use=Counter(r for s in active for r in s["equipment"])
        assert all(count<=capacity[r] for r,count in use.items())

def test_all_recipes_and_parallel_resources():
    assert len(STEPS)==101
    for key in CATALOG:
        plan=schedule(dict(version=1,dishes=[portions(CATALOG[key],2,1)]),Kitchen(**KITCHEN),[])
        check_resources(plan,KITCHEN)
    menu=dict(version=1,dishes=[portions(CATALOG[key],2,3) for key in ("kung_pao_chicken","tomato_egg_soup","plain_rice")])
    for burners in (1,2):
        capacity={**KITCHEN,"burner":burners}
        plan=schedule(menu,Kitchen(**capacity),[])
        check_resources(plan,capacity)
        assert any(s["dish"]!="白米饭" and s["hands"] and any(
            r["dish"]=="白米饭" and not r["hands"] and r["start"]<s["end"] and s["start"]<r["end"]
            for r in plan["steps"]) for s in plan["steps"])
    with pytest.raises(ValueError,match="烤箱"):
        schedule(dict(version=1,dishes=[portions(CATALOG["baked_salmon"],2,1)]),Kitchen(**{**KITCHEN,"oven":0}),[])

def test_restore_dependencies_and_stale_actions(tmp_path,monkeypatch):
    from app.main import app
    monkeypatch.setattr(store,"DB",tmp_path/"v2.db")
    menu=make_menu([],Preferences(),MenuOptions(),1)
    with TestClient(app) as client:
        state=client.get("/api/v2/current").json()
        store.put("v2:active",{**state,"menu":menu})
        def turn(action):
            return client.post("/api/v2/turn",json=dict(action=action,revision=state["revision"],preferences_version=1))
        state=turn("ready").json()
        assert state["ready"] and not state["timeline"]
        state=client.put("/api/v2/kitchen",json=dict(revision=state["revision"],preferences_version=1,kitchen=KITCHEN)).json()
        state=turn("schedule").json()
        timeline=state["timeline"]
        def mark(step):
            return client.put("/api/v2/progress",json=dict(revision=state["revision"],preferences_version=1,
                              timeline_id=timeline["id"],step_id=step["id"],done=True))
        assert mark(next(s for s in timeline["steps"] if s["after"])).status_code==409
        first=timeline["steps"][0]
        committed=mark(first).json()
        assert mark(first).json()["revision"]==committed["revision"]
        assert client.get("/api/v2/current").json()["timeline"]["done"]==[first["id"]]
        assert turn("ready").status_code==409

def test_preference_change_retains_inventory_and_invalidates_old_plan(tmp_path,monkeypatch):
    from app.main import app
    monkeypatch.setattr(store,"DB",tmp_path/"pref.db")
    inventory=[dict(ingredient_id="egg",amount=3,unit="piece")]
    menu=make_menu(inventory,Preferences(),MenuOptions(),1)
    with TestClient(app) as client:
        state=client.get("/api/v2/current").json()
        store.put("v2:active",{**state,"items":inventory,"menu":menu,"ready":True,"timeline":{"id":"old"}})
        p=client.put("/api/preferences",json={"servings":4,"allergens":["egg"]}).json()
        state=client.get("/api/v2/current").json()
        assert state["items"]==inventory and not state["ready"] and not state["timeline"]
        reply=client.post("/api/v2/turn",json={"revision":0,"preferences_version":p["version"],"action":"replan"})
        assert reply.status_code==200
        menu=reply.json()["menu"]
        assert menu["people"]==4 and all(i["ingredient_id"]!="egg" for d in menu["dishes"] for i in d["ingredients"])
        assert client.post("/api/v2/turn",json={"revision":1,"preferences_version":p["version"],"action":"schedule"}).status_code==409
