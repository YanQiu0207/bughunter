"""确定性补丁应用与应用后测试组合接口。"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import Settings
from .runner import run_command
from .schema import ApplyAndTestResult, ApplyResult, FileEdit, FixProposal, TestProposal

_MANIFEST = "manifest.json"


class PatchApplyError(RuntimeError):
    """应用修改失败。"""


def _resolve(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PatchApplyError(f"路径越界，拒绝访问：{rel!r}")
    return target


def _restore_target(root: Path, rel: str) -> Path:
    """解析恢复目标，不跟随最终路径的符号链接。"""
    raw = Path(rel)
    if raw.is_absolute() or ".." in raw.parts:
        raise PatchApplyError(f"路径越界，拒绝访问：{rel!r}")
    parent = root
    for part in raw.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise PatchApplyError(f"恢复路径包含符号链接，拒绝访问：{rel!r}")
    resolved_parent = parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise PatchApplyError(f"路径越界，拒绝访问：{rel!r}")
    target = resolved_parent / raw.name
    if target.is_symlink():
        raise PatchApplyError(f"恢复目标是符号链接，拒绝覆盖：{rel!r}")
    return target


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _proposal_edits(
    proposal: FixProposal | TestProposal | Iterable[FileEdit],
) -> list[FileEdit]:
    if isinstance(proposal, (FixProposal, TestProposal)):
        return list(proposal.edits)
    return list(proposal)


def _make_backup_dir(root: Path, backup_dir: str | Path | None) -> Path:
    if backup_dir is not None:
        path = Path(backup_dir).resolve()
        if path != root and root not in path.parents:
            raise PatchApplyError(f"backup_dir 必须位于仓库内：{backup_dir}")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = root / ".bughunter_backups" / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _apply_edit_group(original_text: str, edits: list[FileEdit], rel: str) -> str:
    replacements: list[tuple[int, int, str]] = []
    for edit in edits:
        start = original_text.find(edit.old_string)
        if start < 0:
            raise PatchApplyError(f"old_string 在 {rel} 中匹配 0 次，必须恰好唯一匹配")
        second = original_text.find(edit.old_string, start + len(edit.old_string))
        if second >= 0:
            raise PatchApplyError(f"old_string 在 {rel} 中匹配多次，必须恰好唯一匹配")
        end = start + len(edit.old_string)
        replacements.append((start, end, edit.new_string))

    replacements.sort(key=lambda item: item[0])
    for previous, current in zip(replacements, replacements[1:]):
        if previous[1] > current[0]:
            raise PatchApplyError(f"{rel} 中存在重叠修改块")

    parts: list[str] = []
    cursor = 0
    for start, end, new_string in replacements:
        parts.append(original_text[cursor:start])
        parts.append(new_string)
        cursor = end
    parts.append(original_text[cursor:])
    return "".join(parts)


def _write_atomic(target: Path, content: str) -> None:
    tmp = target.with_name(f".{target.name}.bughunter-tmp-{uuid.uuid4().hex}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _missing_parents_before_write(root: Path, target: Path) -> list[Path]:
    """返回写入 target 前不存在、且位于 root 内的父目录。"""
    missing: list[Path] = []
    current = target.parent
    while current != root and root in current.parents and not current.exists():
        missing.append(current)
        current = current.parent
    return missing


def apply_edits(
    proposal: FixProposal | TestProposal | Iterable[FileEdit],
    repo_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
) -> ApplyResult:
    """确定性应用人工确认后的修改方案。

    全部修改会先在内存中完成校验；任一步写入失败会回滚已写文件。
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise PatchApplyError(f"repo_path 不是目录：{root}")

    edits = _proposal_edits(proposal)
    if not edits:
        return ApplyResult(applied=[], skipped=[], backup_path=None)

    edit_groups: dict[Path, list[FileEdit]] = {}
    create_edits: dict[Path, FileEdit] = {}
    path_by_key: dict[str, Path] = {}
    original: dict[Path, str | None] = {}
    planned: dict[Path, str] = {}
    applied: list[str] = []

    for edit in edits:
        target = _resolve(root, edit.path)
        rel = target.relative_to(root).as_posix()
        key = _path_key(target)
        existing_target = path_by_key.get(key)
        if edit.action == "create":
            if edit.old_string:
                raise PatchApplyError("create.old_string 必须为空")
            if target.exists() or existing_target is not None:
                raise PatchApplyError(f"create 拒绝覆盖已存在文件：{rel}")
            path_by_key[key] = target
            create_edits[target] = edit
            original[target] = None
            planned[target] = edit.new_string
            continue
        if edit.action != "edit":
            raise PatchApplyError(f"未知 action：{edit.action!r}")
        if target in create_edits:
            raise PatchApplyError(f"同一文件不能同时 create 和 edit：{rel}")
        if existing_target is not None and existing_target != target:
            raise PatchApplyError(f"路径大小写冲突：{rel}")
        path_by_key[key] = target
        edit_groups.setdefault(target, []).append(edit)

    for target, group in edit_groups.items():
        rel = target.relative_to(root).as_posix()
        if not target.is_file():
            raise PatchApplyError(f"edit 目标不存在或不是文件：{rel}")
        original_text = target.read_text(encoding="utf-8", errors="replace")
        original[target] = original_text
        planned[target] = _apply_edit_group(original_text, group, rel)

    backup_root = _make_backup_dir(root, backup_dir)
    manifest = {"created": [], "modified": []}
    written: list[Path] = []
    created_dirs: list[Path] = []
    rollback_failures: list[str] = []
    try:
        for target, content in planned.items():
            rel_path = target.relative_to(root)
            rel = rel_path.as_posix()
            before = original[target]
            if before is None:
                manifest["created"].append(rel)
            else:
                manifest["modified"].append(rel)
                backup_target = backup_root / rel_path
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                backup_target.write_text(before, encoding="utf-8")
            created_dirs.extend(_missing_parents_before_write(root, target))
            target.parent.mkdir(parents=True, exist_ok=True)
            written.append(target)
            _write_atomic(target, content)
        (backup_root / _MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        for target in reversed(written):
            before = original[target]
            if before is None:
                try:
                    target.unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_failures.append(
                        f"{target.relative_to(root).as_posix()}: {rollback_exc}"
                    )
            else:
                try:
                    _write_atomic(target, before)
                except OSError as rollback_exc:
                    rollback_failures.append(
                        f"{target.relative_to(root).as_posix()}: {rollback_exc}"
                    )
        for directory in created_dirs:
            try:
                directory.rmdir()
            except OSError:
                pass
        detail = f"写入失败，已尝试回滚：{exc}"
        if rollback_failures:
            detail += "；回滚失败：" + "；".join(rollback_failures)
        raise PatchApplyError(detail) from exc

    backup_path = (
        backup_root.relative_to(root).as_posix()
        if backup_root == root or root in backup_root.parents
        else str(backup_root)
    )
    applied = [target.relative_to(root).as_posix() for target in planned]
    return ApplyResult(applied=applied, skipped=[], backup_path=backup_path)


def apply_and_test(
    proposal: FixProposal | TestProposal | Iterable[FileEdit],
    repo_path: str | Path,
    settings: Settings,
    *,
    test_name: str = "test",
    backup_dir: str | Path | None = None,
) -> ApplyAndTestResult:
    """应用修改，成功后执行白名单测试命令。"""
    apply_result = apply_edits(proposal, repo_path, backup_dir=backup_dir)
    command_result = run_command(test_name, repo_path, settings)
    return ApplyAndTestResult(
        apply_result=apply_result,
        command_result=command_result,
    )


def restore_backup(repo_path: str | Path, backup_path: str | Path) -> None:
    """把备份目录复制回仓库；manifest 存在时同时删除新增文件。"""
    root = Path(repo_path).resolve()
    backup = (root / backup_path).resolve()
    if backup != root and root not in backup.parents:
        raise PatchApplyError(f"备份目录越界：{backup_path}")
    if not backup.is_dir():
        raise PatchApplyError(f"备份目录不存在：{backup}")

    manifest_path = backup / _MANIFEST
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel in manifest.get("created", []):
            _restore_target(root, str(rel)).unlink(missing_ok=True)
        modified = manifest.get("modified", [])
        sources = []
        for rel in modified:
            target = _restore_target(root, str(rel))
            source = (backup / str(rel)).resolve()
            if source != backup and backup not in source.parents:
                raise PatchApplyError(f"备份 manifest 路径越界：{rel!r}")
            sources.append((source, target))
    else:
        sources = []
        for source in backup.rglob("*"):
            if source.is_symlink():
                raise PatchApplyError(f"备份源是符号链接，拒绝恢复：{source}")
            if not source.is_file():
                continue
            resolved_source = source.resolve()
            if resolved_source != backup and backup not in resolved_source.parents:
                raise PatchApplyError(f"备份源路径越界：{source}")
            sources.append(
                (source, _restore_target(root, source.relative_to(backup).as_posix()))
            )

    for source, target in sources:
        if source.name == _MANIFEST:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
