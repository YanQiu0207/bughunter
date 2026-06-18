"""确定性的白名单命令执行。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import BinaryIO

from .config import Settings
from .schema import CommandResult

MAX_COMMAND_OUTPUT_CHARS = 60_000
_READ_CHUNK_BYTES = 4096


class _LimitedOutput:
    """线程安全地保存有限命令输出，超出部分直接丢弃。"""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        """追加输出；超过上限的部分不会进入内存。"""
        if not data:
            return
        with self._lock:
            remaining = self._limit - self._size
            if remaining > 0:
                kept = data[:remaining]
                self._chunks.append(kept)
                self._size += len(kept)
            if len(data) > max(remaining, 0):
                self._truncated = True

    def text(self) -> str:
        """返回已保存的输出文本。"""
        with self._lock:
            raw = b"".join(self._chunks)
            truncated = self._truncated
        text = raw.decode("utf-8", "replace")
        if truncated:
            text += "\n[已截断：命令输出超过上限]"
        return text


def _drain_stream(stream: BinaryIO | None, output: _LimitedOutput) -> None:
    if stream is None:
        return
    try:
        while True:
            try:
                chunk = stream.read(_READ_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            output.append(chunk)
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _popen_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _safe_kill(proc: subprocess.Popen[bytes]) -> str:
    try:
        proc.kill()
    except ProcessLookupError:
        return ""
    except OSError as exc:
        return f"[警告] 终止直接子进程失败：{exc}"
    return ""


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> str:
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                shell=False,
                check=False,
                timeout=1.0,
            )
        except subprocess.TimeoutExpired:
            warning = _safe_kill(proc)
            suffix = f"；{warning}" if warning else ""
            return f"[警告] taskkill 超时，已尝试终止直接子进程{suffix}"
        except OSError as exc:
            warning = _safe_kill(proc)
            suffix = f"；{warning}" if warning else ""
            return f"[警告] taskkill 启动失败，已尝试终止直接子进程：{exc}{suffix}"
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            return f"[警告] taskkill 未能确认终止子进程树：{detail}"
        return ""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return ""
    except OSError:
        warning = _safe_kill(proc)
        suffix = f"；{warning}" if warning else ""
        return f"[警告] 无法终止进程组，已尝试终止直接子进程{suffix}"
    return ""


def run_command(name: str, repo_path: str | Path, settings: Settings) -> CommandResult:
    """执行配置中声明的白名单命令。

    Args:
        name: ``settings.allowed_commands`` 中的命令名。
        repo_path: 命令工作目录。
        settings: 包含命令白名单和超时时间的配置。

    Returns:
        命令执行结果。

    Raises:
        ValueError: name 未配置或 repo_path 不是目录。
    """
    if name not in settings.allowed_commands:
        raise ValueError(f"命令未在白名单中声明：{name}")
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise ValueError(f"repo_path 不是目录：{repo}")

    argv = settings.allowed_commands[name]
    stdout = _LimitedOutput(MAX_COMMAND_OUTPUT_CHARS)
    stderr = _LimitedOutput(MAX_COMMAND_OUTPUT_CHARS)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **_popen_kwargs(),
        )
    except OSError as exc:
        return CommandResult(
            name=name,
            exit_code=127,
            stdout="",
            stderr=str(exc),
            timed_out=False,
        )

    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stdout, stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    kill_warning = ""
    try:
        exit_code = proc.wait(timeout=settings.command_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_warning = _kill_process_tree(proc)
        exit_code = 124
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if kill_warning:
                kill_warning += "\n"
            kill_warning += "[警告] 直接子进程未在超时后退出"
            warning = _safe_kill(proc)
            if warning:
                kill_warning += "\n" + warning
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                kill_warning += "\n[警告] 直接子进程可能仍未完全终止"
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    return CommandResult(
        name=name,
        exit_code=exit_code,
        stdout=stdout.text(),
        stderr=(
            stderr.text()
            + (("\n" + kill_warning) if timed_out and kill_warning else "")
        ).lstrip("\n"),
        timed_out=timed_out,
    )
