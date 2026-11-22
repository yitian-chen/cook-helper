import asyncio
import io
import os
import tempfile
os.environ["COOK_RUNTIME"] = tempfile.mkdtemp(prefix="cook-test-")
os.environ["REASONING_PROVIDER"] = "mock"
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from app.domain import Item, Preferences, match_recipes, RECIPES
from app import store
from app.agent import execute
from app.llm_client import LLMClient

def test_scoring():
    assert len(RECIPES)==12
    items=[Item(ingredient_id=k,amount=a,unit=u) for k,a,u in [
        ("tomato",300,"g"),("egg",2,"piece"),("cooking_oil",10,"g"),("salt",2,"g")]]
    result=match_recipes(items,Preferences())
    assert result["recipes"][0]["id"]=="tomato_egg"
    assert result["recipes"][0]["score"]==100
    blocked=match_recipes(items,Preferences(allergens=["egg"]))
    assert all(all(i["ingredient_id"]!="egg" for i in r["ingredients"]) for r in blocked["recipes"])
    assert any(e["id"]=="tomato_egg" for e in blocked["excluded"])
    result=match_recipes([Item(ingredient_id="tomato",amount=2,unit="piece")],Preferences())
    tomato=next(r for r in result["recipes"] if r["id"]=="tomato_egg")
    assert tomato["details"][0]["status"]=="unknown"
    assert tomato["details"][0]["missing"] is None

def test_flow():
    with TestClient(app) as c:
        b=io.BytesIO();Image.new("RGB",(20,20)).save(b,format="PNG")
        upload=c.post("/api/images",files={"file":("test.png",b.getvalue(),"image/png")})
        assert upload.status_code==200
        run=c.post("/api/runs",json=upload.json()).json()
        run=c.get("/api/runs/"+run["id"]).json()
        assert run["status"]=="awaiting_confirmation"
        endpoint="/api/runs/"+run["id"]+"/confirm-inventory"
        body={"preferences_version":run["preferences_version"],"items":run["items"]}
        assert c.post(endpoint,json={**body,"items":run["items"]*2}).status_code==422
        assert c.post(endpoint,json=body).status_code==200
        result=c.get("/api/runs/"+run["id"]).json()
        assert result["status"]=="completed" and result["result"]["recipes"]
        assert c.post(endpoint,json=body).status_code==409
        assert c.post("/api/images",files={"file":("x.png",b"invalid","image/png")}).status_code==422
        run=c.post("/api/runs",json={}).json()
        prefs=c.get("/api/preferences").json();prefs.pop("version")
        c.put("/api/preferences",json={**prefs,"allergens":["egg"]})
        assert c.get("/api/runs/"+run["id"]).json()["stale"]
        assert c.post("/api/runs/"+run["id"]+"/confirm-inventory",json={"preferences_version":run["preferences_version"],"items":[]}).status_code==409

def test_cancel_and_budget(monkeypatch):
    with TestClient(app) as c:
        run=c.post("/api/runs",json={}).json()
        assert c.post("/api/runs/"+run["id"]+"/cancel").json()["status"]=="cancelled"
        run=c.post("/api/runs",json={}).json()
        async def invalid(self,messages,tools=None):
            return {"role":"assistant","tool_calls":[{"id":"bad","type":"function","function":{"name":"shell","arguments":"{}"}}]}
        monkeypatch.setattr(LLMClient,"chat",invalid)
        asyncio.run(execute(run["id"],"match"))
        result=store.get("run:"+run["id"])
        assert result["status"]=="failed" and len(result["events"])==8

def test_pantry_defaults_respect_explicit_shortages():
    from app.domain import Item, Preferences, match_recipes
    from app.meal_lists import generate_shopping_list

    inventory = [
        Item(ingredient_id="tomato", amount=300, unit="g"),
        Item(ingredient_id="egg", amount=2, unit="piece"),
    ]
    defaults = match_recipes(inventory, Preferences())["recipes"][0]
    assert defaults["id"] == "tomato_egg"
    assert generate_shopping_list(defaults) == []
    assert {row["ingredient_id"] for row in defaults["details"] if row["assumed_pantry"]} == {
        "cooking_oil", "salt",
    }

    inventory.append(Item(ingredient_id="cooking_oil", amount=0, unit="ml"))
    updated = match_recipes(inventory, Preferences())["recipes"][0]
    assert [(row["ingredient_id"], row["missing"]) for row in generate_shopping_list(updated)] == [
        ("cooking_oil", 10),
    ]
