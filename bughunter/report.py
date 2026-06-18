"""结构化结果的 Markdown 渲染与 JSON 序列化。"""

from __future__ import annotations

import difflib
from dataclasses import asdict

from .schema import (
    AnalysisResult,
    CodeReference,
    CommandResult,
    FileEdit,
    FixProposal,
    TestProposal,
)

_CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}


def _ref_line(ref: CodeReference) -> str:
    return f"`{ref.file}:{ref.line_start}-{ref.line_end}`"


def to_markdown(result: AnalysisResult) -> str:
    """渲染为人类可读的 Markdown 报告。"""
    lines: list[str] = []
    lines.append("# 报错分析报告")
    lines.append("")
    lines.append(f"**结论**：{result.summary}")
    lines.append("")
    lines.append(f"**置信度**：{_CONFIDENCE_LABEL.get(result.confidence, result.confidence)}")
    lines.append("")

    lines.append("## 问题根因")
    lines.append("")
    lines.append(result.root_cause)
    lines.append("")

    if result.code_references:
        lines.append("## 相关代码")
        lines.append("")
        for ref in result.code_references:
            lines.append(f"- {_ref_line(ref)}")
            lines.append("")
            lines.append("  ```")
            for ln in ref.excerpt.splitlines():
                lines.append("  " + ln)
            lines.append("  ```")
        lines.append("")

    lines.append("## 优化建议")
    lines.append("")
    for i, sug in enumerate(result.suggestions, start=1):
        lines.append(f"### {i}. {sug.title}")
        lines.append("")
        lines.append(sug.detail)
        lines.append("")
        for ref in sug.code_refs:
            lines.append(f"- {_ref_line(ref)}")
        if sug.code_refs:
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _edit_diff(edit: FileEdit) -> str:
    old_lines = edit.old_string.splitlines(keepends=True)
    new_lines = edit.new_string.splitlines(keepends=True)
    if not old_lines and edit.action == "create":
        old_lines = []
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{edit.path}",
        tofile=f"b/{edit.path}",
        lineterm="",
    )
    return "\n".join(diff)


def _proposal_to_markdown(proposal: FixProposal | TestProposal, *, title: str) -> str:
    """把修复或测试方案渲染成供人工确认的 Markdown。"""
    lines = [f"# {title}", ""]
    lines.append(f"**摘要**：{proposal.summary}")
    lines.append("")
    lines.append(f"**置信度**：{_CONFIDENCE_LABEL.get(proposal.confidence, proposal.confidence)}")
    lines.append("")
    lines.append("## 修改块")
    lines.append("")
    for index, edit in enumerate(proposal.edits, start=1):
        lines.append(f"### {index}. `{edit.path}` ({edit.action})")
        lines.append("")
        lines.append(f"**原因**：{edit.rationale}")
        lines.append("")
        lines.append("```diff")
        lines.append(_edit_diff(edit))
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def fix_proposal_to_markdown(proposal: FixProposal) -> str:
    """渲染修复方案。"""
    return _proposal_to_markdown(proposal, title="修复方案")


def test_proposal_to_markdown(proposal: TestProposal) -> str:
    """渲染测试方案。"""
    return _proposal_to_markdown(proposal, title="测试方案")


def command_result_to_markdown(result: CommandResult) -> str:
    """渲染命令执行结果。"""
    lines = ["# 命令执行结果", ""]
    lines.append(f"**命令名**：`{result.name}`")
    lines.append(f"**退出码**：`{result.exit_code}`")
    lines.append(f"**是否超时**：{'是' if result.timed_out else '否'}")
    lines.append("")
    if result.stdout:
        lines.append("## stdout")
        lines.append("")
        lines.append("```")
        lines.append(result.stdout.rstrip())
        lines.append("```")
        lines.append("")
    if result.stderr:
        lines.append("## stderr")
        lines.append("")
        lines.append("```")
        lines.append(result.stderr.rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_dict(result: object) -> dict:
    """转成可 json.dumps 的 dict。"""
    return asdict(result)  # type: ignore[arg-type]
