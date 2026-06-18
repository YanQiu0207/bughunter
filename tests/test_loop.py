"""通用工具循环的单元测试。"""

from __future__ import annotations

import unittest
from typing import Any

from bughunter.llm import ChatResponse, ToolCall
from bughunter.loop import MaxStepsExceeded, run_tool_loop
from bughunter.schema import ResultParseError


class FakeLLMClient:
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
        return self._script.pop(0) if self._script else ChatResponse()


def _build(arguments: dict[str, Any]) -> str:
    value = arguments.get("value")
    if not isinstance(value, str):
        raise ResultParseError("value 必须是字符串")
    return value


class RunToolLoopTest(unittest.TestCase):
    def test_dispatches_tool_and_returns_submit_result(self) -> None:
        fake = FakeLLMClient(
            [
                ChatResponse(
                    tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "x"})]
                ),
                ChatResponse(
                    tool_calls=[ToolCall(id="s1", name="submit", arguments={"value": "ok"})]
                ),
            ]
        )
        dispatched: list[str] = []

        def dispatch(name: str, arguments: dict[str, Any]) -> str:
            dispatched.append(f"{name}:{arguments['path']}")
            return "tool-result"

        result = run_tool_loop(
            llm=fake,
            messages=[{"role": "system", "content": "x"}],
            tools=[],
            submit_tool_name="submit",
            build_fn=_build,
            dispatcher=dispatch,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(dispatched, ["read_file:x"])
        self.assertTrue(any(m.get("role") == "tool" for m in fake.calls[1]))

    def test_bad_submit_arguments_are_fed_back(self) -> None:
        fake = FakeLLMClient(
            [
                ChatResponse(tool_calls=[ToolCall(id="s1", name="submit", arguments={})]),
                ChatResponse(
                    tool_calls=[ToolCall(id="s2", name="submit", arguments={"value": "ok"})]
                ),
            ]
        )
        result = run_tool_loop(
            llm=fake,
            messages=[{"role": "system", "content": "x"}],
            tools=[],
            submit_tool_name="submit",
            build_fn=_build,
            dispatcher=lambda _name, _args: "",
        )
        self.assertEqual(result, "ok")
        self.assertTrue(
            any("提交失败" in m.get("content", "") for m in fake.calls[1] if m.get("role") == "tool")
        )

    def test_max_steps_exceeded(self) -> None:
        fake = FakeLLMClient([ChatResponse() for _ in range(5)])
        with self.assertRaises(MaxStepsExceeded):
            run_tool_loop(
                llm=fake,
                messages=[{"role": "system", "content": "x"}],
                tools=[],
                submit_tool_name="submit",
                build_fn=_build,
                dispatcher=lambda _name, _args: "",
                max_steps=2,
            )


if __name__ == "__main__":
    unittest.main()
