"""novare/llm_client.py — OpenAI 兼容 API 客户端"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("novare.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict = field(default_factory=dict)


def parse_chat_response(raw: dict) -> LLMResponse:
    """解析 OpenAI 兼容的 chat completion 响应"""
    choice = raw["choices"][0]
    message = choice["message"]
    stop_reason = choice.get("finish_reason", "stop")
    content = message.get("content") or ""
    tool_calls = []

    for tc in (message.get("tool_calls") or []):
        func = tc["function"]
        tool_calls.append(ToolCall(
            id=tc["id"],
            name=func["name"],
            arguments=json.loads(func["arguments"]) if isinstance(func["arguments"], str) else func["arguments"],
        ))

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=raw.get("usage", {}),
    )


class LLMClient:
    """OpenAI 兼容 API 客户端（接 MiniMax）"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """调用 chat completion API"""
        body: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        logger.debug("LLM request: model=%s, messages=%d, tools=%d",
                      self.model, len(messages), len(tools or []))

        resp = await self._http.post("/chat/completions", json=body)
        resp.raise_for_status()
        raw = resp.json()

        result = parse_chat_response(raw)
        logger.debug("LLM response: stop_reason=%s, tool_calls=%d, content_len=%d",
                      result.stop_reason, len(result.tool_calls), len(result.content))
        return result

    async def close(self):
        await self._http.aclose()
