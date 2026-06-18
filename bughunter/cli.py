"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agent import analyze, generate_tests, propose_fix
from .config import Settings
from .patch import apply_and_test, apply_edits, restore_backup
from .report import to_dict, to_markdown
from .runner import run_command
from .schema import (
    FixProposal,
    TestProposal,
    build_fix_proposal,
    build_result,
    build_test_proposal,
)

_MAX_STACK_BYTES = 65_536
_COMMANDS = {
    "analyze",
    "propose-fix",
    "generate-tests",
    "apply",
    "run",
    "apply-and-test",
    "restore",
}


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


def _read_text_file(path: str, label: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        raise SystemExit(f"错误：{label} 文件不存在：{path}")
    return file_path.read_text(encoding="utf-8")


def _load_proposal_file(path: str, kind: str) -> FixProposal | TestProposal:
    raw = _load_json_file(path)
    return build_fix_proposal(raw) if kind == "fix" else build_test_proposal(raw)


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
    propose_parser.add_argument(
        "--analysis",
        help="上一阶段 analyze 产出的 JSON 文件",
    )
    propose_parser.add_argument(
        "--test-output",
        help="上一轮 run 产出的结果文件或原始测试输出",
    )
    _add_llm_args(propose_parser)

    tests_parser = sub.add_parser("generate-tests", help="生成测试方案 JSON")
    tests_parser.add_argument("--repo", required=True)
    tests_parser.add_argument(
        "--proposal",
        required=True,
        help="上一阶段生成的 proposal JSON",
    )
    tests_parser.add_argument("--kind", choices=("fix", "tests"), default="fix")
    _add_llm_args(tests_parser)

    apply_parser = sub.add_parser("apply", help="应用已确认的 proposal JSON")
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument("--proposal", required=True)
    apply_parser.add_argument("--kind", choices=("fix", "tests"), default="fix")

    run_parser = sub.add_parser("run", help="执行白名单命令")
    run_parser.add_argument("--repo", required=True)
    run_parser.add_argument("--name", required=True)
    _add_llm_args(run_parser)

    apply_test_parser = sub.add_parser(
        "apply-and-test",
        help="应用 proposal 后执行白名单命令",
    )
    apply_test_parser.add_argument("--repo", required=True)
    apply_test_parser.add_argument("--proposal", required=True)
    apply_test_parser.add_argument("--kind", choices=("fix", "tests"), default="fix")
    apply_test_parser.add_argument("--name", default="test")
    _add_llm_args(apply_test_parser)

    restore_parser = sub.add_parser("restore", help="按备份路径恢复文件")
    restore_parser.add_argument("--repo", required=True)
    restore_parser.add_argument("--backup-path", required=True)
    return parser


def _run_command_mode(args: argparse.Namespace) -> int:
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "propose-fix":
        analysis = None
        if args.analysis:
            analysis = build_result(_load_json_file(args.analysis))
        test_output = None
        if args.test_output:
            test_output = _read_text_file(args.test_output, "--test-output")
        proposal = propose_fix(
            _read_stack(args.stack_file),
            args.repo,
            analysis=analysis,
            test_output=test_output,
            settings=_build_settings(args),
            max_steps=args.max_steps,
        )
        print(json.dumps(to_dict(proposal), ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate-tests":
        proposal = _load_proposal_file(args.proposal, args.kind)
        tests = generate_tests(
            args.repo,
            proposal=proposal,
            settings=_build_settings(args),
            max_steps=args.max_steps,
        )
        print(json.dumps(to_dict(tests), ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply":
        proposal = _load_proposal_file(args.proposal, args.kind)
        result = apply_edits(proposal, args.repo)
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        result = run_command(args.name, args.repo, _settings_from_env_or_args(args))
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply-and-test":
        proposal = _load_proposal_file(args.proposal, args.kind)
        result = apply_and_test(
            proposal,
            args.repo,
            _settings_from_env_or_args(args),
            test_name=args.name,
        )
        print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command == "restore":
        restore_backup(args.repo, args.backup_path)
        print(json.dumps({"restored": True}, ensure_ascii=False, indent=2))
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
