"""E1/E2 契约测试：shard-review-prompt.md 成文面对齐（2026-10-26 对齐包）。

E1 结构快照：必备分节（Purpose / Review Guidelines / Testing Guidelines /
Review Style / Repository context / What not to include / Examples）存在且
有序；main 版既有分节（Output Format / Self-Validation Rules / Budget
Instructions / Important）不丢失。

E2 内容契约：
- 必含词：应审字面缺口（XSS、input validation、cleanup、caching、敏感数据
  不落盘）与用户向性能 >=P1 表述必须落在条文里；
- 行为契约原文保留：<=20 findings、P0/P1 先扫、运行时分钟预算、JSON 输出契约；
- 禁词：模糊口号句式（"be more accurate" 类）只能作为反例被引用，不得裸
  出现为审查条目。

判别力：默认锁仓内当前 prompt；设置环境变量 SHARD_REVIEW_PROMPT_FILE 可把
本套断言指向历史版本（如 origin/main 的 65 行版）做红证。
"""

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = REPO_ROOT / ".github" / "review" / "shard-review-prompt.md"
PROMPT_PATH = Path(os.environ.get("SHARD_REVIEW_PROMPT_FILE", str(DEFAULT_PROMPT_PATH)))

REQUIRED_SECTIONS = [
    "Purpose",
    "Review Guidelines",
    "Testing Guidelines",
    "Review Style",
    "Repository context",
    "What not to include",
    "Examples",
]

PRESERVED_SECTIONS = [
    "Output Format",
    "Self-Validation Rules",
    "Budget Instructions",
    "Important",
]

# 口号句式只允许在引号引用或示例代码块内出现（作为反例），裸出现即为回退
BANNED_SLOGANS = [
    "be more accurate",
    "don't miss issues",
    "be more thorough",
    "be consistent",
    "improve code quality",
]


def _sections(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def _section_body(text: str, heading: str) -> str:
    """`## heading` 分节正文（到下一个 `## ` 为止）；分节缺失时返回空串。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            body: list[str] = []
            for later in lines[i + 1 :]:
                if later.startswith("## "):
                    break
                body.append(later)
            return "\n".join(body)
    return ""


def _strip_quoted_and_examples(text: str) -> str:
    """剥离引号串与示例代码块：口号只能被引用为反例，剥离后不应再出现。"""
    without_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r'"[^"\n]*"', "", without_code).lower()


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return PROMPT_PATH.read_text()


class TestE1Structure:
    @pytest.mark.parametrize("section", REQUIRED_SECTIONS)
    def test_required_section_present(self, prompt_text: str, section: str) -> None:
        assert section in _sections(prompt_text), f"缺少必备分节: ## {section}"

    def test_required_sections_ordered(self, prompt_text: str) -> None:
        headings = _sections(prompt_text)
        positions = []
        for section in REQUIRED_SECTIONS:
            assert section in headings, f"缺少必备分节: ## {section}"
            positions.append(headings.index(section))
        assert positions == sorted(positions), "必备分节顺序回归"

    @pytest.mark.parametrize("section", PRESERVED_SECTIONS)
    def test_preserved_section_present(self, prompt_text: str, section: str) -> None:
        assert section in _sections(prompt_text), f"既有分节不得丢失: ## {section}"

    def test_severity_levels_subsection_preserved(self, prompt_text: str) -> None:
        assert "### Severity Levels" in prompt_text

    def test_what_not_lists_exclusions(self, prompt_text: str) -> None:
        body = _section_body(prompt_text, "What not to include").lower()
        for keyword in ("slogan", "ux", "speculative", "stylistic"):
            assert keyword in body, f"What-not 缺少勿审项: {keyword}"

    def test_examples_have_actionable_and_vague_pair(self, prompt_text: str) -> None:
        body = _section_body(prompt_text, "Examples")
        assert "actionable" in body.lower(), "Examples 缺正例标注"
        assert "vague" in body.lower(), "Examples 缺反例标注"
        assert body.count('"severity"') >= 2, "正反例须各为一条 finding JSON"


class TestE2AuditLiterals:
    @pytest.mark.parametrize("literal", ["xss", "input validation", "cleanup", "caching"])
    def test_review_guidelines_literal(self, prompt_text: str, literal: str) -> None:
        assert literal in _section_body(prompt_text, "Review Guidelines").lower()

    def test_sensitive_data_not_persisted(self, prompt_text: str) -> None:
        assert "secrets, tokens, and pii are not logged" in prompt_text.lower()

    def test_user_impacting_performance_named(self, prompt_text: str) -> None:
        assert "user-impacting performance" in prompt_text.lower()

    def test_user_impacting_performance_rated_p1(self, prompt_text: str) -> None:
        p1_lines = [
            line for line in prompt_text.splitlines() if line.strip().startswith("- **P1**:")
        ]
        assert p1_lines, "P1 severity 条目缺失"
        assert any("performance" in line.lower() for line in p1_lines)


class TestE2BehaviorContractPreserved:
    def test_findings_budget_kept(self, prompt_text: str) -> None:
        assert "at most 20 findings" in prompt_text

    def test_p0_p1_first_scan_kept(self, prompt_text: str) -> None:
        assert "P0/P1 issues first" in prompt_text

    def test_runtime_minute_budget_kept(self, prompt_text: str) -> None:
        budget = _section_body(prompt_text, "Budget Instructions")
        assert "SHARD_TIMEOUT_MINUTES" in budget

    def test_json_output_contract_kept(self, prompt_text: str) -> None:
        assert "You MUST respond with a valid JSON object" in prompt_text
        assert '"shard_id"' in prompt_text
        assert '"findings"' in prompt_text


class TestE2BannedSlogans:
    @pytest.mark.parametrize("slogan", BANNED_SLOGANS)
    def test_no_bare_vague_slogan(self, prompt_text: str, slogan: str) -> None:
        bare = _strip_quoted_and_examples(prompt_text)
        assert slogan not in bare, f"模糊口号句式不得裸出现: {slogan!r}"
