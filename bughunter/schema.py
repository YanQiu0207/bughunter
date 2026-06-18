"""结构化结果模型与工具 JSON Schema。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

# 工具名常量
TOOL_READ_FILE = "read_file"
TOOL_GREP_CODE = "grep_code"
TOOL_LIST_DIR = "list_dir"
TOOL_SUBMIT = "submit_analysis"
TOOL_SUBMIT_FIX = "submit_fix"
TOOL_SUBMIT_TESTS = "submit_tests"

Confidence = Literal["high", "medium", "low"]
EditAction = Literal["edit", "create"]
_VALID_CONFIDENCE = get_args(Confidence)
_VALID_EDIT_ACTIONS = get_args(EditAction)


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


@dataclass
class FileEdit:
    """一个待人工确认并由确定性代码应用的文件修改块。"""

    path: str
    action: EditAction
    old_string: str
    new_string: str
    rationale: str


@dataclass
class FixProposal:
    """修复方案；只描述修改，不直接落盘。"""

    summary: str
    edits: list[FileEdit]
    confidence: Confidence


@dataclass
class TestProposal:
    """测试方案；语义上用于新增或修改测试文件。"""

    summary: str
    edits: list[FileEdit]
    confidence: Confidence


@dataclass
class ApplyResult:
    """确定性应用修改后的结果。"""

    applied: list[str]
    skipped: list[str] = field(default_factory=list)
    backup_path: str | None = None


@dataclass
class CommandResult:
    """白名单命令执行结果。"""

    name: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class ApplyAndTestResult:
    """应用修改并执行测试命令后的组合结果。"""

    apply_result: ApplyResult
    command_result: CommandResult | None = None


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

_FILE_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "相对仓库根的文件路径"},
        "action": {"type": "string", "enum": list(_VALID_EDIT_ACTIONS)},
        "old_string": {
            "type": "string",
            "description": "edit 时必须在目标文件中唯一匹配；create 时留空",
        },
        "new_string": {"type": "string", "description": "替换后或新建文件内容"},
        "rationale": {"type": "string", "description": "修改原因，供人工确认"},
    },
    "required": ["path", "action", "old_string", "new_string", "rationale"],
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
                "confidence": {"type": "string", "enum": list(_VALID_CONFIDENCE)},
            },
            "required": ["summary", "root_cause", "suggestions", "confidence"],
        },
    },
}

SUBMIT_FIX_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_SUBMIT_FIX,
        "description": "提交修复方案。只提交修改块，不要直接落盘。",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "修复方案摘要"},
                "edits": {"type": "array", "items": _FILE_EDIT_SCHEMA},
                "confidence": {"type": "string", "enum": list(_VALID_CONFIDENCE)},
            },
            "required": ["summary", "edits", "confidence"],
        },
    },
}

SUBMIT_TESTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_SUBMIT_TESTS,
        "description": "提交测试文件方案。只提交修改块，不要直接落盘。",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "测试方案摘要"},
                "edits": {"type": "array", "items": _FILE_EDIT_SCHEMA},
                "confidence": {"type": "string", "enum": list(_VALID_CONFIDENCE)},
            },
            "required": ["summary", "edits", "confidence"],
        },
    },
}

READ_ONLY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    READ_FILE_TOOL,
    GREP_CODE_TOOL,
    LIST_DIR_TOOL,
]

TOOL_SCHEMAS: list[dict[str, Any]] = READ_ONLY_TOOL_SCHEMAS + [SUBMIT_ANALYSIS_TOOL]
FIX_TOOL_SCHEMAS: list[dict[str, Any]] = READ_ONLY_TOOL_SCHEMAS + [SUBMIT_FIX_TOOL]
TEST_TOOL_SCHEMAS: list[dict[str, Any]] = READ_ONLY_TOOL_SCHEMAS + [SUBMIT_TESTS_TOOL]


# --------------------------------------------------------------------------- #
# 反序列化
# --------------------------------------------------------------------------- #

class ResultParseError(ValueError):
    """模型提交的收口工具参数不符合 schema。"""


def _require(d: dict[str, Any], key: str, types: tuple[type, ...]) -> Any:
    if key not in d:
        raise ResultParseError(f"缺少字段：{key}")
    value = d[key]
    if not isinstance(value, types):
        raise ResultParseError(
            f"字段 {key} 类型错误：期望 {types}，实际 {type(value).__name__}"
        )
    return value


def _build_confidence(arguments: dict[str, Any]) -> Confidence:
    confidence = _require(arguments, "confidence", (str,))
    if confidence not in _VALID_CONFIDENCE:
        raise ResultParseError(
            f"confidence 非法：{confidence!r}，应为 {_VALID_CONFIDENCE}"
        )
    return confidence  # type: ignore[return-value]


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


def _build_file_edit(d: dict[str, Any]) -> FileEdit:
    if not isinstance(d, dict):
        raise ResultParseError("file_edit 必须是对象")
    action = _require(d, "action", (str,))
    if action not in _VALID_EDIT_ACTIONS:
        raise ResultParseError(f"action 非法：{action!r}，应为 {_VALID_EDIT_ACTIONS}")
    old_string = str(_require(d, "old_string", (str,)))
    if action == "edit" and not old_string:
        raise ResultParseError("edit.old_string 不能为空")
    if action == "create" and old_string:
        raise ResultParseError("create.old_string 必须为空")
    return FileEdit(
        path=str(_require(d, "path", (str,))),
        action=action,  # type: ignore[arg-type]
        old_string=old_string,
        new_string=str(_require(d, "new_string", (str,))),
        rationale=str(_require(d, "rationale", (str,))),
    )


def build_result(arguments: dict[str, Any]) -> AnalysisResult:
    """把 submit_analysis 的参数 dict 转成 AnalysisResult。"""
    if not isinstance(arguments, dict):
        raise ResultParseError("submit_analysis 参数必须是对象")

    suggestions_raw = _require(arguments, "suggestions", (list,))
    code_refs_raw = arguments.get("code_references") or []
    if not isinstance(code_refs_raw, list):
        raise ResultParseError("code_references 必须是数组")

    return AnalysisResult(
        summary=str(_require(arguments, "summary", (str,))),
        root_cause=str(_require(arguments, "root_cause", (str,))),
        suggestions=[_build_suggestion(s) for s in suggestions_raw],
        confidence=_build_confidence(arguments),
        code_references=[_build_code_ref(r) for r in code_refs_raw],
    )


def _build_edits(
    arguments: dict[str, Any],
    submit_name: str,
) -> tuple[str, list[FileEdit], Confidence]:
    if not isinstance(arguments, dict):
        raise ResultParseError(f"{submit_name} 参数必须是对象")
    edits_raw = _require(arguments, "edits", (list,))
    return (
        str(_require(arguments, "summary", (str,))),
        [_build_file_edit(e) for e in edits_raw],
        _build_confidence(arguments),
    )


def build_fix_proposal(arguments: dict[str, Any]) -> FixProposal:
    """把 submit_fix 的参数 dict 转成 FixProposal。"""
    summary, edits, confidence = _build_edits(arguments, TOOL_SUBMIT_FIX)
    return FixProposal(summary=summary, edits=edits, confidence=confidence)


def build_test_proposal(arguments: dict[str, Any]) -> TestProposal:
    """把 submit_tests 的参数 dict 转成 TestProposal。"""
    summary, edits, confidence = _build_edits(arguments, TOOL_SUBMIT_TESTS)
    return TestProposal(summary=summary, edits=edits, confidence=confidence)
