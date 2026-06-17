"""命令行入口：python -m bughunter --repo <path> --stack-file <trace.txt>

堆栈来源：--stack-file 指定文件，或省略时从标准输入读取。
输出：--format md（默认，Markdown 报告）或 json（结构化结果）。
配置：默认从 BUGHUNTER_* 环境变量读取，可用 --base-url/--model/--api-key 覆盖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MAX_STACK_BYTES = 65_536

from .agent import analyze
from .config import Settings
from .report import to_dict, to_markdown


def _build_settings(args: argparse.Namespace) -> Settings | None:
    """命令行显式给了端点就用命令行，否则返回 None（由 analyze 读环境变量）。"""
    if args.base_url or args.model or args.api_key:
        if not args.base_url or not args.model:
            raise SystemExit("错误：--base-url 与 --model 需同时提供（或都用环境变量）")
        return Settings(
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key or "",
            max_retries=args.max_retries,
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bughunter",
        description="把报错堆栈交给大模型，检索本地代码后给出根因与优化建议。",
    )
    parser.add_argument("--repo", required=True, help="可供检索的代码仓库根目录")
    parser.add_argument("--stack-file", help="报错堆栈文件；省略则从标准输入读取")
    parser.add_argument(
        "--format", choices=("md", "json"), default="md", help="输出格式，默认 md"
    )
    parser.add_argument("--max-steps", type=int, default=12, help="工具循环最大轮数")
    parser.add_argument(
        "--max-retries", type=int, default=3, help="LLM 调用失败重试次数，默认 3"
    )
    parser.add_argument("--base-url", help="OpenAI 兼容端点，覆盖环境变量")
    parser.add_argument("--model", help="模型名，覆盖环境变量")
    parser.add_argument("--api-key", help="API Key，覆盖环境变量")
    args = parser.parse_args(argv)

    if args.stack_file:
        p = Path(args.stack_file).resolve()
        if not p.exists():
            raise SystemExit(f"错误：--stack-file 文件不存在：{args.stack_file}")
        if p.stat().st_size > _MAX_STACK_BYTES:
            raise SystemExit(
                f"错误：--stack-file 文件过大（>{_MAX_STACK_BYTES} bytes），请截取相关段落后重试"
            )
        with p.open("r", encoding="utf-8") as f:
            stack_trace = f.read(_MAX_STACK_BYTES)
    else:
        stack_trace = sys.stdin.read(_MAX_STACK_BYTES + 1)
        if len(stack_trace) > _MAX_STACK_BYTES:
            raise SystemExit(
                f"错误：stdin 输入过大（>{_MAX_STACK_BYTES} 字符），请截取相关段落后重试"
            )
        stack_trace = stack_trace[:_MAX_STACK_BYTES]

    if not stack_trace.strip():
        raise SystemExit("错误：报错堆栈为空")

    settings = _build_settings(args)
    result = analyze(
        stack_trace, args.repo, settings=settings, max_steps=args.max_steps
    )

    if args.format == "json":
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
