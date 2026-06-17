"""report.to_markdown / to_dict 的单元测试。"""

from __future__ import annotations

import unittest

from bughunter.report import to_dict, to_markdown
from bughunter.schema import AnalysisResult, CodeReference, Suggestion

_REF = CodeReference(file="app.py", line_start=1, line_end=2, excerpt="def f():\n    pass")
_SUG = Suggestion(title="改用 .get", detail="用 d.get('user', {})", code_refs=[])
_RESULT = AnalysisResult(
    summary="KeyError：缺少 user 键",
    root_cause="app.py:2 直接索引 d['user']",
    code_references=[_REF],
    suggestions=[_SUG],
    confidence="high",
)


class ToMarkdownTest(unittest.TestCase):
    def test_contains_summary(self) -> None:
        md = to_markdown(_RESULT)
        self.assertIn("KeyError", md)

    def test_contains_code_references_section(self) -> None:
        md = to_markdown(_RESULT)
        self.assertIn("## 相关代码", md)
        self.assertIn("app.py:1-2", md)

    def test_no_code_references_section_when_empty(self) -> None:
        result = AnalysisResult(
            summary="s",
            root_cause="r",
            code_references=[],
            suggestions=[],
            confidence="low",
        )
        md = to_markdown(result)
        self.assertNotIn("## 相关代码", md)

    def test_contains_suggestions_section(self) -> None:
        md = to_markdown(_RESULT)
        self.assertIn("## 优化建议", md)
        self.assertIn("改用 .get", md)

    def test_confidence_label_chinese(self) -> None:
        md = to_markdown(_RESULT)
        self.assertIn("高", md)

    def test_ends_with_newline(self) -> None:
        md = to_markdown(_RESULT)
        self.assertTrue(md.endswith("\n"))


class ToDictTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        d = to_dict(_RESULT)
        self.assertIsInstance(d, dict)

    def test_all_top_level_fields_present(self) -> None:
        d = to_dict(_RESULT)
        for key in ("summary", "root_cause", "confidence", "suggestions", "code_references"):
            self.assertIn(key, d)

    def test_confidence_value_preserved(self) -> None:
        self.assertEqual(to_dict(_RESULT)["confidence"], "high")

    def test_code_references_serialized(self) -> None:
        refs = to_dict(_RESULT)["code_references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file"], "app.py")


if __name__ == "__main__":
    unittest.main()
