"""Agent 阶段入口：分析、修复提案与测试提案。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .config import ENV_SYSTEM_CONTEXT, Settings
from .llm import LLMClient, UrllibOpenAIClient
from .loop import MaxStepsExceeded, run_tool_loop
from .schema import (
    FIX_TOOL_SCHEMAS,
    TEST_TOOL_SCHEMAS,
    TOOL_SCHEMAS,
    TOOL_SUBMIT,
    TOOL_SUBMIT_FIX,
    TOOL_SUBMIT_TESTS,
    AnalysisResult,
    FixProposal,
    TestProposal,
    build_fix_proposal,
    build_result,
    build_test_proposal,
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

FIX_PROMPT = """\
你是一名资深的软件修复专家。用户会给你一段报错堆栈，以及一个可供你只读检索的代码仓库。

工作要求：
1. 先用 grep_code / read_file / list_dir 定位真实代码，严禁不看代码就给修改。
2. 只产出修复方案，不要声称已经修改文件。
3. 每个修改必须使用 old_string 唯一匹配 + new_string 替换；新增文件使用 action=create 且 old_string 为空。
4. 修改块要尽量小，方便人工确认和确定性应用。
5. 最终必须调用 submit_fix 工具提交结构化修复方案。"""

TEST_PROMPT = """\
你是一名资深的软件测试工程师。你需要在只读检索仓库后，为修复方案生成回归测试或集成测试文件方案。

工作要求：
1. 先用 grep_code / read_file / list_dir 查看现有测试风格和项目结构。
2. 只产出测试修改方案，不要声称已经修改文件。
3. 每个修改必须使用 old_string 唯一匹配 + new_string 替换；新增文件使用 action=create 且 old_string 为空。
4. 测试应覆盖修复的关键路径。
5. 最终必须调用 submit_tests 工具提交结构化测试方案。"""


def _build_llm(settings: Settings | None, llm: LLMClient | None) -> LLMClient:
    if llm is not None:
        return llm
    return UrllibOpenAIClient(settings or Settings.from_env())


def _json_context(value: Any) -> str:
    if value is None:
        return ""
    if is_dataclass(value):
        return json.dumps(asdict(value), ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2)


def analyze(
    stack_trace: str,
    repo_path: str | Path,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    max_steps: int = 12,
) -> AnalysisResult:
    """分析报错堆栈，返回结构化的根因与建议。"""
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
    return run_tool_loop(
        llm=_build_llm(settings, llm),
        messages=messages,
        tools=TOOL_SCHEMAS,
        submit_tool_name=TOOL_SUBMIT,
        build_fn=build_result,
        dispatcher=tools.dispatch,
        max_steps=max_steps,
        nudge_message="请在查看代码后调用 submit_analysis 工具提交结构化结论。",
    )


def propose_fix(
    stack_trace: str,
    repo_path: str | Path,
    *,
    analysis: AnalysisResult | dict[str, Any] | None = None,
    test_output: str | None = None,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    max_steps: int = 12,
) -> FixProposal:
    """只读检索仓库并产出修复方案，不落盘。"""
    tools = CodeTools(repo_path)
    context_parts = [
        "请基于下面的报错堆栈生成修复方案。",
        "",
        "=== 报错堆栈 ===",
        stack_trace.strip(),
    ]
    if analysis is not None:
        context_parts.extend(["", "=== 已有分析结果 ===", _json_context(analysis)])
    if test_output:
        context_parts.extend(["", "=== 上一轮测试输出 ===", test_output.strip()])

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": FIX_PROMPT},
        {"role": "user", "content": "\n".join(context_parts)},
    ]
    return run_tool_loop(
        llm=_build_llm(settings, llm),
        messages=messages,
        tools=FIX_TOOL_SCHEMAS,
        submit_tool_name=TOOL_SUBMIT_FIX,
        build_fn=build_fix_proposal,
        dispatcher=tools.dispatch,
        max_steps=max_steps,
        nudge_message="请在查看代码后调用 submit_fix 工具提交结构化修复方案。",
    )


def generate_tests(
    repo_path: str | Path,
    *,
    proposal: FixProposal | TestProposal | dict[str, Any] | None = None,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    max_steps: int = 12,
) -> TestProposal:
    """只读检索仓库并产出测试文件方案，不落盘。"""
    effective_settings = settings
    if effective_settings is None and llm is None:
        try:
            effective_settings = Settings.from_env()
        except RuntimeError:
            raise
    system_context = (
        effective_settings.system_context
        if effective_settings
        else os.environ.get(ENV_SYSTEM_CONTEXT, "")
    )
    context_parts = ["请为当前修复生成回归测试或集成测试方案。"]
    if proposal is not None:
        context_parts.extend(["", "=== 修复方案 ===", _json_context(proposal)])
    if system_context:
        context_parts.extend(["", "=== 系统启动 / 集成测试说明 ===", system_context])

    tools = CodeTools(repo_path)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": TEST_PROMPT},
        {"role": "user", "content": "\n".join(context_parts)},
    ]
    return run_tool_loop(
        llm=_build_llm(effective_settings, llm),
        messages=messages,
        tools=TEST_TOOL_SCHEMAS,
        submit_tool_name=TOOL_SUBMIT_TESTS,
        build_fn=build_test_proposal,
        dispatcher=tools.dispatch,
        max_steps=max_steps,
        nudge_message="请在查看代码后调用 submit_tests 工具提交结构化测试方案。",
    )
