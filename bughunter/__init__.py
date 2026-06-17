"""bughunter：把报错堆栈交给大模型，自主检索本地代码后给出根因与优化建议。

零第三方依赖；LLM 调用通过可替换的防腐层（LLMClient）走 OpenAI 兼容协议。

快速开始::

    from bughunter import analyze, Settings

    result = analyze(stack_trace, repo_path="/path/to/repo",
                     settings=Settings(base_url="http://10.0.0.5:8000/v1",
                                       model="qwen2.5-coder"))
    print(result.summary)
"""

from __future__ import annotations

from .agent import MaxStepsExceeded, analyze
from .config import Settings
from .llm import ChatResponse, LLMClient, LLMError, ToolCall, UrllibOpenAIClient
from .report import to_dict, to_markdown
from .schema import AnalysisResult, CodeReference, Suggestion

__all__ = [
    "analyze",
    "MaxStepsExceeded",
    "Settings",
    "AnalysisResult",
    "CodeReference",
    "Suggestion",
    "LLMClient",
    "ChatResponse",
    "ToolCall",
    "UrllibOpenAIClient",
    "LLMError",
    "to_markdown",
    "to_dict",
]

__version__ = "0.1.0"
