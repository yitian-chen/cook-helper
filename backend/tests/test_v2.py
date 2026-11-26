from collections import Counter
from datetime import datetime
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

def test_grouped_cooking_progress_updates_atomically(tmp_path,monkeypatch):
    from app.main import app
    monkeypatch.setattr(store,"DB",tmp_path/"grouped-progress.db")
    timeline={
        "id":"timeline",
        "steps":[
            {"id":"wash","after":[]},
            {"id":"slice","after":["wash"]},
            {"id":"season","after":["slice"]},
            {"id":"cook","after":["season"]},
            {"id":"rice","after":[]},
        ],
        "done":[],
    }
    with TestClient(app) as client:
        initial=client.get("/api/v2/current").json()
        store.put("v2:active",{**initial,"timeline":timeline})

        def mark(ids,revision,done=True):
            return client.put("/api/v2/progress",json={
                "revision":revision,"preferences_version":1,"timeline_id":"timeline",
                "step_ids":ids,"done":done,
            })

        assert mark(["rice","season"],0).status_code==409
        assert client.get("/api/v2/current").json()["timeline"]["done"]==[]
        grouped=mark(["season","wash","slice"],0).json()
        assert grouped["timeline"]["done"]==["season","slice","wash"]
        assert mark(["wash","slice","season"],0).json()["revision"]==grouped["revision"]
        cooked=mark(["cook"],grouped["revision"]).json()
        assert mark(["wash","slice","season"],cooked["revision"],done=False).status_code==409
        assert client.get("/api/v2/current").json()["timeline"]["done"]==cooked["timeline"]["done"]
        assert mark(["wash","slice","season","cook"],cooked["revision"],done=False).json()["timeline"]["done"]==[]

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

@pytest.mark.parametrize("amount,unit", [(1,"piece"), (None,"g")])
def test_recent_beef_is_used_without_weighing(amount,unit):
    inventory=[dict(ingredient_id=k,amount=2,unit="piece")
               for k in ("tomato","egg","potato","carrot","cucumber")]
    inventory.append(dict(ingredient_id="beef",amount=amount,unit=unit))
    options=MenuOptions(dishes=3)
    menu=make_menu(inventory,Preferences(),options,1,preferred=["beef"])
    assert any(i["ingredient_id"]=="beef" for d in menu["dishes"] for i in d["ingredients"])
    shopping,_=meal_reports(menu,inventory)
    assert not any(i["ingredient_id"]=="beef" for i in shopping["buy"])
    assert inventory[-1]["amount"]==amount
    restricted=make_menu(inventory,Preferences(dietary_rules=["vegetarian"]),options,2,preferred=["beef"])
    assert not any(i["ingredient_id"]=="beef" for d in restricted["dishes"] for i in d["ingredients"])

def test_sessions_isolate_inventory_and_share_household_settings(tmp_path, monkeypatch):
    from app.main import app
    monkeypatch.setattr(store, "DB", tmp_path / "sessions.db")
    legacy = dict(updated_at=datetime(2026,11,20,12).timestamp(),revision=3, preferences_version=1, items=[{"ingredient_id":"beef","amount":1,"unit":"piece"}],
                  extra_foods=[], questions=[], menu=make_menu([],Preferences(),MenuOptions(),1), ready=True,
                  timeline={"id":"previous"}, messages=[{"role":"user","content":"还有一块牛肉"}])
    store.put("v2:active", legacy)
    with TestClient(app) as client:
        assert client.get("/api/v2/sessions").json() == [{"id":"default","title":"2026-11-20 中午"}]
        other = client.post("/api/v2/sessions").json()["id"]
        def read(session_id):
            return client.get("/api/v2/current",params={"session_id":session_id}).json()
        blank = read(other)
        assert not any(blank[k] for k in ("items","questions","messages","menu","timeline","ready"))
        settings = dict(session_id=other,revision=0,preferences_version=1,kitchen=KITCHEN)
        assert client.put("/api/v2/kitchen",json=settings).status_code == 200
        first = read("default")
        assert first["items"] == legacy["items"] and first["messages"] == legacy["messages"]
        assert first["menu"] == legacy["menu"] and first["timeline"] is None and first["kitchen"] == KITCHEN
        assert client.get("/api/v2/current?session_id=unknown").status_code == 404
        client.put("/api/preferences",json={"allergens":["egg"]}).raise_for_status()
        assert read(other)["preferences_version"] == read("default")["preferences_version"] == 2
        assert not read(other)["items"] and read("default")["items"] == legacy["items"]

