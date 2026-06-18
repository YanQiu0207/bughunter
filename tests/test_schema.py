"""schema build 函数的单元测试。"""

from __future__ import annotations

import unittest

from bughunter.schema import (
    AnalysisResult,
    FixProposal,
    ResultParseError,
    build_fix_proposal,
    build_result,
    build_test_proposal,
)
from bughunter import (
    ResultParseError as PublicResultParseError,
    build_fix_proposal as public_build_fix_proposal,
    build_test_proposal as public_build_test_proposal,
)

_VALID_ARGS: dict = {
    "summary": "KeyError：缺少 user 键",
    "root_cause": "app.py:2 直接索引 d['user']",
    "confidence": "high",
    "suggestions": [
        {"title": "改用 .get", "detail": "用 d.get('user', {})", "code_refs": []}
    ],
    "code_references": [
        {
            "file": "app.py",
            "line_start": 1,
            "line_end": 2,
            "excerpt": "def f(d):\n    return d['user']",
        }
    ],
}

_VALID_FIX_ARGS = {
    "summary": "修复 KeyError",
    "edits": [
        {
            "path": "app.py",
            "action": "edit",
            "old_string": "return d['user']",
            "new_string": "return d.get('user')",
            "rationale": "避免缺键异常。",
        }
    ],
    "confidence": "high",
}


class BuildResultTest(unittest.TestCase):
    def test_result_parse_error_exported_from_package(self) -> None:
        self.assertIs(PublicResultParseError, ResultParseError)

    def test_proposal_builders_exported_from_package(self) -> None:
        self.assertIs(public_build_fix_proposal, build_fix_proposal)
        self.assertIs(public_build_test_proposal, build_test_proposal)

    def test_valid_args_returns_analysis_result(self) -> None:
        result = build_result(_VALID_ARGS)
        self.assertIsInstance(result, AnalysisResult)
        self.assertEqual(result.confidence, "high")
        self.assertIn("KeyError", result.summary)
        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(result.code_references[0].file, "app.py")

    def test_missing_summary_raises(self) -> None:
        bad = dict(_VALID_ARGS)
        del bad["summary"]
        with self.assertRaises(ResultParseError) as ctx:
            build_result(bad)
        self.assertIn("summary", str(ctx.exception))

    def test_missing_root_cause_raises(self) -> None:
        bad = dict(_VALID_ARGS)
        del bad["root_cause"]
        with self.assertRaises(ResultParseError):
            build_result(bad)

    def test_invalid_confidence_raises(self) -> None:
        bad = {**_VALID_ARGS, "confidence": "very_high"}
        with self.assertRaises(ResultParseError) as ctx:
            build_result(bad)
        self.assertIn("very_high", str(ctx.exception))

    def test_code_references_not_list_raises(self) -> None:
        bad = {**_VALID_ARGS, "code_references": "not-a-list"}
        with self.assertRaises(ResultParseError):
            build_result(bad)

    def test_all_confidence_values_accepted(self) -> None:
        for conf in ("high", "medium", "low"):
            result = build_result({**_VALID_ARGS, "confidence": conf})
            self.assertEqual(result.confidence, conf)

    def test_empty_code_references_allowed(self) -> None:
        result = build_result({**_VALID_ARGS, "code_references": []})
        self.assertEqual(result.code_references, [])


class BuildProposalTest(unittest.TestCase):
    def test_build_fix_proposal_valid(self) -> None:
        result = build_fix_proposal(_VALID_FIX_ARGS)
        self.assertIsInstance(result, FixProposal)
        self.assertEqual(result.edits[0].action, "edit")

    def test_build_test_proposal_accepts_create(self) -> None:
        args = {
            **_VALID_FIX_ARGS,
            "edits": [
                {
                    "path": "tests/test_app.py",
                    "action": "create",
                    "old_string": "",
                    "new_string": "def test_x():\n    pass\n",
                    "rationale": "新增测试。",
                }
            ],
        }
        result = build_test_proposal(args)
        self.assertEqual(result.edits[0].action, "create")

    def test_invalid_action_raises(self) -> None:
        args = {
            **_VALID_FIX_ARGS,
            "edits": [{**_VALID_FIX_ARGS["edits"][0], "action": "delete"}],
        }
        with self.assertRaises(ResultParseError):
            build_fix_proposal(args)

    def test_edit_requires_old_string(self) -> None:
        args = {
            **_VALID_FIX_ARGS,
            "edits": [{**_VALID_FIX_ARGS["edits"][0], "old_string": ""}],
        }
        with self.assertRaises(ResultParseError):
            build_fix_proposal(args)

    def test_create_requires_empty_old_string(self) -> None:
        args = {
            **_VALID_FIX_ARGS,
            "edits": [
                {
                    "path": "tests/test_app.py",
                    "action": "create",
                    "old_string": "not-empty",
                    "new_string": "x",
                    "rationale": "新增测试。",
                }
            ],
        }
        with self.assertRaises(ResultParseError):
            build_test_proposal(args)


if __name__ == "__main__":
    unittest.main()
