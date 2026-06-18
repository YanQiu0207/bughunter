"""bughunter：报错诊断、修复提案、确定性应用与测试执行。"""

from __future__ import annotations

from .agent import MaxStepsExceeded, analyze, generate_tests, propose_fix
from .config import Settings
from .llm import ChatResponse, LLMClient, LLMError, ToolCall, UrllibOpenAIClient
from .patch import PatchApplyError, apply_and_test, apply_edits, restore_backup
from .report import (
    command_result_to_markdown,
    fix_proposal_to_markdown,
    test_proposal_to_markdown,
    to_dict,
    to_markdown,
)
from .runner import run_command
from .schema import (
    AnalysisResult,
    ApplyAndTestResult,
    ApplyResult,
    CodeReference,
    CommandResult,
    FileEdit,
    FixProposal,
    ResultParseError,
    Suggestion,
    TestProposal,
    build_fix_proposal,
    build_test_proposal,
)

__all__ = [
    "analyze",
    "propose_fix",
    "generate_tests",
    "apply_edits",
    "run_command",
    "apply_and_test",
    "restore_backup",
    "MaxStepsExceeded",
    "PatchApplyError",
    "ResultParseError",
    "Settings",
    "AnalysisResult",
    "CodeReference",
    "Suggestion",
    "FileEdit",
    "FixProposal",
    "TestProposal",
    "ApplyResult",
    "CommandResult",
    "ApplyAndTestResult",
    "build_fix_proposal",
    "build_test_proposal",
    "LLMClient",
    "ChatResponse",
    "ToolCall",
    "UrllibOpenAIClient",
    "LLMError",
    "to_markdown",
    "fix_proposal_to_markdown",
    "test_proposal_to_markdown",
    "command_result_to_markdown",
    "to_dict",
]

__version__ = "0.1.0"
