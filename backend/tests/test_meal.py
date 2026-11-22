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
        assert second["items"][1]["ingredient_id"] == "soy_milk"
        url = "/api/meals/"+second["id"]+"/select"
        selected = client.post(url,json={"recipe_id":"tomato_egg","preferences_version":1}).json()
        assert {d["ingredient_id"] for d in selected["shopping"]} == {"cooking_oil","salt"}
        assert selected["nutrition"]["unavailable"] == []
        assert selected["nutrition"]["total"]["kcal"] == 285.4
        assert client.post(url,json={"recipe_id":"unlisted","preferences_version":1}).status_code == 422
