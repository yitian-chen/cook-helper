"""Real upload -> vision tool -> confirmation -> recipe matching smoke test."""
import os
import tempfile
os.environ["COOK_RUNTIME"] = tempfile.mkdtemp(prefix="cook-vision-check-")
from fastapi.testclient import TestClient
from app.main import app
from app.domain import ROOT

with TestClient(app) as client:
    health = client.get("/api/health").json()
    assert health["vision_provider"] == "live", "此检查要求真实视觉模型"
    with (ROOT / "kitchen-counter-test.png").open("rb") as image:
        uploaded = client.post("/api/images", files={"file": ("kitchen.png", image, "image/png")})
    uploaded.raise_for_status()
    run = client.post("/api/runs", json=uploaded.json()).json()
    run = client.get("/api/runs/" + run["id"]).json()
    assert run["status"] == "awaiting_confirmation", run.get("error")
    print({"vision_model": run["vision_model"], "items": run["items"], "uncertain": run["uncertain"]}, flush=True)
    expected = {"tomato": 2, "egg": 3, "potato": 2, "carrot": 1, "cucumber": 1}
    actual = {i["ingredient_id"]: i["amount"] for i in run["items"]}
    assert actual == expected, "识别结果与人工标注不一致，请检查"
    response = client.post("/api/runs/" + run["id"] + "/confirm-inventory",
                           json={"preferences_version": run["preferences_version"], "items": run["items"]})
    response.raise_for_status()
    result = client.get("/api/runs/" + run["id"]).json()
    assert result["status"] == "completed", result.get("error")
    print({"status": result["status"], "recipes": [r["name"] for r in result["result"]["recipes"]]})
