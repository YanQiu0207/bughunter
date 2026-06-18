"""确定性补丁应用测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from bughunter.config import Settings
from bughunter.patch import PatchApplyError, apply_and_test, apply_edits, restore_backup
from bughunter.schema import FileEdit, FixProposal


class ApplyEditsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_edit_unique_match_and_backup(self) -> None:
        proposal = FixProposal(
            summary="change value",
            edits=[
                FileEdit(
                    path="app.py",
                    action="edit",
                    old_string="value = 1",
                    new_string="value = 2",
                    rationale="update value",
                )
            ],
            confidence="high",
        )
        result = apply_edits(proposal, self.root)
        self.assertEqual(result.applied, ["app.py"])
        self.assertIn("value = 2", (self.root / "app.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(result.backup_path)
        self.assertTrue((self.root / result.backup_path / "app.py").exists())

    def test_same_file_multiple_edits_report_path_once(self) -> None:
        (self.root / "multi.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        result = apply_edits(
            [
                FileEdit("multi.py", "edit", "a = 1", "a = 10", "first"),
                FileEdit("multi.py", "edit", "b = 2", "b = 20", "second"),
            ],
            self.root,
        )
        self.assertEqual(result.applied, ["multi.py"])

    def test_create_refuses_overwrite(self) -> None:
        proposal = [
            FileEdit(
                path="app.py",
                action="create",
                old_string="",
                new_string="x",
                rationale="create",
            )
        ]
        with self.assertRaises(PatchApplyError):
            apply_edits(proposal, self.root)

    def test_edit_zero_match_refused(self) -> None:
        proposal = [
            FileEdit(
                path="app.py",
                action="edit",
                old_string="missing",
                new_string="x",
                rationale="edit",
            )
        ]
        with self.assertRaises(PatchApplyError):
            apply_edits(proposal, self.root)

    def test_edit_multiple_match_refused(self) -> None:
        proposal = [
            FileEdit(
                path="dup.py",
                action="edit",
                old_string="x = 1",
                new_string="x = 2",
                rationale="edit",
            )
        ]
        with self.assertRaises(PatchApplyError):
            apply_edits(proposal, self.root)

    def test_path_escape_refused(self) -> None:
        proposal = [
            FileEdit(
                path="../outside.py",
                action="create",
                old_string="",
                new_string="x",
                rationale="escape",
            )
        ]
        with self.assertRaises(PatchApplyError):
            apply_edits(proposal, self.root)

    def test_external_backup_dir_refused(self) -> None:
        outside = Path(self._tmp.name).parent / "external-backup"
        with self.assertRaises(PatchApplyError):
            apply_edits(
                [FileEdit("app.py", "edit", "value = 1", "value = 2", "edit")],
                self.root,
                backup_dir=outside,
            )

    def test_write_failure_rolls_back_written_files(self) -> None:
        (self.root / "b.py").write_text("two = 2\n", encoding="utf-8")
        edits = [
            FileEdit("app.py", "edit", "value = 1", "value = 2", "first"),
            FileEdit("b.py", "edit", "two = 2", "two = 3", "second"),
        ]
        original_write_text = Path.write_text

        def flaky_write_text(path: Path, data: str, *args: object, **kwargs: object) -> int:
            if "b.py" in path.name and "two = 3" in data:
                raise OSError("boom")
            return original_write_text(path, data, *args, **kwargs)

        with unittest.mock.patch.object(Path, "write_text", flaky_write_text):
            with self.assertRaises(PatchApplyError):
                apply_edits(edits, self.root)

        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "two = 2\n")

    def test_write_failure_rolls_back_current_file(self) -> None:
        original_replace = __import__("os").replace
        failed = False

        def flaky_replace(src: str, dst: str) -> None:
            nonlocal failed
            original_replace(src, dst)
            if str(dst).endswith("app.py") and not failed:
                failed = True
                raise OSError("boom after replace")

        with unittest.mock.patch("os.replace", flaky_replace):
            with self.assertRaises(PatchApplyError):
                apply_edits(
                    [FileEdit("app.py", "edit", "value = 1", "value = 2", "edit")],
                    self.root,
                )

        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_restore_backup_removes_created_files(self) -> None:
        result = apply_edits(
            [FileEdit("new.py", "create", "", "print('new')\n", "create")],
            self.root,
        )
        self.assertTrue((self.root / "new.py").exists())
        restore_backup(self.root, result.backup_path)
        self.assertFalse((self.root / "new.py").exists())

    def test_restore_backup_restores_modified_file(self) -> None:
        result = apply_edits(
            [FileEdit("app.py", "edit", "value = 1", "value = 2", "edit")],
            self.root,
        )
        self.assertIn("value = 2", (self.root / "app.py").read_text(encoding="utf-8"))
        restore_backup(self.root, result.backup_path)
        self.assertEqual(
            (self.root / "app.py").read_text(encoding="utf-8"),
            "value = 1\n",
        )

    def test_restore_backup_rejects_manifest_path_escape(self) -> None:
        backup = self.root / ".bughunter_backups" / "evil"
        backup.mkdir(parents=True)
        (backup / "x").write_text("evil", encoding="utf-8")
        (backup / "manifest.json").write_text(
            json.dumps({"created": [], "modified": ["../outside.txt"]}),
            encoding="utf-8",
        )
        with self.assertRaises(PatchApplyError):
            restore_backup(self.root, ".bughunter_backups/evil")

    def test_restore_backup_without_manifest_rejects_source_symlink(self) -> None:
        import os
        import sys

        if sys.platform == "win32":
            self.skipTest("符号链接在 Windows 上需要特权，跳过")
        backup = self.root / ".bughunter_backups" / "legacy"
        backup.mkdir(parents=True)
        os.symlink(self.root / "dup.py", backup / "app.py")
        with self.assertRaises(PatchApplyError):
            restore_backup(self.root, ".bughunter_backups/legacy")

    def test_create_failure_removes_empty_parent_dirs(self) -> None:
        original_replace = __import__("os").replace
        failed = False

        def flaky_replace(src: str, dst: str) -> None:
            nonlocal failed
            original_replace(src, dst)
            if str(dst).endswith("new.py") and not failed:
                failed = True
                raise OSError("boom after replace")

        with unittest.mock.patch("os.replace", flaky_replace):
            with self.assertRaises(PatchApplyError):
                apply_edits(
                    [FileEdit("nested/new.py", "create", "", "x", "create")],
                    self.root,
                )

        self.assertFalse((self.root / "nested").exists())

    def test_restore_backup_rejects_symlink_target(self) -> None:
        import os
        import sys

        if sys.platform == "win32":
            self.skipTest("符号链接在 Windows 上需要特权，跳过")
        result = apply_edits(
            [FileEdit("app.py", "edit", "value = 1", "value = 2", "edit")],
            self.root,
        )
        (self.root / "app.py").unlink()
        os.symlink(self.root / "dup.py", self.root / "app.py")
        with self.assertRaises(PatchApplyError):
            restore_backup(self.root, result.backup_path)

    def test_restore_backup_rejects_created_symlink_target(self) -> None:
        import os
        import sys

        if sys.platform == "win32":
            self.skipTest("符号链接在 Windows 上需要特权，跳过")
        result = apply_edits(
            [FileEdit("new.py", "create", "", "new", "create")],
            self.root,
        )
        (self.root / "new.py").unlink()
        os.symlink(self.root / "dup.py", self.root / "new.py")
        with self.assertRaises(PatchApplyError):
            restore_backup(self.root, result.backup_path)
        self.assertTrue((self.root / "dup.py").exists())

    def test_apply_and_test_runs_command_after_apply(self) -> None:
        settings = Settings(
            base_url="http://localhost/v1",
            model="m",
            allowed_commands={"test": ["python", "-c", "print('ok')"]},
        )
        result = apply_and_test(
            [FileEdit("app.py", "edit", "value = 1", "value = 2", "edit")],
            self.root,
            settings,
        )
        self.assertIsNotNone(result.command_result)
        self.assertEqual(result.command_result.exit_code, 0)

    def test_apply_and_test_does_not_run_command_when_apply_fails(self) -> None:
        settings = Settings(
            base_url="http://localhost/v1",
            model="m",
            allowed_commands={},
        )
        with self.assertRaises(PatchApplyError):
            apply_and_test(
                [FileEdit("app.py", "edit", "missing", "value = 2", "edit")],
                self.root,
                settings,
            )


if __name__ == "__main__":
    unittest.main()
