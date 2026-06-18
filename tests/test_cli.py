"""CLI 入口的单元测试（mock agent，零网络）。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from dataclasses import asdict
from pathlib import Path

from bughunter.config import ENV_ALLOWED_COMMANDS
from bughunter.schema import (
    AnalysisResult,
    ApplyAndTestResult,
    ApplyResult,
    CodeReference,
    CommandResult,
    FileEdit,
    FixProposal,
    Suggestion,
    TestProposal,
)

_FAKE_RESULT = AnalysisResult(
    summary="KeyError：缺少 user 键",
    root_cause="app.py:2 问题",
    code_references=[CodeReference(file="app.py", line_start=1, line_end=2, excerpt="x")],
    suggestions=[Suggestion(title="改用 .get", detail="用 d.get", code_refs=[])],
    confidence="high",
)

_FAKE_FIX = FixProposal(
    summary="修复缺键",
    edits=[FileEdit("app.py", "edit", "a", "b", "r")],
    confidence="high",
)

_FAKE_TESTS = TestProposal(
    summary="覆盖缺键场景",
    edits=[FileEdit("tests/test_app.py", "create", "", "def test_x(): pass\n", "r")],
    confidence="high",
)


def _run(argv: list[str]) -> tuple[int, str]:
    """运行 CLI 并捕获 stdout，返回 (exit_code, output)。"""
    import io
    from bughunter.cli import main

    with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
        try:
            code = main(argv)
        except SystemExit as exc:
            raw = exc.code
            exit_code = int(raw) if isinstance(raw, int) else (0 if not raw else 1)
            return exit_code, mock_out.getvalue()
    return code, mock_out.getvalue()


class CLITest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.stack_file = Path(self._tmp.name) / "trace.txt"
        self.stack_file.write_text("Traceback...\nKeyError: 'user'\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop(ENV_ALLOWED_COMMANDS, None)

    def _with_analyze(self, argv: list[str]) -> tuple[int, str]:
        with unittest.mock.patch("bughunter.cli.analyze", return_value=_FAKE_RESULT):
            return _run(argv)

    def test_stack_file_md_output(self) -> None:
        code, out = self._with_analyze(
            ["--repo", self.repo, "--stack-file", str(self.stack_file)]
        )
        self.assertEqual(code, 0)
        self.assertIn("KeyError", out)

    def test_format_json_output(self) -> None:
        code, out = self._with_analyze(
            [
                "--repo",
                self.repo,
                "--stack-file",
                str(self.stack_file),
                "--format",
                "json",
            ]
        )
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["confidence"], "high")

    def test_empty_stack_file_exits(self) -> None:
        empty = Path(self._tmp.name) / "empty.txt"
        empty.write_text("   ", encoding="utf-8")
        code, _ = _run(["--repo", self.repo, "--stack-file", str(empty)])
        self.assertNotEqual(code, 0)

    def test_only_base_url_without_model_exits(self) -> None:
        code, _ = _run(
            [
                "--repo",
                self.repo,
                "--stack-file",
                str(self.stack_file),
                "--base-url",
                "http://localhost/v1",
            ]
        )
        self.assertNotEqual(code, 0)


class CLINewCommandsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.stack_file = Path(self._tmp.name) / "trace.txt"
        self.stack_file.write_text("Traceback...\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop(ENV_ALLOWED_COMMANDS, None)

    def test_analyze_subcommand_json(self) -> None:
        with unittest.mock.patch("bughunter.cli.analyze", return_value=_FAKE_RESULT):
            code, out = _run(
                ["analyze", "--repo", self.repo, "--stack-file", str(self.stack_file)]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["confidence"], "high")

    def test_propose_fix_subcommand_outputs_json(self) -> None:
        with unittest.mock.patch("bughunter.cli.propose_fix", return_value=_FAKE_FIX):
            code, out = _run(
                [
                    "propose-fix",
                    "--repo",
                    self.repo,
                    "--stack-file",
                    str(self.stack_file),
                ]
            )
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["edits"][0]["path"], "app.py")

    def test_propose_fix_subcommand_passes_previous_outputs(self) -> None:
        analysis_file = Path(self._tmp.name) / "analysis.json"
        analysis_file.write_text(
            json.dumps(
                {
                    "summary": "s",
                    "root_cause": "r",
                    "suggestions": [],
                    "confidence": "high",
                }
            ),
            encoding="utf-8",
        )
        test_output_file = Path(self._tmp.name) / "test-output.txt"
        test_output_file.write_text("FAILED tests/test_app.py", encoding="utf-8")

        def fake_propose_fix(*args, **kwargs):
            self.assertEqual(kwargs["analysis"].summary, "s")
            self.assertEqual(kwargs["test_output"], "FAILED tests/test_app.py")
            return _FAKE_FIX

        with unittest.mock.patch(
            "bughunter.cli.propose_fix",
            side_effect=fake_propose_fix,
        ):
            code, out = _run(
                [
                    "propose-fix",
                    "--repo",
                    self.repo,
                    "--stack-file",
                    str(self.stack_file),
                    "--analysis",
                    str(analysis_file),
                    "--test-output",
                    str(test_output_file),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["summary"], "修复缺键")

    def test_generate_tests_subcommand_uses_previous_proposal(self) -> None:
        proposal_file = Path(self._tmp.name) / "fix.json"
        proposal_file.write_text(
            json.dumps(asdict(_FAKE_FIX)),
            encoding="utf-8",
        )

        def fake_generate_tests(*args, **kwargs):
            self.assertEqual(kwargs["proposal"].summary, "修复缺键")
            return _FAKE_TESTS

        with unittest.mock.patch(
            "bughunter.cli.generate_tests",
            side_effect=fake_generate_tests,
        ):
            code, out = _run(
                [
                    "generate-tests",
                    "--repo",
                    self.repo,
                    "--proposal",
                    str(proposal_file),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["edits"][0]["path"], "tests/test_app.py")

    def test_run_subcommand_uses_command_env_without_llm_env(self) -> None:
        os.environ[ENV_ALLOWED_COMMANDS] = '{"echo": ["python", "-c", "print(123)"]}'
        code, out = _run(["run", "--repo", self.repo, "--name", "echo"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["exit_code"], 0)
        self.assertIn("123", parsed["stdout"])

    def test_apply_and_test_subcommand_outputs_json(self) -> None:
        proposal_file = Path(self._tmp.name) / "fix.json"
        proposal_file.write_text(
            json.dumps(asdict(_FAKE_FIX)),
            encoding="utf-8",
        )
        fake_result = ApplyAndTestResult(
            apply_result=ApplyResult(applied=["app.py"], backup_path=".backup"),
            command_result=CommandResult(
                name="test",
                exit_code=0,
                stdout="ok",
                stderr="",
            ),
        )
        with unittest.mock.patch(
            "bughunter.cli.apply_and_test",
            return_value=fake_result,
        ):
            code, out = _run(
                [
                    "apply-and-test",
                    "--repo",
                    self.repo,
                    "--proposal",
                    str(proposal_file),
                    "--name",
                    "test",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["command_result"]["exit_code"], 0)

    def test_restore_subcommand_calls_restore_backup(self) -> None:
        with unittest.mock.patch("bughunter.cli.restore_backup") as mock_restore:
            code, out = _run(
                [
                    "restore",
                    "--repo",
                    self.repo,
                    "--backup-path",
                    ".bughunter_backups/1",
                ]
            )
        self.assertEqual(code, 0)
        mock_restore.assert_called_once_with(self.repo, ".bughunter_backups/1")
        self.assertTrue(json.loads(out)["restored"])


if __name__ == "__main__":
    unittest.main()
