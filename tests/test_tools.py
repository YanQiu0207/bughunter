"""只读代码工具与沙箱的单元测试（纯本地，零网络）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bughunter.tools import CodeTools, SandboxError


class CodeToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "app.py").write_text(
            "def handler(data):\n"
            "    return data['user']['name']\n"
            "\n"
            "VALUE = 42\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# demo\n", encoding="utf-8")
        # 仓库外的敏感文件，用于验证沙箱（内容哨兵与文件名无关，便于断言内容未泄露）
        self.outside = Path(self._tmp.name).parent / "outside.txt"
        self.outside.write_text("LEAKED_CONTENT_XYZ\n", encoding="utf-8")
        self.tools = CodeTools(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self.outside.exists():
            self.outside.unlink()

    def test_read_file_full(self) -> None:
        out = self.tools.read_file("pkg/app.py")
        self.assertIn("def handler(data):", out)
        self.assertIn("共 4 行", out)

    def test_read_file_range(self) -> None:
        out = self.tools.read_file("pkg/app.py", start=2, end=2)
        self.assertIn("return data['user']['name']", out)
        self.assertNotIn("VALUE = 42", out)

    def test_read_missing_file(self) -> None:
        out = self.tools.read_file("pkg/nope.py")
        self.assertIn("[错误]", out)

    def test_grep_hit(self) -> None:
        out = self.tools.grep_code(r"VALUE\s*=")
        self.assertIn("pkg/app.py:4:", out)

    def test_grep_glob_filter(self) -> None:
        out = self.tools.grep_code("demo", glob="*.md")
        self.assertIn("README.md:1:", out)
        self.assertNotIn("app.py", out)

    def test_grep_no_match(self) -> None:
        out = self.tools.grep_code("zzz_not_present_zzz")
        self.assertIn("[无命中]", out)

    def test_list_dir(self) -> None:
        out = self.tools.list_dir(".")
        self.assertIn("pkg/", out)
        self.assertIn("README.md", out)

    def test_grep_and_list_skip_bughunter_backups(self) -> None:
        backup = self.root / ".bughunter_backups" / "20260618"
        backup.mkdir(parents=True)
        (backup / "old.py").write_text("SECRET_BACKUP_MARKER\n", encoding="utf-8")

        grep_out = self.tools.grep_code("SECRET_BACKUP_MARKER")
        list_out = self.tools.list_dir(".")
        explicit_list = self.tools.list_dir(".bughunter_backups")
        explicit_read = self.tools.read_file(".bughunter_backups/20260618/old.py")

        self.assertIn("[无命中]", grep_out)
        self.assertNotIn(".bughunter_backups", grep_out)
        self.assertNotIn(".bughunter_backups", list_out)
        self.assertIn("[跳过]", explicit_list)
        self.assertIn("[跳过]", explicit_read)

    def test_sandbox_blocks_parent_escape(self) -> None:
        with self.assertRaises(SandboxError):
            self.tools.read_file("../outside.txt")

    def test_dispatch_wraps_sandbox_error(self) -> None:
        out = self.tools.dispatch("read_file", {"path": "../outside.txt"})
        self.assertIn("[拒绝]", out)
        self.assertNotIn("LEAKED_CONTENT_XYZ", out)

    def test_dispatch_unknown_tool(self) -> None:
        out = self.tools.dispatch("rm_rf", {})
        self.assertIn("未知工具", out)

    def test_grep_skips_symlink_file(self) -> None:
        """grep_code 不应读取仓库内指向外部的符号链接文件。"""
        import sys
        if sys.platform == "win32":
            self.skipTest("符号链接在 Windows 上需要特权，跳过")
        import os
        link = self.root / "leak_link.txt"
        try:
            os.symlink(self.outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("无法创建符号链接，跳过")
        out = self.tools.grep_code("LEAKED_CONTENT_XYZ")
        self.assertNotIn("LEAKED_CONTENT_XYZ", out)

    def test_grep_truncation(self) -> None:
        """命中超过 MAX_GREP_MATCHES 时返回截断提示。"""
        from bughunter.tools import MAX_GREP_MATCHES
        # 写一个有大量匹配的文件
        many = "\n".join(f"MARKER_LINE_{i}" for i in range(MAX_GREP_MATCHES + 5))
        (self.root / "big.py").write_text(many, encoding="utf-8")
        tools2 = type(self.tools)(self.root)
        out = tools2.grep_code("MARKER_LINE_")
        self.assertIn("[已截断", out)

    def test_glob_with_path_separator_raises(self) -> None:
        """glob 含路径分隔符时 dispatch 应返回错误字符串而非崩溃。"""
        out = self.tools.dispatch("grep_code", {"pattern": "x", "glob": "sub/*.py"})
        self.assertIn("[错误]", out)
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()
