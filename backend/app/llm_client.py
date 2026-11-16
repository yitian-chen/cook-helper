"""OpenAI-compatible chat and native tool calling. Never log credentials."""
import asyncio
import os
from pathlib import Path
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "docs/.env")

class LLMError(RuntimeError):
    pass

class LLMClient:
    def __init__(self):
        self.key = os.getenv("REASONING_API_KEY") or os.getenv("API_KEY", "")
        url = os.getenv("REASONING_BASE_URL") or os.getenv("API_URL", "")
        self.url = url.rstrip("/")
        if not self.url.endswith("/chat/completions"):
            self.url += "/chat/completions"
        self.model = os.getenv("REASONING_MODEL", "deepseek-v4.1-flash")
        self.mode = os.getenv("REASONING_PROVIDER", "live")
        if self.mode == "live" and (not self.key or not url):
            raise LLMError("缺少 API_KEY/API_URL，请配置 .env 或选择 mock 模式")

    async def chat(self, messages, tools=None):
        if self.mode == "mock":
            if tools:
                import json
                name = tools[0]["function"]["name"]
                return {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "mock-call", "type": "function",
                    "function": {"name": name, "arguments": json.dumps({})}}]}
            return {"role": "assistant", "content": "工具执行完成。"}
        body = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            body.update(tools=tools, tool_choice="auto")
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(3):
                try:
                    response = await client.post(self.url, headers={"Authorization": f"Bearer {self.key}"}, json=body)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                    if response.is_error:
                        raise LLMError(f"模型服务 HTTP {response.status_code}（响应正文已隐藏）")
                    message = response.json()["choices"][0]["message"]
                    if not isinstance(message, dict):
                        raise ValueError()
                    # Only preserve protocol fields; don't persist provider reasoning.
                    return {k: v for k, v in message.items() if k in {"role", "content", "tool_calls"}}
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 2:
                        raise LLMError("模型网络请求超时或连接失败") from None
                    await asyncio.sleep(2 ** attempt)
                except (KeyError, ValueError, IndexError):
                    raise LLMError("模型响应格式无效") from None
