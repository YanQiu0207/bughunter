"""schema.build_result 的单元测试。"""

from __future__ import annotations

import unittest

from bughunter.schema import AnalysisResult, ResultParseError, build_result

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


class BuildResultTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
