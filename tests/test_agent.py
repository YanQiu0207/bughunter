"""Agent 阶段入口的单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bughunter.agent import MaxStepsExceeded, analyze, generate_tests, propose_fix
from bughunter.config import ENV_BASE_URL, ENV_MODEL, ENV_SYSTEM_CONTEXT, Settings
from bughunter.llm import ChatResponse, ToolCall
from bughunter.schema import FixProposal, TestProposal


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
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            return ChatResponse(content="(no more script)", tool_calls=[])
        return self._script.pop(0)


def _make_repo() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "app.py").write_text(
        "def get_name(d):\n    return d['user']['name']\n",
        encoding="utf-8",
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

FIX_ARGS = {
    "summary": "用 get 避免 KeyError",
    "edits": [
        {
            "path": "app.py",
            "action": "edit",
            "old_string": "return d['user']['name']",
            "new_string": "return d.get('user', {}).get('name')",
            "rationale": "输入缺 user 时避免 KeyError。",
        }
    ],
    "confidence": "high",
}

TEST_ARGS = {
    "summary": "覆盖缺少 user 的输入",
    "edits": [
        {
            "path": "test_app.py",
            "action": "create",
            "old_string": "",
            "new_string": "import app\n",
            "rationale": "新增回归测试。",
        }
    ],
    "confidence": "medium",
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
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="grep_code",
                        arguments={"pattern": "get_name"},
                    )
                ]
            ),
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c2", name="read_file", arguments={"path": "app.py"})
                ]
            ),
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c3", name="submit_analysis", arguments=SUBMIT_ARGS)
                ]
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
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "app.py"})
                ]
            ),
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c2", name="submit_analysis", arguments=SUBMIT_ARGS)
                ]
            ),
        ]
        fake = FakeLLMClient(script)
        analyze("boom", self.repo, llm=fake)

        second_call_messages = fake.calls[1]
        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("get_name", tool_msgs[0]["content"])

    def test_nudges_when_no_tool_call(self) -> None:
        script = [
            ChatResponse(content="我觉得大概是空指针", tool_calls=[]),
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c1", name="submit_analysis", arguments=SUBMIT_ARGS)
                ]
            ),
        ]
        fake = FakeLLMClient(script)
        result = analyze("boom", self.repo, llm=fake)
        self.assertEqual(result.confidence, "high")
        nudges = [
            m
            for m in fake.calls[1]
            if m.get("role") == "user" and "submit_analysis" in m.get("content", "")
        ]
        self.assertTrue(nudges)

    def test_max_steps_exceeded(self) -> None:
        looping = [
            ChatResponse(
                tool_calls=[
                    ToolCall(
                        id=f"c{i}",
                        name="grep_code",
                        arguments={"pattern": "x"},
                    )
                ]
            )
            for i in range(20)
        ]
        fake = FakeLLMClient(looping)
        with self.assertRaises(MaxStepsExceeded):
            analyze("boom", self.repo, llm=fake, max_steps=3)

    def test_submit_with_bad_args_retries_then_succeeds(self) -> None:
        bad_args = {"summary": "x", "root_cause": "y"}
        script = [
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c1", name="submit_analysis", arguments=bad_args)
                ]
            ),
            ChatResponse(
                tool_calls=[
                    ToolCall(id="c2", name="submit_analysis", arguments=SUBMIT_ARGS)
                ]
            ),
        ]
        fake = FakeLLMClient(script)
        result = analyze("boom", self.repo, llm=fake)
        self.assertEqual(result.confidence, "high")
        tool_msgs = [m for m in fake.calls[1] if m.get("role") == "tool"]
        self.assertTrue(any("提交失败" in m.get("content", "") for m in tool_msgs))

    def test_submit_always_bad_args_eventually_max_steps(self) -> None:
        bad_args = {"summary": "x"}
        script = [
            ChatResponse(
                tool_calls=[
                    ToolCall(id=f"c{i}", name="submit_analysis", arguments=bad_args)
                ]
            )
            for i in range(20)
        ]
        fake = FakeLLMClient(script)
        with self.assertRaises(MaxStepsExceeded):
            analyze("boom", self.repo, llm=fake, max_steps=3)


class ProposalAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _make_repo()
        self.repo = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for key in (ENV_BASE_URL, ENV_MODEL, ENV_SYSTEM_CONTEXT):
            os.environ.pop(key, None)

    def test_propose_fix_returns_fix_proposal(self) -> None:
        fake = FakeLLMClient(
            [
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="grep_code",
                            arguments={"pattern": "get_name"},
                        )
                    ]
                ),
                ChatResponse(
                    tool_calls=[ToolCall(id="c2", name="submit_fix", arguments=FIX_ARGS)]
                ),
            ]
        )
        result = propose_fix("KeyError: 'user'", self.repo, llm=fake)
        self.assertIsInstance(result, FixProposal)
        self.assertEqual(result.edits[0].path, "app.py")
        self.assertIn("get", result.edits[0].new_string)

    def test_generate_tests_includes_system_context(self) -> None:
        fake = FakeLLMClient(
            [ChatResponse(tool_calls=[ToolCall(id="c1", name="submit_tests", arguments=TEST_ARGS)])]
        )
        settings = Settings(
            base_url="http://localhost/v1",
            model="m",
            system_context="使用 python -m unittest 运行测试。",
        )
        result = generate_tests(self.repo, settings=settings, llm=fake)
        self.assertIsInstance(result, TestProposal)
        first_user = [m for m in fake.calls[0] if m.get("role") == "user"][0]
        self.assertIn("python -m unittest", first_user["content"])

    def test_generate_tests_reads_system_context_from_env_without_llm(self) -> None:
        os.environ[ENV_BASE_URL] = "http://localhost/v1"
        os.environ[ENV_MODEL] = "m"
        os.environ[ENV_SYSTEM_CONTEXT] = "env integration guide"
        fake = FakeLLMClient(
            [ChatResponse(tool_calls=[ToolCall(id="c1", name="submit_tests", arguments=TEST_ARGS)])]
        )
        generate_tests(self.repo, llm=fake)
        first_user = [m for m in fake.calls[0] if m.get("role") == "user"][0]
        self.assertIn("env integration guide", first_user["content"])

    def test_generate_tests_reads_context_env_when_llm_injected_without_endpoint(
        self,
    ) -> None:
        os.environ[ENV_SYSTEM_CONTEXT] = "context only"
        fake = FakeLLMClient(
            [
                ChatResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="submit_tests", arguments=TEST_ARGS)
                    ]
                )
            ]
        )
        generate_tests(self.repo, llm=fake)
        first_user = [m for m in fake.calls[0] if m.get("role") == "user"][0]
        self.assertIn("context only", first_user["content"])

    def test_generate_tests_ignores_invalid_llm_env_when_llm_injected(self) -> None:
        os.environ[ENV_BASE_URL] = "not-a-url"
        os.environ[ENV_MODEL] = "m"
        os.environ[ENV_SYSTEM_CONTEXT] = "context survives"
        fake = FakeLLMClient(
            [
                ChatResponse(
                    tool_calls=[
                        ToolCall(id="c1", name="submit_tests", arguments=TEST_ARGS)
                    ]
                )
            ]
        )
        generate_tests(self.repo, llm=fake)
        first_user = [m for m in fake.calls[0] if m.get("role") == "user"][0]
        self.assertIn("context survives", first_user["content"])


if __name__ == "__main__":
    unittest.main()