def test_session_names_across_periods_and_concurrent_creation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import v2_state
    monkeypatch.setattr(store, "DB", tmp_path / "names.db")
    def at(day, hour):
        timestamp = datetime(2026,11,day,hour).timestamp()
        monkeypatch.setattr(v2_state.time,"time",lambda: timestamp)
    at(22,10)
    first = v2_state.list_sessions()[0]
    assert first["title"] == "2026-11-22 上午"
    with ThreadPoolExecutor(max_workers=3) as pool:
        duplicates = list(pool.map(lambda _: v2_state.new_session(), range(3)))
    assert sorted(s["title"] for s in duplicates) == [first["title"]+f" {i}" for i in (2,3,4)]
    for day,hour,period in [(22,11,"中午"),(22,14,"下午"),(22,18,"晚上"),(23,0,"上午")]:
        at(day,hour)
        assert v2_state.new_session()["title"] == f"2026-11-{day} {period}"
    assert first in v2_state.list_sessions()


def test_first_inventory_tool_uses_request_then_waits_for_menu_confirmation(tmp_path, monkeypatch):
    import json
    from app import v2
    from app.main import app
    from app.meal import InventoryChange

    monkeypatch.setattr(store, "DB", tmp_path / "first-meal.db")
    monkeypatch.setenv("REASONING_PROVIDER", "mock")
    used_tools = []
    mock_chat = v2.LLMClient.chat

    async def chat(self, messages, tools=None):
        reply = await mock_chat(self, messages, tools)
        if tools:
            call = reply["tool_calls"][0]["function"]
            used_tools.append(call["name"])
            if call["name"] == "write_inventory":
                call["arguments"] = json.dumps({"items": [{"ingredient_id":"garlic","amount":0}]})
        return reply

    async def interpret(text, items, extras, questions):
        assert text == "有一块牛肉"
        return InventoryChange(intent="inventory", items=[
            {"ingredient_id":"beef","amount":1,"unit":"piece"},
        ])

    monkeypatch.setattr(v2.LLMClient, "chat", chat)
    monkeypatch.setattr(v2, "interpret", interpret)
    with TestClient(app) as client:
        draft = client.post("/api/v2/turn", json={"revision":0,"preferences_version":1,
                                                "text":"有一块牛肉"}).json()
        assert used_tools == ["write_inventory","compose_menu"]
        assert [i["ingredient_id"] for i in draft["items"]] == ["beef"]
        assert draft["menu"] and not draft["confirmed"] and not draft["shopping"] and not draft["nutrition"]
        used_tools.clear()
        confirmed = client.post("/api/v2/turn", json={"revision":draft["revision"],"preferences_version":1,
                                                    "action":"confirm"}).json()
        assert used_tools == ["confirm_menu","generate_shopping_list","analyze_nutrition"]
        assert confirmed["confirmed"] and not confirmed["ready"]
        assert not {"scallion","ginger","garlic"} & {item["ingredient_id"] for item in confirmed["shopping"]["buy"]}
        assert confirmed["nutrition"]["per_person"]


def test_supplementing_a_confirmed_menu_keeps_dishes_and_refreshes_reports(tmp_path, monkeypatch):
    from app import v2, v2_state
    from app.main import app
    from app.meal import InventoryChange

    monkeypatch.setattr(store, "DB", tmp_path / "confirmed-meal.db")
    monkeypatch.setenv("REASONING_PROVIDER", "mock")
    dish = portions(CATALOG["tomato_egg"], 2, 1)
    menu = {"version": 1, "people": 2, "dishes": [dish], "options": MenuOptions().model_dump()}
    shopping, nutrition = meal_reports(menu, [])
    store.put("v2:active", {**v2_state.fresh(1), "menu": menu, "confirmed": True,
                            "shopping": shopping, "nutrition": nutrition})

    async def recognize_ingredients(image_id):
        assert image_id == "photo"
        return {"items": [], "uncertain": ["这是番茄吗？"]}

    async def interpret(text, items, extras, questions):
        assert text == "是番茄，已经买了两颗"
        assert questions == ["这是番茄吗？"]
        return InventoryChange(intent="inventory", items=[
            {"ingredient_id": "tomato", "amount": 2, "unit": "piece"},
        ])

    monkeypatch.setattr(v2, "recognize_ingredients", recognize_ingredients)
    monkeypatch.setattr(v2, "interpret", interpret)
    with TestClient(app) as client:
        def turn(revision, **payload):
            return client.post("/api/v2/turn", json={
                "revision": revision, "preferences_version": 1, **payload,
            })

        unclear = turn(0, image_id="photo").json()
        assert unclear["confirmed"] and unclear["menu"] == menu
        assert unclear["questions"] == ["这是番茄吗？"]
        assert unclear["shopping"] is None and unclear["nutrition"] is None
        assert turn(unclear["revision"], action="ready").status_code == 409

        updated = turn(unclear["revision"], text="是番茄，已经买了两颗").json()
        assert updated["confirmed"] and updated["menu"] == menu
        assert not updated["questions"] and not updated["ready"]
        assert {item["ingredient_id"] for item in updated["shopping"]["buy"]} == {"egg"}
        assert updated["nutrition"] == nutrition
        assert not turn(updated["revision"], action="replan").json()["confirmed"]
