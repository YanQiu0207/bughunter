"""Agent 核心：OpenAI 兼容 function-calling 工具循环 + analyze() 入口。

循环只依赖 LLMClient 接口（防腐层），不感知具体厂商。模型通过反复调用
read_file/grep_code/list_dir 在仓库里定位代码，最终调用 submit_analysis 收口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .llm import ChatResponse, LLMClient, UrllibOpenAIClient
from .schema import (
    TOOL_SCHEMAS,
    TOOL_SUBMIT,
    AnalysisResult,
    ResultParseError,
    build_result,
)
from .tools import CodeTools

SYSTEM_PROMPT = """\
你是一名资深的软件错误诊断专家。用户会给你一段报错堆栈，以及一个可供你检索的代码仓库。

工作要求：
1. 先用 grep_code / read_file / list_dir 在仓库中定位与堆栈相关的真实代码，
   严禁脱离代码凭空臆测根因。
2. 沿着堆栈自顶向下或自底向上定位到出错的具体函数与代码行。
3. 在充分查看代码后，解释问题的根本原因（必须结合具体代码，并引用 file:line）。
4. 给出可执行的优化 / 修复建议。
5. 最终必须调用 submit_analysis 工具提交结构化结论；在此之前不要给最终答案。

注意：所有文件路径都相对仓库根目录。工具结果可能被截断，必要时缩小范围再读。"""


class MaxStepsExceeded(RuntimeError):
    """在 max_steps 内模型仍未调用 submit_analysis 收口。"""


def _assistant_message(resp: ChatResponse) -> dict[str, Any]:
    """把中立响应还原成 OpenAI 格式的 assistant 消息，供下一轮请求携带。"""
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


def analyze(
    stack_trace: str,
    repo_path: str | Path,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    max_steps: int = 12,
) -> AnalysisResult:
    """分析报错堆栈，返回结构化的根因与建议。

    :param stack_trace: 报错堆栈文本。
    :param repo_path: 可供检索的代码仓库根目录。
    :param settings: 运行配置；省略则从环境变量读取（仅在需要构造默认 llm 时使用）。
    :param llm: LLM 客户端（防腐层注入点）；省略则用 UrllibOpenAIClient(settings)。
    :param max_steps: 工具循环最大轮数，防止失控。
    """
    if llm is None:
        llm = UrllibOpenAIClient(settings or Settings.from_env())

    tools = CodeTools(repo_path)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "请分析下面的报错堆栈，仓库已挂载，可用工具检索代码。\n\n"
                "=== 报错堆栈 ===\n" + stack_trace.strip()
            ),
        },
    ]

    for _ in range(max_steps):
        resp = llm.chat(messages, tools=TOOL_SCHEMAS, tool_choice="auto")

        if not resp.tool_calls:
            # 模型没调工具也没收口，明确要求它用 submit_analysis 提交
            messages.append(_assistant_message(resp))
            messages.append(
                {
                    "role": "user",
                    "content": "请在查看代码后调用 submit_analysis 工具提交结构化结论。",
                }
            )
            continue

        messages.append(_assistant_message(resp))

        # 先执行所有普通工具，再处理 submit_analysis，保证消息历史完整。
        # OpenAI 协议要求 assistant 声明的每个 tool_call_id 都有对应 tool 消息。
        submit_tc = None
        for tc in resp.tool_calls:
            if tc.name == TOOL_SUBMIT:
                submit_tc = tc
            else:
                result = tools.dispatch(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
        if submit_tc is not None:
            # 补齐 submit 的 tool 消息，保证历史完整（防止 build_result 异常时重入循环发出非法请求）
            try:
                return build_result(submit_tc.arguments)
            except ResultParseError as exc:
                # 提交的参数不符合 schema：把错误回灌给模型，让其修正后重新提交，
                # 而非直接中断整个分析。受 max_steps 兜底，不会无限循环。
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": submit_tc.id,
                        "content": (
                            f"[提交失败] {exc}。"
                            "请检查参数是否符合 submit_analysis 的 schema 后重新调用。"
                        ),
                    }
                )
                continue

    raise MaxStepsExceeded(
        f"已达到 max_steps={max_steps}，模型仍未调用 {TOOL_SUBMIT} 收口"
    )
