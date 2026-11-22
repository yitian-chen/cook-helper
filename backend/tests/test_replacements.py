from fastapi.testclient import TestClient

from app import store, v2_state
from app.domain import Preferences
from app.main import app
from app.menu import CATALOG, portions, meal_reports
from app.replacements import Swap, alternatives


def meal_state():
    state = v2_state.fresh(1)
    state.update(items=[{"ingredient_id":i,"amount":1,"unit":"piece"} for i in ("beef","cucumber","egg")],
                 preferred_foods=["beef"], ready=True,
                 menu=dict(version=1,people=2,options={},dishes=[
                     portions(CATALOG[k],2,2) for k in ("cucumber_egg","plain_rice")]))
    return state


def test_filter_and_shopping_deltas():
    state = meal_state()
    options = alternatives(state,Preferences(),Swap(dish_id="cucumber_egg",ingredient_id="beef",
                           exclude_ingredients=["egg"]),None)["options"]
    assert 0 < len(options) <= 3
    for option in options:
        ingredients = {i.ingredient_id for i in CATALOG[option["id"]].ingredients}
        assert "beef" in ingredients and "egg" not in ingredients
        assert "牛肉" in option["reason"] and all(i["name"]!="牛肉" for i in option["added"])
        assert option["added"]
    assert not alternatives(state,Preferences(dietary_rules=["vegetarian"]),
                            Swap(dish_id="cucumber_egg",ingredient_id="beef"),None)["options"]
    assert all("wok" not in row["equipment"] for row in alternatives(state,Preferences(),
               Swap(dish_id="cucumber_egg",avoid_equipment=["wok"]),None)["options"])


def test_preview_select_stale_click_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(store,"DB",tmp_path/"swap.db")
    monkeypatch.setenv("REASONING_PROVIDER","mock")
    state = meal_state()
    state["timeline"] = {"id":"original","version":1,"done":["cut"],"steps":[]}
    store.put("v2:active",state)
    version = dict(revision=0,preferences_version=1)
    with TestClient(app) as client:
        preview = client.post("/api/v2/alternatives",json={**version,"dish_id":"cucumber_egg"}).json()
        assert len(preview["options"]) == 3 and store.get("v2:active") == state
        target = preview["options"][0]["id"]
        choose = dict(**version,action="choose",dish_id="cucumber_egg",target_id=target)
        response = client.post("/api/v2/turn",json=choose)
        response.raise_for_status()
        updated = response.json()
        assert [d["id"] for d in updated["menu"]["dishes"]] == [target,"plain_rice"]
        assert not updated["ready"] and not updated["timeline"]
        assert updated["shopping"] == meal_reports(updated["menu"],state["items"])[0]
        assert client.post("/api/v2/turn",json=choose).status_code == 409
        restored = client.post("/api/v2/turn",json={**version,"revision":updated["revision"],"action":"undo"}).json()
        assert restored["menu"]["dishes"] == state["menu"]["dishes"] and restored["ready"]
        assert restored["timeline"]["done"] == ["cut"]
        assert restored["nutrition"] == meal_reports(state["menu"],state["items"])[1]

def test_chat_only_applies_an_explicit_target(tmp_path,monkeypatch):
    import json
    from app.llm_client import LLMClient

    monkeypatch.setattr(store,"DB",tmp_path/"chat.db")
    monkeypatch.setenv("REASONING_PROVIDER","mock")
    initial = meal_state()
    store.put("v2:active",initial)
    target = CATALOG["celery_beef"].name
    args = {"dish_id":"cucumber_egg","ingredient_id":"beef"}
    original_chat = LLMClient.chat
    async def chat(self,messages,tools=None):
        if tools and any(t["function"]["name"]=="replace_dish" for t in tools):
            return {"role":"assistant","content":None,"tool_calls":[{
                "id":"swap","type":"function","function":{"name":"replace_dish","arguments":json.dumps(args)}}]}
        return await original_chat(self,messages,tools)
    monkeypatch.setattr(LLMClient,"chat",chat)
    with TestClient(app) as client:
        preview = client.post("/api/v2/turn",json=dict(revision=0,preferences_version=1,text="把黄瓜炒蛋换成牛肉菜")).json()
        assert preview["menu"] == initial["menu"] and preview["swap_preview"]["options"]
        args["target_name"] = target
        chosen = client.post("/api/v2/turn",json=dict(revision=1,preferences_version=1,text="把黄瓜炒蛋换成"+target)).json()
        assert chosen["menu"]["dishes"][0]["id"] == "celery_beef" and chosen["undo_swap"]
