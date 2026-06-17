"""把 AnalysisResult 渲染成 Markdown 报告，以及 JSON 序列化。"""

from __future__ import annotations

from dataclasses import asdict

from .schema import AnalysisResult, CodeReference

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


def to_dict(result: AnalysisResult) -> dict:
    """转成可 json.dumps 的 dict。"""
    return asdict(result)
