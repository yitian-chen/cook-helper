from fastapi.testclient import TestClient

def test_clarification_flow_and_selection(monkeypatch, tmp_path):
    from app import meal, store
    from app.main import app
    from app.domain import Item

    monkeypatch.setattr(store, "DB", tmp_path / "test.db")
    monkeypatch.setenv("REASONING_PROVIDER", "mock")
    async def ambiguous(_):
        return {"provider":"live", "items":[{"ingredient_id":"egg","amount":3,"unit":"piece"}],
                "uncertain":["杯子里是牛奶还是豆浆？"]}

    async def resolve(text, items, extras, questions):
        assert questions and items[0]["ingredient_id"] == "egg"
        return meal.InventoryChange(intent="inventory", items=[
            Item.model_validate(items[0]), Item(ingredient_id="soy_milk",amount=None,unit="ml"),
            Item(ingredient_id="tomato",amount=300,unit="g")])
    monkeypatch.setattr(meal, "recognize_ingredients", ambiguous)
    monkeypatch.setattr(meal, "interpret", resolve)
    with TestClient(app) as client:
        first = client.post("/api/meal-turn",json={"image_id":"fixture","preferences_version":1}).json()
        assert first["status"] == "clarifying" and not first["result"]
        second = client.post("/api/meal-turn",json={"meal_id":first["id"],"text":"是豆浆，另外有300克番茄","preferences_version":1}).json()
        assert second["status"] == "completed"
        assert store.get("run:"+second["id"])["items"][1]["ingredient_id"] == "soy_milk"
        url = "/api/meals/"+second["id"]+"/select"
        selected = client.post(url,json={"recipe_id":"tomato_egg","preferences_version":1}).json()
        assert selected["content"] == "工具执行完成。"
        trace = store.get("run:"+selected["id"])["events"]
        assert [e["tool"] for e in trace] == ["generate_shopping_list", "analyze_nutrition"]
        assert "shopping" not in selected and "nutrition" not in selected
        assert client.post(url,json={"recipe_id":"unlisted","preferences_version":1}).status_code == 422

def test_selection_sends_both_tool_results_back_before_publishing(monkeypatch,tmp_path):
    import json
    from app import store
    from app.domain import match_recipes, Preferences
    from app.main import app
    from app.llm_client import LLMClient

    monkeypatch.setattr(store,"DB",tmp_path/"state.db")
    monkeypatch.setenv("REASONING_PROVIDER","mock")
    prefs=Preferences()
    recipe=match_recipes([],prefs)["recipes"][0]
    store.put("run:prior",{"id":"prior","status":"completed","preferences":prefs.model_dump(),"preferences_version":1,
                           "items":[],"result":{"recipes":[recipe]}})
    final="模型整理后的答复。"
    async def scripted(self,messages,tools=None):
        outputs={m["tool_call_id"]:json.loads(m["content"]) for m in messages if m["role"]=="tool"}
        if tools and not outputs and messages[-1]["role"]!="user":
            raise AssertionError("missing user request")
        if tools and len(messages)==3:
            return {"role":"assistant","content":"提前结束"}
        if tools:
            names=[t["function"]["name"] for t in reversed(tools)]
            return {"role":"assistant","tool_calls":[{"id":n,"type":"function","function":{"name":n,"arguments":"{}"}} for n in names]}
        assert outputs["analyze_nutrition"]["total"]["kcal"]>0
        assert outputs["generate_shopping_list"][0]["missing"]>0
        return {"role":"assistant","content":final}
    monkeypatch.setattr(LLMClient,"chat",scripted)
    with TestClient(app) as client:
        result=client.post("/api/meals/prior/select",json={"recipe_id":recipe["id"],"preferences_version":1})
        assert result.status_code==200
        assert result.json()["content"]==final

        response = client.post(
            "/api/meals/prior/select",
            json={"recipe_id": recipe["id"], "preferences_version": 1},
            headers={"Accept": "application/x-ndjson"},
        )
        assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [(event["name"], event["status"]) for event in events[:-1]] == [
            ("analyze_nutrition", "running"),
            ("analyze_nutrition", "completed"),
            ("generate_shopping_list", "running"),
            ("generate_shopping_list", "completed"),
        ]
        assert events[-1]["type"] == "result"
        assert events[-1]["result"]["content"] == final
