"""白名单命令执行测试。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
import unittest.mock

from bughunter.config import Settings
from bughunter.runner import _kill_process_tree, run_command


class RunCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _settings(self, **kwargs: object) -> Settings:
        return Settings(base_url="http://localhost/v1", model="m", **kwargs)

    def test_runs_allowed_command(self) -> None:
        settings = self._settings(
            allowed_commands={"echo": [sys.executable, "-c", "print('hello')"]}
        )
        result = run_command("echo", self.repo, settings)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)
        self.assertFalse(result.timed_out)

    def test_rejects_unknown_command(self) -> None:
        settings = self._settings(allowed_commands={})
        with self.assertRaises(ValueError):
            run_command("test", self.repo, settings)

    def test_timeout(self) -> None:
        settings = self._settings(
            allowed_commands={
                "sleep": [sys.executable, "-c", "import time; time.sleep(1)"]
            },
            command_timeout=0.01,
        )
        result = run_command("sleep", self.repo, settings)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, 124)

    def test_large_output_is_truncated(self) -> None:
        settings = self._settings(
            allowed_commands={
                "spam": [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('x' * 70000)",
                ]
            }
        )
        result = run_command("spam", self.repo, settings)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("已截断", result.stdout)
        self.assertLess(len(result.stdout), 61000)

    def test_timeout_kills_child_process_tree_quickly(self) -> None:
        settings = self._settings(
            allowed_commands={
                "spawn": [
                    sys.executable,
                    "-c",
                    (
                        "import subprocess, sys, time; "
                        "subprocess.Popen([sys.executable, '-c', "
                        "\"import time; time.sleep(5)\"]); "
                        "time.sleep(5)"
                    ),
                ]
            },
            command_timeout=0.1,
        )
        start = time.monotonic()
        result = run_command("spawn", self.repo, settings)
        elapsed = time.monotonic() - start
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 2.0)

    def test_kill_process_tree_when_taskkill_and_kill_fail_returns_warning(
        self,
    ) -> None:
        class FakeProc:
            pid = 123

            def kill(self) -> None:
                raise OSError("kill failed")

        with unittest.mock.patch("sys.platform", "win32"):
            with unittest.mock.patch(
                "subprocess.run",
                side_effect=OSError("taskkill missing"),
            ):
                warning = _kill_process_tree(FakeProc())  # type: ignore[arg-type]

        self.assertIn("taskkill 启动失败", warning)
        self.assertIn("kill failed", warning)


if __name__ == "__main__":
    unittest.main()
