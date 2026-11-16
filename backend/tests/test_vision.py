import asyncio
import json
import uuid
import pytest
from app import store
from app.llm_client import LLMClient, LLMError
from app.vision import parse_recognition, recognize_ingredients

@pytest.mark.parametrize("content", [
    '{"items":[{"ingredient_id":"unknown","amount":1,"unit":"piece"}]}',
    '{"items":[{"ingredient_id":"tomato","amount":300,"unit":"g"}]}',
    '{"items":[{"ingredient_id":"egg","amount":-1,"unit":"piece"}]}',
    '{"items":[{"ingredient_id":"egg","amount":1,"unit":"piece"},{"ingredient_id":"egg","amount":2,"unit":"piece"}]}',
    'not JSON', None
])
def test_invalid_results(content):
    with pytest.raises(LLMError):
        parse_recognition(content)

def test_empty_and_uncertain():
    result = parse_recognition('{"items":[],"uncertain":["遮挡无法判断"]}')
    assert result.items == [] and result.uncertain

def test_actual_image_payload(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "live")
    monkeypatch.setenv("VISION_API_KEY", "test-only")
    monkeypatch.setenv("VISION_BASE_URL", "https://example.invalid/v1")
    image_id = str(uuid.uuid4())
    store.put("image:" + image_id, True)
    (store.RUNTIME / (image_id + ".jpg")).write_bytes(b"test-image-bytes")
    async def fake(self, messages, tools=None):
        assert self.mode == "live"
        data = messages[1]["content"][1]
        assert data["type"] == "image_url"
        assert data["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return {"content": json.dumps({"items": [{"ingredient_id":"egg","amount":3,"unit":"piece"}]})}
    monkeypatch.setattr(LLMClient, "chat", fake)
    result = asyncio.run(recognize_ingredients(image_id))
    assert result["provider"] == "live" and result["items"][0]["amount"] == 3
    with pytest.raises(LLMError):
        asyncio.run(recognize_ingredients("../../.env"))

def test_service_failure_is_not_mock(monkeypatch):
    monkeypatch.setenv("VISION_PROVIDER", "live")
    monkeypatch.setenv("VISION_API_KEY", "test-only")
    monkeypatch.setenv("VISION_BASE_URL", "https://example.invalid/v1")
    image_id = str(uuid.uuid4())
    store.put("image:" + image_id, True)
    (store.RUNTIME / (image_id + ".jpg")).write_bytes(b"test")
    async def fail(*args, **kwargs):
        raise LLMError("模型服务 HTTP 500")
    monkeypatch.setattr(LLMClient, "chat", fail)
    with pytest.raises(LLMError, match="500"):
        asyncio.run(recognize_ingredients(image_id))
