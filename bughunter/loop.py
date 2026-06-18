"""通用 OpenAI function-calling 工具循环。"""

from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

from .llm import ChatResponse, LLMClient
from .schema import ResultParseError

_T = TypeVar("_T")


class MaxStepsExceeded(RuntimeError):
    """在 max_steps 内模型仍未调用指定收口工具。"""


def _assistant_message(resp: ChatResponse) -> dict[str, Any]:
    """把中立响应还原成 OpenAI 格式的 assistant 消息。"""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": resp.content or "",
    }
    if resp.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in resp.tool_calls
        ]
    return msg


def run_tool_loop(
    *,
    llm: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    submit_tool_name: str,
    build_fn: Callable[[dict[str, Any]], _T],
    dispatcher: Callable[[str, dict[str, Any]], str],
    max_steps: int = 12,
    nudge_message: str | None = None,
) -> _T:
    """运行通用工具循环，直到模型调用收口工具并通过校验。

    Args:
        llm: LLM 客户端。
        messages: 初始消息；函数会在该列表上追加对话历史。
        tools: OpenAI function-calling 工具 schema。
        submit_tool_name: 收口工具名。
        build_fn: 将收口工具参数转换为结构化结果的函数。
        dispatcher: 普通工具分发函数。
        max_steps: 最大循环轮数。
        nudge_message: 模型未调用工具时追加的提示。

    Returns:
        build_fn 构造出的结构化结果。

    Raises:
        MaxStepsExceeded: 达到最大轮数仍未成功收口。
    """
    prompt = nudge_message or f"请调用 {submit_tool_name} 工具提交结构化结论。"

    for _ in range(max_steps):
        resp = llm.chat(messages, tools=tools, tool_choice="auto")

        if not resp.tool_calls:
            messages.append(_assistant_message(resp))
            messages.append({"role": "user", "content": prompt})
            continue

        messages.append(_assistant_message(resp))

        submit_tc = None
        for tc in resp.tool_calls:
            if tc.name == submit_tool_name:
                submit_tc = tc
                continue
            result = dispatcher(tc.name, tc.arguments)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

        if submit_tc is not None:
            try:
                return build_fn(submit_tc.arguments)
            except ResultParseError as exc:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": submit_tc.id,
                        "content": (
                            f"[提交失败] {exc}。"
                            f"请检查参数是否符合 {submit_tool_name} 的 schema 后重新调用。"
                        ),
                    }
                )
                continue

    raise MaxStepsExceeded(
        f"已达到 max_steps={max_steps}，模型仍未调用 {submit_tool_name} 收口"
    )
