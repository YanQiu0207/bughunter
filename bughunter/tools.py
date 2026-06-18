"""只读代码工具：read_file / grep_code / list_dir。

全部纯标准库实现，并把访问严格限制在 repo_path 沙箱内（防路径穿越）。
默认只读，不提供任何命令执行，内网使用更安全。
工具返回值都是给模型看的字符串。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# 防止单次工具结果撑爆上下文的上限
MAX_READ_BYTES = 60_000
MAX_GREP_MATCHES = 100
MAX_LIST_ENTRIES = 300

# grep / 遍历时跳过的目录与二进制后缀
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules",
    ".venv", "venv", ".idea", ".vscode", "dist", "build", ".mypy_cache",
    ".bughunter_backups",
}
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".jar", ".class", ".pyc", ".so", ".dll", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".wav",
}


class SandboxError(ValueError):
    """试图访问 repo_path 之外的路径。"""


class CodeTools:
    """绑定到某个仓库根目录的只读工具集。"""

    def __init__(self, repo_path: str | os.PathLike[str]) -> None:
        self.root = Path(repo_path).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"repo_path 不是目录：{self.root}")

    # -- 沙箱 --------------------------------------------------------------- #

    def _resolve(self, rel: str) -> Path:
        """把相对路径解析为绝对路径，并校验仍在沙箱内。"""
        target = (self.root / (rel or ".")).resolve()
        if target != self.root and self.root not in target.parents:
            raise SandboxError(f"路径越界，拒绝访问：{rel!r}")
        return target

    def _has_skipped_part(self, target: Path) -> bool:
        """检查目标路径是否落在工具应跳过的目录下。"""
        try:
            rel = target.relative_to(self.root)
        except ValueError:
            return False
        return any(part in _SKIP_DIRS for part in rel.parts)

    # -- 工具 --------------------------------------------------------------- #

    def read_file(self, path: str, start: int | None = None, end: int | None = None) -> str:
        target = self._resolve(path)
        if self._has_skipped_part(target):
            return f"[跳过] 路径位于忽略目录中：{path}"
        if not target.is_file():
            return f"[错误] 文件不存在或不是文件：{path}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"[错误] 读取失败：{exc}"

        lines = text.splitlines()
        total = len(lines)
        s = 1 if start is None else max(1, start)
        e = total if end is None else min(total, end)
        if s > total:
            return f"[提示] {path} 共 {total} 行，起始行 {s} 超出范围"

        selected = lines[s - 1 : e]
        body = "\n".join(f"{i}\t{line}" for i, line in enumerate(selected, start=s))
        if len(body.encode("utf-8")) > MAX_READ_BYTES:
            body = body.encode("utf-8")[:MAX_READ_BYTES].decode("utf-8", "ignore")
            body += "\n[已截断：内容超过单次读取上限，请用 start/end 缩小范围]"
        header = f"# {path}（第 {s}-{e} 行，共 {total} 行）\n"
        return header + body

    def grep_code(self, pattern: str, glob: str | None = None) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return f"[错误] 正则无效：{exc}"

        name_re = _glob_to_regex(glob) if glob else None
        matches: list[str] = []
        truncated = False

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _SKIP_DIRS and not (Path(dirpath) / d).is_symlink()
            ]
            for fn in filenames:
                if Path(fn).suffix.lower() in _BINARY_EXT:
                    continue
                if name_re and not name_re.match(fn):
                    continue
                fpath = Path(dirpath) / fn
                # 沙箱：跳过符号链接文件，防止读取仓库外内容
                if fpath.is_symlink():
                    continue
                try:
                    resolved = fpath.resolve()
                    if resolved != self.root and self.root not in resolved.parents:
                        continue
                except OSError:
                    continue
                rel = fpath.relative_to(self.root).as_posix()
                try:
                    with fpath.open("r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, start=1):
                            if regex.search(line):
                                matches.append(f"{rel}:{lineno}:{line.rstrip()}")
                                if len(matches) >= MAX_GREP_MATCHES:
                                    truncated = True
                                    break
                except OSError:
                    pass
                if truncated:
                    break
            if truncated:
                break

        if not matches:
            return f"[无命中] 模式 {pattern!r} 未匹配到任何内容"
        out = "\n".join(matches)
        if truncated:
            out += f"\n[已截断：命中超过 {MAX_GREP_MATCHES} 条，请细化模式]"
        return out

    def list_dir(self, path: str | None = None) -> str:
        target = self._resolve(path or ".")
        if self._has_skipped_part(target):
            return f"[跳过] 路径位于忽略目录中：{path}"
        if not target.is_dir():
            return f"[错误] 不是目录：{path}"
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if child.name in _SKIP_DIRS or child.is_symlink():
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(child.name + suffix)
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append(f"[已截断：超过 {MAX_LIST_ENTRIES} 项]")
                break
        rel = target.relative_to(self.root).as_posix() or "."
        return f"# {rel}/\n" + "\n".join(entries)

    # -- 分发 --------------------------------------------------------------- #

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """按工具名执行，捕获异常返回给模型而非中断循环。"""
        try:
            if name == "read_file":
                return self.read_file(
                    str(arguments["path"]),
                    arguments.get("start"),
                    arguments.get("end"),
                )
            if name == "grep_code":
                return self.grep_code(
                    str(arguments["pattern"]), arguments.get("glob")
                )
            if name == "list_dir":
                return self.list_dir(arguments.get("path"))
        except SandboxError as exc:
            return f"[拒绝] {exc}"
        except KeyError as exc:
            return f"[错误] 缺少必填参数：{exc}"
        except Exception as exc:  # noqa: BLE001 - 工具错误回传给模型
            return f"[错误] 工具执行失败：{exc}"
        return f"[错误] 未知工具：{name}"


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """把简单文件名通配（* ?）转成正则，仅匹配文件名（不含路径）。"""
    if "/" in glob or "\\" in glob:
        raise ValueError(f"glob 只支持文件名级别通配，不支持路径分隔符：{glob!r}")
    parts = []
    for ch in glob:
        if ch == "*":
            parts.append("[^/]*")
        elif ch == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts) + r"\Z")
