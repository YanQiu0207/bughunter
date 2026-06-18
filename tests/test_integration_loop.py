"""闭环流程集成测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from bughunter import Settings, apply_edits, propose_fix, run_command
from bughunter.llm import ChatResponse, ToolCall


class FakeLLMClient:
    def __init__(self, script: list[ChatResponse]) -> None:
        self._script = list(script)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ChatResponse:
        return self._script.pop(0)


class ClosedLoopIntegrationTest(unittest.TestCase):
    def test_propose_apply_run_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(
                "def get_name(d):\n    return d['user']['name']\n",
                encoding="utf-8",
            )
            (root / "test_app.py").write_text(
                "import unittest\n"
                "import app\n\n"
                "class AppTest(unittest.TestCase):\n"
                "    def test_missing_user(self):\n"
                "        self.assertIsNone(app.get_name({}))\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
                encoding="utf-8",
            )
            proposal_args = {
                "summary": "修复缺少 user 的输入",
                "edits": [
                    {
                        "path": "app.py",
                        "action": "edit",
                        "old_string": "return d['user']['name']",
                        "new_string": "return d.get('user', {}).get('name')",
                        "rationale": "缺少 user 时返回 None。",
                    }
                ],
                "confidence": "high",
            }
            fake = FakeLLMClient(
                [
                    ChatResponse(
                        tool_calls=[
                            ToolCall(
                                id="c1",
                                name="submit_fix",
                                arguments=proposal_args,
                            )
                        ]
                    )
                ]
            )
            proposal = propose_fix("KeyError: 'user'", root, llm=fake)
            apply_edits(proposal, root)
            settings = Settings(
                base_url="http://localhost/v1",
                model="m",
                allowed_commands={"test": [sys.executable, "-m", "unittest"]},
            )
            result = run_command("test", root, settings)
            self.assertEqual(result.exit_code, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
