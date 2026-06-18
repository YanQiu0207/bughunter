"""report 渲染与序列化测试。"""

from __future__ import annotations

import unittest

from bughunter.report import (
    command_result_to_markdown,
    fix_proposal_to_markdown,
    to_dict,
    to_markdown,
)
from bughunter.schema import (
    AnalysisResult,
    CodeReference,
    CommandResult,
    FileEdit,
    FixProposal,
    Suggestion,
)

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
        result_dict = to_dict(_RESULT)
        self.assertIsInstance(result_dict, dict)

    def test_all_top_level_fields_present(self) -> None:
        result_dict = to_dict(_RESULT)
        for key in ("summary", "root_cause", "confidence", "suggestions", "code_references"):
            self.assertIn(key, result_dict)

    def test_confidence_value_preserved(self) -> None:
        self.assertEqual(to_dict(_RESULT)["confidence"], "high")

    def test_code_references_serialized(self) -> None:
        refs = to_dict(_RESULT)["code_references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["file"], "app.py")


class ProposalMarkdownTest(unittest.TestCase):
    def test_fix_proposal_contains_diff(self) -> None:
        proposal = FixProposal(
            summary="修复缺键",
            edits=[
                FileEdit(
                    path="app.py",
                    action="edit",
                    old_string="return d['user']\n",
                    new_string="return d.get('user')\n",
                    rationale="避免 KeyError。",
                )
            ],
            confidence="high",
        )
        md = fix_proposal_to_markdown(proposal)
        self.assertIn("```diff", md)
        self.assertIn("-return d['user']", md)
        self.assertIn("+return d.get('user')", md)

    def test_command_result_markdown_contains_stdout(self) -> None:
        md = command_result_to_markdown(
            CommandResult(name="test", exit_code=0, stdout="ok", stderr="")
        )
        self.assertIn("stdout", md)
        self.assertIn("ok", md)


if __name__ == "__main__":
    unittest.main()
