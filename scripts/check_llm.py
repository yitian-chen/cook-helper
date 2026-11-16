"""Run from project root: PYTHONPATH=backend .venv/bin/python scripts/check_llm.py"""
import asyncio
import json
from app.llm_client import LLMClient

async def main():
    client = LLMClient()
    assert client.mode == "live", "可用性检查必须使用真实模型"
    reply = await client.chat([{"role": "user", "content": "Reply with OK only."}])
    assert reply.get("content"), "没有文本响应"
    schema = {"type": "function", "function": {"name": "ping", "description": "Connectivity test. Call this tool.",
              "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    messages = [{"role": "user", "content": "Call ping now. Do not respond in text until the tool returns."}]
    reply = await client.chat(messages, [schema])
    calls = reply.get("tool_calls", [])
    assert calls and calls[0]["function"]["name"] == "ping", "原生工具调用未通过"
    messages.append(reply)
    for call in calls:
        json.loads(call["function"]["arguments"])
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": '{"ok":true}'})
    final = await client.chat(messages)
    assert final.get("content"), "工具结果回传失败"
    print(json.dumps({"model": client.model, "text": "PASS", "tool_call": "PASS", "tool_result": "PASS"}))

asyncio.run(main())
