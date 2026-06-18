"""运行配置：LLM 端点、模型、超时、命令白名单等。"""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, TypeVar

_N = TypeVar("_N", int, float)


# 环境变量名
ENV_BASE_URL = "BUGHUNTER_BASE_URL"
ENV_API_KEY = "BUGHUNTER_API_KEY"
ENV_MODEL = "BUGHUNTER_MODEL"
ENV_TIMEOUT = "BUGHUNTER_TIMEOUT"
ENV_MAX_RETRIES = "BUGHUNTER_MAX_RETRIES"
ENV_ALLOWED_COMMANDS = "BUGHUNTER_ALLOWED_COMMANDS"
ENV_COMMAND_TIMEOUT = "BUGHUNTER_COMMAND_TIMEOUT"
ENV_SYSTEM_CONTEXT = "BUGHUNTER_SYSTEM_CONTEXT"


def _env_number(name: str, default: _N, cast: Callable[[str], _N]) -> _N:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 值非法，实际：{raw!r}")


def _env_allowed_commands() -> dict[str, list[str]]:
    raw = os.environ.get(ENV_ALLOWED_COMMANDS, "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"环境变量 {ENV_ALLOWED_COMMANDS} 不是合法 JSON") from exc
    return _validate_allowed_commands(parsed, ENV_ALLOWED_COMMANDS)


def _validate_allowed_commands(
    value: object, field_name: str = "allowed_commands"
) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象：name -> argv list")
    result: dict[str, list[str]] = {}
    for name, argv in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{field_name} 的命令名必须是非空字符串")
        if not isinstance(argv, list) or not argv:
            raise ValueError(f"{field_name}.{name} 必须是非空字符串数组")
        if not all(isinstance(part, str) and part for part in argv):
            raise ValueError(f"{field_name}.{name} 必须只包含非空字符串")
        result[name] = list(argv)
    return result


@dataclass
class Settings:
    """调用 OpenAI 兼容端点和确定性执行阶段所需的配置。

    base_url 给到 ``/v1`` 这一层即可，客户端会自行拼接
    ``/chat/completions``。内网无鉴权时 api_key 可留空字符串。
    """

    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 60.0
    temperature: float = 0.0
    max_retries: int = 3
    allowed_commands: dict[str, list[str]] = field(default_factory=dict)
    command_timeout: float = 300.0
    system_context: str = ""

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("Settings.base_url 不能为空")
        if not self.model:
            raise ValueError("Settings.model 不能为空")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"base_url 协议非法，只允许 http/https：{self.base_url!r}")
        self.base_url = self.base_url.rstrip("/")
        if self.max_retries < 1:
            raise ValueError(f"max_retries 至少为 1：{self.max_retries}")
        if self.command_timeout <= 0:
            raise ValueError(f"command_timeout 必须为正数：{self.command_timeout}")
        self.allowed_commands = _validate_allowed_commands(self.allowed_commands)

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构造配置；缺少必填项时给出清晰报错。"""
        base_url = os.environ.get(ENV_BASE_URL, "").strip()
        model = os.environ.get(ENV_MODEL, "").strip()
        missing = []
        if not base_url:
            missing.append(ENV_BASE_URL)
        if not model:
            missing.append(ENV_MODEL)
        if missing:
            raise RuntimeError(
                "缺少必要的环境变量：" + ", ".join(missing) + "。"
                "请设置后重试，或显式传入 Settings。"
            )

        timeout = _env_number(ENV_TIMEOUT, 60.0, float)
        max_retries = _env_number(ENV_MAX_RETRIES, 3, int)
        command_timeout = _env_number(ENV_COMMAND_TIMEOUT, 300.0, float)

        return cls(
            base_url=base_url,
            model=model,
            api_key=os.environ.get(ENV_API_KEY, "").strip(),
            timeout=timeout,
            max_retries=max_retries,
            allowed_commands=_env_allowed_commands(),
            command_timeout=command_timeout,
            system_context=os.environ.get(ENV_SYSTEM_CONTEXT, ""),
        )

    @classmethod
    def command_from_env(cls) -> "Settings":
        """从环境变量构造命令执行配置，不要求 LLM 端点变量。"""
        command_timeout = _env_number(ENV_COMMAND_TIMEOUT, 300.0, float)
        return cls(
            base_url="http://localhost/v1",
            model="command-runner",
            allowed_commands=_env_allowed_commands(),
            command_timeout=command_timeout,
            system_context=os.environ.get(ENV_SYSTEM_CONTEXT, ""),
        )
