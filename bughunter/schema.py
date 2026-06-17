"""结构化结果模型与工具 JSON Schema。

- ``AnalysisResult`` 及其子结构用标准库 dataclass 表达，零依赖。
- 各工具（含收口工具 ``submit_analysis``）的 JSON Schema 在此集中维护，
  供 agent 在请求体里声明 ``tools``。
- ``build_result`` 把模型通过 ``submit_analysis`` 提交的参数转成 ``AnalysisResult``，
  字段缺失或类型不符时抛出清晰错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

# 工具名常量
TOOL_READ_FILE = "read_file"
TOOL_GREP_CODE = "grep_code"
TOOL_LIST_DIR = "list_dir"
TOOL_SUBMIT = "submit_analysis"

Confidence = Literal["high", "medium", "low"]
_VALID_CONFIDENCE = get_args(Confidence)


@dataclass
class CodeReference:
    """一处定位到的相关代码。"""

    file: str
    line_start: int
    line_end: int
    excerpt: str


@dataclass
class Suggestion:
    """一条优化 / 修复建议。"""

    title: str
    detail: str
    code_refs: list[CodeReference] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """最终结构化分析结论。"""

    summary: str
    root_cause: str
    suggestions: list[Suggestion]
    confidence: Confidence
    code_references: list[CodeReference] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# JSON Schema（OpenAI function calling 的 tools 声明）
# --------------------------------------------------------------------------- #

_CODE_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": {"type": "string", "description": "相对仓库根的文件路径"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "excerpt": {"type": "string", "description": "对应代码片段"},
    },
    "required": ["file", "line_start", "line_end", "excerpt"],
}

_SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "detail": {"type": "string", "description": "建议详情，需结合代码"},
        "code_refs": {"type": "array", "items": _CODE_REFERENCE_SCHEMA},
    },
    "required": ["title", "detail"],
}

READ_FILE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_READ_FILE,
        "description": "读取仓库内某个文件的内容，可指定起止行（1-based，含端点）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对仓库根的文件路径"},
                "start": {"type": "integer", "description": "起始行，省略则从头"},
                "end": {"type": "integer", "description": "结束行，省略则到尾"},
            },
            "required": ["path"],
        },
    },
}

GREP_CODE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_GREP_CODE,
        "description": "在仓库内按正则搜索代码，返回命中的 文件:行号:内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python re 正则"},
                "glob": {
                    "type": "string",
                    "description": "可选，限定文件名通配，如 *.py",
                },
            },
            "required": ["pattern"],
        },
    },
}

LIST_DIR_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_LIST_DIR,
        "description": "列出仓库内某个目录的子项，帮助定位代码。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对仓库根的目录路径，省略为根目录",
                },
            },
            "required": [],
        },
    },
}

SUBMIT_ANALYSIS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_SUBMIT,
        "description": (
            "提交最终的结构化分析结论。完成代码定位与分析后，"
            "必须调用本工具收口；调用它即视为分析结束。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "一句话结论"},
                "root_cause": {
                    "type": "string",
                    "description": "问题根因，必须结合具体代码解释",
                },
                "code_references": {
                    "type": "array",
                    "items": _CODE_REFERENCE_SCHEMA,
                    "description": "定位到的相关代码",
                },
                "suggestions": {
                    "type": "array",
                    "items": _SUGGESTION_SCHEMA,
                    "description": "优化 / 修复建议",
                },
                "confidence": {
                    "type": "string",
                    "enum": list(_VALID_CONFIDENCE),
                },
            },
            "required": ["summary", "root_cause", "suggestions", "confidence"],
        },
    },
}

# agent 请求里声明的全部工具
TOOL_SCHEMAS: list[dict[str, Any]] = [
    READ_FILE_TOOL,
    GREP_CODE_TOOL,
    LIST_DIR_TOOL,
    SUBMIT_ANALYSIS_TOOL,
]


# --------------------------------------------------------------------------- #
# 反序列化
# --------------------------------------------------------------------------- #

class ResultParseError(ValueError):
    """模型提交的 submit_analysis 参数不符合 schema。"""


def _require(d: dict[str, Any], key: str, types: tuple[type, ...]) -> Any:
    if key not in d:
        raise ResultParseError(f"缺少字段：{key}")
    value = d[key]
    if not isinstance(value, types):
        raise ResultParseError(
            f"字段 {key} 类型错误：期望 {types}，实际 {type(value).__name__}"
        )
    return value


def _build_code_ref(d: dict[str, Any]) -> CodeReference:
    if not isinstance(d, dict):
        raise ResultParseError("code_reference 必须是对象")
    return CodeReference(
        file=str(_require(d, "file", (str,))),
        line_start=int(_require(d, "line_start", (int,))),
        line_end=int(_require(d, "line_end", (int,))),
        excerpt=str(_require(d, "excerpt", (str,))),
    )


def _build_suggestion(d: dict[str, Any]) -> Suggestion:
    if not isinstance(d, dict):
        raise ResultParseError("suggestion 必须是对象")
    refs_raw = d.get("code_refs") or []
    if not isinstance(refs_raw, list):
        raise ResultParseError("suggestion.code_refs 必须是数组")
    return Suggestion(
        title=str(_require(d, "title", (str,))),
        detail=str(_require(d, "detail", (str,))),
        code_refs=[_build_code_ref(r) for r in refs_raw],
    )


def build_result(arguments: dict[str, Any]) -> AnalysisResult:
    """把 submit_analysis 的参数 dict 转成 AnalysisResult。"""
    if not isinstance(arguments, dict):
        raise ResultParseError("submit_analysis 参数必须是对象")

    confidence = _require(arguments, "confidence", (str,))
    if confidence not in _VALID_CONFIDENCE:
        raise ResultParseError(
            f"confidence 非法：{confidence!r}，应为 {_VALID_CONFIDENCE}"
        )

    suggestions_raw = _require(arguments, "suggestions", (list,))
    code_refs_raw = arguments.get("code_references") or []
    if not isinstance(code_refs_raw, list):
        raise ResultParseError("code_references 必须是数组")

    return AnalysisResult(
        summary=str(_require(arguments, "summary", (str,))),
        root_cause=str(_require(arguments, "root_cause", (str,))),
        suggestions=[_build_suggestion(s) for s in suggestions_raw],
        confidence=confidence,  # type: ignore[arg-type]  # 运行时已校验，静态分析器无法从 get_args 窄化
        code_references=[_build_code_ref(r) for r in code_refs_raw],
    )
