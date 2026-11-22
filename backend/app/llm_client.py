"""OpenAI-compatible chat and native tool calling. Never log credentials."""
import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "docs/.env")

def record_exchange(request, response=None, error=None):
    """Replace the local snapshot atomically after each finished HTTP attempt."""
    request_text = request.content.decode("utf-8")
    response_text = response.text if response is not None else ""
    longest = max((len(run) for body in (request_text, response_text)
                   for run in re.findall(r"`{3,}", body)), default=2)
    fence = "`" * (longest + 1)
    status = f"HTTP {response.status_code}" if response is not None else f"{error}（未收到响应体）"
    timestamp = datetime.now(timezone.utc).isoformat()
    snapshot = (
        f"# 最近一次 LLM 调用\n\n完成时间（UTC） {timestamp}\n\n结果 {status}\n\n"
        f"## 请求体\n\n{fence}json\n{request_text}\n{fence}\n\n"
        f"## 返回体\n\n{fence}\n{response_text}\n{fence}\n"
    )
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT,
                                         prefix=".llm-debug-", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(snapshot)
        os.replace(temp_path, ROOT / "LLM_DEBUG.md")
    except OSError:
        logging.getLogger(__name__).exception("无法写入 LLM_DEBUG.md")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class LLMError(RuntimeError):
    pass

class LLMClient:
    def __init__(self, purpose="reasoning"):
        self.key = os.getenv("REASONING_API_KEY") or os.getenv("API_KEY", "")
        url = os.getenv("REASONING_BASE_URL") or os.getenv("API_URL", "")
        if purpose == "vision":
            self.key = os.getenv("VISION_API_KEY") or self.key
            url = os.getenv("VISION_BASE_URL") or url
        self.url = url.rstrip("/")
        if not self.url.endswith("/chat/completions"):
            self.url += "/chat/completions"
        self.model = os.getenv("REASONING_MODEL", "qwen3.8-flash")
        self.mode = os.getenv("REASONING_PROVIDER", "live")
        if purpose == "vision":
            self.model = os.getenv("VISION_MODEL") or self.model
            self.mode = os.getenv("VISION_PROVIDER") or self.mode
        if self.mode not in ("live", "mock"):
            raise LLMError("模型 provider 必须是 live 或 mock")
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
                request = client.build_request("POST", self.url, headers={"Authorization": f"Bearer {self.key}"}, json=body)
                try:
                    response = await client.send(request)
                    record_exchange(request, response)
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
                except httpx.RequestError as exc:
                    record_exchange(request, error=type(exc).__name__)
                    if attempt == 2:
                        raise LLMError("模型网络请求超时或连接失败") from None
                    await asyncio.sleep(2 ** attempt)
                except (KeyError, ValueError, IndexError):
                    raise LLMError("模型响应格式无效") from None
