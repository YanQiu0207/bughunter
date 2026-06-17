"""CLI 入口的单元测试（mock analyze，零网络）。"""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from bughunter.schema import AnalysisResult, CodeReference, Suggestion

_FAKE_RESULT = AnalysisResult(
    summary="KeyError：缺少 user 键",
    root_cause="app.py:2 问题",
    code_references=[CodeReference(file="app.py", line_start=1, line_end=2, excerpt="x")],
    suggestions=[Suggestion(title="改用 .get", detail="用 d.get", code_refs=[])],
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
            # exc.code 可能是整数也可能是字符串错误消息
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
            ["--repo", self.repo, "--stack-file", str(self.stack_file), "--format", "json"]
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
                "--repo", self.repo,
                "--stack-file", str(self.stack_file),
                "--base-url", "http://localhost/v1",
                # 故意不传 --model
            ]
        )
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
