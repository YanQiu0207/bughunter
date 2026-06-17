"""Agent 工具循环的单元测试。

用实现了 LLMClient 接口的 FakeLLMClient 脚本化 tool_calls 序列，
全程零网络，同时验证防腐层注入点可用。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from bughunter.agent import MaxStepsExceeded, analyze
from bughunter.llm import ChatResponse, ToolCall


class FakeLLMClient:
    """按预设脚本逐次返回 ChatResponse；记录每次收到的 messages。"""

    def __init__(self, script: list[ChatResponse]) -> None:
        self._script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ChatResponse:
        # 记录一份快照，便于断言工具结果已回灌
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            # 脚本耗尽却仍被调用：返回空响应触发收口提示
            return ChatResponse(content="(no more script)", tool_calls=[])
        return self._script.pop(0)


def _make_repo() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "app.py").write_text(
        "def get_name(d):\n    return d['user']['name']\n", encoding="utf-8"
    )
    return tmp


SUBMIT_ARGS = {
    "summary": "KeyError：缺少 user 键",
    "root_cause": "app.py:2 直接索引 d['user']，输入缺该键时抛 KeyError。",
    "code_references": [
        {
            "file": "app.py",
            "line_start": 1,
            "line_end": 2,
            "excerpt": "def get_name(d):\n    return d['user']['name']",
        }
    ],
    "suggestions": [
        {
            "title": "改用 .get 并校验",
            "detail": "用 d.get('user', {}).get('name') 或先校验键存在。",
            "code_refs": [],
        }
    ],
    "confidence": "high",
}


class AnalyzeLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_repo()
        self.repo = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_loop_returns_structured_result(self) -> None:
        script = [
            ChatResponse(
                tool_calls=[ToolCall(id="c1", name="grep_code", arguments={"pattern": "get_name"})]
            ),
            ChatResponse(
                tool_calls=[ToolCall(id="c2", name="read_file", arguments={"path": "app.py"})]
            ),
            ChatResponse(
                tool_calls=[ToolCall(id="c3", name="submit_analysis", arguments=SUBMIT_ARGS)]
            ),
        ]
        fake = FakeLLMClient(script)
        result = analyze("Traceback ... KeyError: 'user'", self.repo, llm=fake)

        self.assertEqual(result.confidence, "high")
        self.assertIn("KeyError", result.summary)
        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(result.code_references[0].file, "app.py")

    def test_tool_results_fed_back_to_model(self) -> None:
        script = [
            ChatResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "app.py"})]
            ),
            ChatResponse(
                tool_calls=[ToolCall(id="c2", name="submit_analysis", arguments=SUBMIT_ARGS)]
            ),
        ]
        fake = FakeLLMClient(script)
        analyze("boom", self.repo, llm=fake)

        # 第二次调用时，messages 里应已包含 read_file 的工具结果
        second_call_messages = fake.calls[1]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("get_name", tool_msgs[0]["content"])

    def test_nudges_when_no_tool_call(self) -> None:
        script = [
            ChatResponse(content="我觉得大概是空指针", tool_calls=[]),  # 没调工具
            ChatResponse(
                tool_calls=[ToolCall(id="c1", name="submit_analysis", arguments=SUBMIT_ARGS)]
            ),
        ]
        fake = FakeLLMClient(script)
        result = analyze("boom", self.repo, llm=fake)
        self.assertEqual(result.confidence, "high")
        # 第二次请求里应有催促 submit 的 user 消息
        nudges = [
            m for m in fake.calls[1]
            if m.get("role") == "user" and "submit_analysis" in m.get("content", "")
        ]
        self.assertTrue(nudges)

    def test_max_steps_exceeded(self) -> None:
        # 永远只 grep、从不收口
        looping = [
            ChatResponse(
                tool_calls=[ToolCall(id=f"c{i}", name="grep_code", arguments={"pattern": "x"})]
            )
            for i in range(20)
        ]
        fake = FakeLLMClient(looping)
        with self.assertRaises(MaxStepsExceeded):
            analyze("boom", self.repo, llm=fake, max_steps=3)

    def test_submit_with_bad_args_retries_then_succeeds(self) -> None:
        """build_result 失败时回灌错误，模型修正后重新提交成功。"""
        bad_args = {"summary": "x", "root_cause": "y"}  # 缺 suggestions / confidence
        script = [
            ChatResponse(
                tool_calls=[ToolCall(id="c1", name="submit_analysis", arguments=bad_args)]
            ),
            ChatResponse(
                tool_calls=[ToolCall(id="c2", name="submit_analysis", arguments=SUBMIT_ARGS)]
            ),
        ]
        fake = FakeLLMClient(script)
        result = analyze("boom", self.repo, llm=fake)
        self.assertEqual(result.confidence, "high")
        # 第二次请求的 messages 里应有回灌的错误提示
        tool_msgs = [
            m for m in fake.calls[1] if m.get("role") == "tool"
        ]
        self.assertTrue(any("提交失败" in m.get("content", "") for m in tool_msgs))

    def test_submit_always_bad_args_eventually_max_steps(self) -> None:
        """模型反复提交脏数据，最终触发 MaxStepsExceeded 而非无限循环。"""
        bad_args = {"summary": "x"}  # 持续缺字段
        script = [
            ChatResponse(
                tool_calls=[ToolCall(id=f"c{i}", name="submit_analysis", arguments=bad_args)]
            )
            for i in range(20)
        ]
        fake = FakeLLMClient(script)
        with self.assertRaises(MaxStepsExceeded):
            analyze("boom", self.repo, llm=fake, max_steps=3)


if __name__ == "__main__":
    unittest.main()
