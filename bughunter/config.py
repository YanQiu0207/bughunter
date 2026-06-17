"""运行配置：LLM 端点、模型、超时等。

参数优先，其次读环境变量。所有字段都不绑定具体厂商，
换 DeepSeek / 通义 / 内网自托管模型只需改 base_url + model（+ api_key）。
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Callable, TypeVar

_N = TypeVar("_N", int, float)


def _env_number(name: str, default: _N, cast: Callable[[str], _N]) -> _N:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 值非法，实际：{raw!r}")

# 环境变量名
ENV_BASE_URL = "BUGHUNTER_BASE_URL"
ENV_API_KEY = "BUGHUNTER_API_KEY"
ENV_MODEL = "BUGHUNTER_MODEL"
ENV_TIMEOUT = "BUGHUNTER_TIMEOUT"
ENV_MAX_RETRIES = "BUGHUNTER_MAX_RETRIES"


@dataclass
class Settings:
    """调用 OpenAI 兼容端点所需的全部配置。

    base_url 给到 ``/v1`` 这一层即可（例如 ``https://api.deepseek.com/v1``
    或内网 ``http://10.0.0.5:8000/v1``），客户端会自行拼接 ``/chat/completions``。
    内网无鉴权时 api_key 可留空字符串。
    """

    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 60.0
    temperature: float = 0.0
    max_retries: int = 3

    def __post_init__(self) -> None:
        if not self.base_url:
            raise ValueError("Settings.base_url 不能为空")
        if not self.model:
            raise ValueError("Settings.model 不能为空")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"base_url 协议非法，只允许 http/https：{self.base_url!r}")
        # 规范化：去掉尾部斜杠，统一由客户端拼接路径
        self.base_url = self.base_url.rstrip("/")
        if self.max_retries < 1:
            raise ValueError(f"max_retries 至少为 1：{self.max_retries}")

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

        return cls(
            base_url=base_url,
            model=model,
            api_key=os.environ.get(ENV_API_KEY, "").strip(),
            timeout=timeout,
            max_retries=max_retries,
        )
