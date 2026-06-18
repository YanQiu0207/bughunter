"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agent import analyze, propose_fix
from .config import Settings
from .patch import apply_edits
from .report import to_dict, to_markdown
from .runner import run_command
from .schema import build_fix_proposal, build_test_proposal

_MAX_STACK_BYTES = 65_536
_COMMANDS = {"analyze", "propose-fix", "apply", "run"}


def _build_settings(args: argparse.Namespace) -> Settings | None:
    """命令行显式给了端点就用命令行，否则返回 None。"""
    if args.base_url or args.model or getattr(args, "api_key", None):
        if not args.base_url or not args.model:
            raise SystemExit("错误：--base-url 与 --model 需同时提供（或都用环境变量）")
        return Settings(
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key or "",
            max_retries=args.max_retries,
        )
    return None


def _read_stack(stack_file: str | None) -> str:
    if stack_file:
        path = Path(stack_file).resolve()
        if not path.exists():
            raise SystemExit(f"错误：--stack-file 文件不存在：{stack_file}")
        if path.stat().st_size > _MAX_STACK_BYTES:
            raise SystemExit(
                f"错误：--stack-file 文件过大（>{_MAX_STACK_BYTES} bytes），请截取相关段落后重试"
            )
        stack_trace = path.read_text(encoding="utf-8")[:_MAX_STACK_BYTES]
    else:
        stack_trace = sys.stdin.read(_MAX_STACK_BYTES + 1)
        if len(stack_trace) > _MAX_STACK_BYTES:
            raise SystemExit(
                f"错误：stdin 输入过大（>{_MAX_STACK_BYTES} 字符），请截取相关段落后重试"
            )
        stack_trace = stack_trace[:_MAX_STACK_BYTES]
    if not stack_trace.strip():
        raise SystemExit("错误：报错堆栈为空")
    return stack_trace


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-steps", type=int, default=12, help="工具循环最大轮数")
    parser.add_argument("--max-retries", type=int, default=3, help="LLM 调用失败重试次数")
    parser.add_argument("--base-url", help="OpenAI 兼容端点，覆盖环境变量")
    parser.add_argument("--model", help="模型名，覆盖环境变量")
    parser.add_argument("--api-key", help="API Key，覆盖环境变量")


def _legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bughunter",
        description="把报错堆栈交给大模型，检索本地代码后给出根因与建议。",
    )
    parser.add_argument("--repo", required=True, help="可供检索的代码仓库根目录")
    parser.add_argument("--stack-file", help="报错堆栈文件；省略则从标准输入读取")
    parser.add_argument("--format", choices=("md", "json"), default="md", help="输出格式")
    _add_llm_args(parser)
    return parser


def _run_analyze(args: argparse.Namespace) -> int:
    result = analyze(
        _read_stack(args.stack_file),
        args.repo,
        settings=_build_settings(args),
        max_steps=args.max_steps,
    )
    if args.format == "json":
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(to_markdown(result))
    return 0


def _load_json_file(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("错误：JSON 文件顶层必须是对象")
    return data


def _settings_from_env_or_args(args: argparse.Namespace) -> Settings:
    settings = Settings.command_from_env()
    if args.base_url and args.model:
        settings.base_url = args.base_url.rstrip("/")
        settings.model = args.model
        settings.api_key = args.api_key or settings.api_key
        settings.max_retries = args.max_retries
    return settings


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bughunter")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_parser = sub.add_parser("analyze", help="分析报错")
    analyze_parser.add_argument("--repo", required=True)
    analyze_parser.add_argument("--stack-file")
    analyze_parser.set_defaults(format="json")
    _add_llm_args(analyze_parser)

    propose_parser = sub.add_parser("propose-fix", help="生成修复方案 JSON")
    propose_parser.add_argument("--repo", required=True)
    propose_parser.add_argument("--stack-file")
    _add_llm_args(propose_parser)

    apply_parser = sub.add_parser("apply", help="应用已确认的 proposal JSON")
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument("--proposal", required=True)
    apply_parser.add_argument("--kind", choices=("fix", "tests"), default="fix")

    run_parser = sub.add_parser("run", help="执行白名单命令")
    run_parser.add_argument("--repo", required=True)
    run_parser.add_argument("--name", required=True)
    _add_llm_args(run_parser)
    return parser


def _run_command_mode(args: argparse.Namespace) -> int:
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "propose-fix":
        proposal = propose_fix(
            _read_stack(args.stack_file),
            args.repo,
            settings=_build_settings(args),
            max_steps=args.max_steps,
        )
        print(json.dumps(to_dict(proposal), ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply":
        raw = _load_json_file(args.proposal)
        proposal = build_fix_proposal(raw) if args.kind == "fix" else build_test_proposal(raw)
        result = apply_edits(proposal, args.repo)
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        result = run_command(args.name, args.repo, _settings_from_env_or_args(args))
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"未知子命令：{args.command}")


def main(argv: list[str] | None = None) -> int:
    """运行 CLI 并返回进程退出码。"""
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if actual_argv and actual_argv[0] in _COMMANDS:
        args = _command_parser().parse_args(actual_argv)
        return _run_command_mode(args)
    args = _legacy_parser().parse_args(actual_argv)
    return _run_analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())
