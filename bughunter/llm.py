"""防腐层：把「外部 LLM 服务」隔离在一个厂商无关的接口后面。

- ``ChatResponse`` / ``ToolCall``：中立响应模型，agent 核心循环只认这些。
- ``LLMClient``：标准库 Protocol 定义的接口；任何实现了 ``chat`` 的对象都可注入。
- ``UrllibOpenAIClient``：默认实现，用标准库 urllib 调 OpenAI 兼容
  ``/chat/completions``，并把厂商响应转换成中立模型。这是防腐层的「腐败侧」，
  所有协议细节都封死在这里。

替换实现 = 写一个新类满足 ``LLMClient``（如 httpx 版、openai SDK 版、mock 版），
传给 ``analyze(..., llm=...)`` 即可，agent.py 一行不改。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .config import Settings

# 可重试的 HTTP 状态码：限流 + 服务端临时故障
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class ToolCall:
    """模型请求的一次工具调用（厂商无关）。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    """一次对话返回（厂商无关）。"""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(RuntimeError):
    """调用 LLM 端点失败。"""


@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端接口。核心循环只依赖此协议。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ChatResponse:
        ...


class UrllibOpenAIClient:
    """默认实现：标准库 urllib + OpenAI 兼容协议。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._endpoint = settings.base_url + "/chat/completions"

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": self._settings.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        raw = self._post(payload)
        return self._parse(raw)

    # -- HTTP --------------------------------------------------------------- #

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"

        max_retries = self._settings.max_retries
        for attempt in range(max_retries):
            req = urllib.request.Request(
                self._endpoint, data=data, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self._settings.timeout) as resp:
                    body = resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_STATUS and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避：1s, 2s, 4s ...
                    continue
                try:
                    detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
                except OSError:
                    detail = ""
                raise LLMError(
                    f"LLM 端点返回 HTTP {exc.code}: {detail[:2000]}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(
                    f"无法连接 LLM 端点 {self._endpoint}: {exc.reason}"
                ) from exc

            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise LLMError(f"LLM 响应不是合法 JSON: {body[:2000]}") from exc

        raise LLMError(f"请求 LLM 端点失败，已重试 {max_retries} 次")

    # -- 厂商响应 -> 中立模型 ------------------------------------------------ #

    @staticmethod
    def _parse(raw: dict[str, Any]) -> ChatResponse:
        choices = raw.get("choices")
        if not choices:
            raise LLMError(f"LLM 响应缺少 choices: {json.dumps(raw)[:2000]}")
        message = choices[0].get("message") or {}

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                arguments = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"工具调用 {fn.get('name')!r} 的 arguments 不是合法 JSON: {args_raw[:1000]}"
                ) from exc
            tool_calls.append(
                ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )

        return ChatResponse(content=message.get("content"), tool_calls=tool_calls)
