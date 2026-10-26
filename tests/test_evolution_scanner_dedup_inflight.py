"""病四铸卡去重：PR 工单反查 / 同文件文件核闸 / max_inflight_prs / intra-tick 归一化。

权威：docs/guides/pr-pipeline-cutover-FINAL-PLAN.md §1.3
     evidence/pipeline-adversarial-2026-10-25/disease-4-design-supplement.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_engine_dir = Path(__file__).resolve().parent.parent / "src" / "infra_core" / "engine"
sys.path.insert(0, str(_engine_dir))

from evolution_scanner import (  # noqa: E402
    CODE_HYGIENE_DUPLICATE_RULE,
    Finding,
    InflightPrDedupState,
    _resolve_dedup_key_from_pr,
    _resolve_max_inflight_prs,
    deduplicate,
    get_open_prs_dedup_keys,
)
from evolution_utils import (  # noqa: E402
    _extract_duplicate_file_core,
    _normalize_dedup_location,
    dedup_intra_tick,
)

RULE = CODE_HYGIENE_DUPLICATE_RULE


def _finding(location: str, rule_id: str = RULE) -> Finding:
    return Finding(rule_id, "info", "code_hygiene", "dup", location, "ev")


def _issue(rule_id: str, location: str, number: int = 1) -> dict:
    return {
        "rule_id": rule_id,
        "location": _normalize_dedup_location(location),
        "number": number,
        "state": "open",
        "body": f"**Rule ID**: {rule_id}\n**Location**: {location}",
        "category": "code_hygiene",
    }


class TestFileCoreGate:
    def test_same_file_core_extracted(self):
        assert _extract_duplicate_file_core("solo.py::L1 <-> solo.py::L20") == frozenset(
            {"solo.py"}
        )

    def test_cross_file_core_is_none(self):
        assert _extract_duplicate_file_core("a.py::L1 <-> b.py::L2") is None
        assert _extract_duplicate_file_core("a.py <-> b.py") is None

    def test_file_core_suppresses_different_line_pair(self):
        """精确键挡不住的同文件另一行对 → 文件核闸抑制。"""
        open_issues = [_issue(RULE, "solo.py::L860 <-> solo.py::L950")]
        findings = [_finding("solo.py::L846 <-> solo.py::L860")]
        stats: dict[str, int] = {}
        out = deduplicate(findings, open_issues, stats=stats)
        assert out == []
        assert stats["suppressed_by_inflight_file"] == 1

    def test_file_core_from_pr_keys(self):
        open_issues: list[dict] = []
        findings = [_finding("solo.py::L10 <-> solo.py::L20")]
        pr_cores = frozenset({frozenset({"solo.py"})})
        out = deduplicate(
            findings,
            open_issues,
            pr_file_cores=pr_cores,
        )
        assert out == []

    def test_cross_file_still_uses_exact_key_only(self):
        open_issues = [_issue(RULE, "a.py <-> b.py")]
        # 不同跨文件对：精确键未命中，且无同文件核 → 放行
        findings = [_finding("a.py <-> c.py")]
        out = deduplicate(findings, open_issues)
        assert len(out) == 1

    def test_file_core_gate_env_off(self, monkeypatch):
        monkeypatch.setenv("EVOLUTION_DEDUP_FILE_CORE", "0")
        open_issues = [_issue(RULE, "solo.py::L1 <-> solo.py::L20")]
        findings = [_finding("solo.py::L30 <-> solo.py::L40")]
        out = deduplicate(findings, open_issues)
        assert len(out) == 1


class TestReverseLookup:
    def test_direct_body_keys(self):
        body = f"**Rule ID**: {RULE}\n**Location**: a.py <-> b.py\n"
        key = _resolve_dedup_key_from_pr(body, "feat/x", "evolution-found")
        assert key == (RULE, "a.py <-> b.py")

    def test_closes_issue_number(self):
        issue_body = f"**Rule ID**: {RULE}\n**Location**: solo.py::L1 <-> solo.py::L2\n"
        with patch("evolution_scanner._fetch_issue_body", return_value=issue_body) as fetch:
            key = _resolve_dedup_key_from_pr(
                "Fixes stuff\n\nCloses #442\n", "feat/x", "evolution-found"
            )
            fetch.assert_called_once_with(442)
        assert key == (RULE, "solo.py::L1 <-> solo.py::L2")

    def test_fixes_infra_searches_evolution_issue(self):
        issue_body = "**Rule ID**: R1\n**Location**: path.py\n"
        with patch(
            "evolution_scanner._lookup_evolution_issue_body_by_infra",
            return_value=issue_body,
        ) as lookup:
            key = _resolve_dedup_key_from_pr("Fixes INFRA-1500\n", "feat/other", "evolution-found")
            lookup.assert_called_once_with("INFRA-1500", "evolution-found")
        assert key == ("R1", "path.py")

    def test_branch_infra_id(self):
        issue_body = "**Rule ID**: R2\n**Location**: z.py\n"
        with patch(
            "evolution_scanner._lookup_evolution_issue_body_by_infra",
            return_value=issue_body,
        ) as lookup:
            key = _resolve_dedup_key_from_pr(
                "narrative only", "feat/INFRA-99-dedupe", "evolution-found"
            )
            lookup.assert_called_once_with("INFRA-99", "evolution-found")
        assert key == ("R2", "z.py")

    def test_lookup_failure_returns_none(self):
        with (
            patch("evolution_scanner._fetch_issue_body", return_value=None),
            patch(
                "evolution_scanner._lookup_evolution_issue_body_by_infra",
                return_value=None,
            ),
        ):
            assert (
                _resolve_dedup_key_from_pr(
                    "Fixes INFRA-1\nCloses #9", "feat/INFRA-1", "evolution-found"
                )
                is None
            )


class TestGetOpenPrsDedupKeys:
    def test_keyed_pr_enters_suppress_set(self):
        prs = [
            {
                "number": 445,
                "body": "Fixes INFRA-1500",
                "headRefName": "feat/INFRA-1500-x",
            }
        ]
        with (
            patch("evolution_scanner.subprocess.run") as mock_run,
            patch(
                "evolution_scanner._resolve_dedup_key_from_pr",
                return_value=(RULE, "solo.py::L1 <-> solo.py::L9"),
            ),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(prs), stderr="")
            state = get_open_prs_dedup_keys("evolution-found")
        assert (RULE, "solo.py::L1 <-> solo.py::L9") in state.keys
        assert frozenset({"solo.py"}) in state.file_cores
        assert state.evolution_pr_count == 1
        assert state.unkeyed_count == 0

    def test_unkeyed_evolution_pr_not_in_suppress_set(self, capsys):
        prs = [
            {
                "number": 445,
                "body": "Fixes INFRA-1500",
                "headRefName": "feat/INFRA-1500-x",
            }
        ]
        with (
            patch("evolution_scanner.subprocess.run") as mock_run,
            patch("evolution_scanner._resolve_dedup_key_from_pr", return_value=None),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(prs), stderr="")
            state = get_open_prs_dedup_keys("evolution-found")
        assert state.keys == frozenset()
        assert state.evolution_pr_count == 1
        assert state.unkeyed_count == 1
        assert "suppressed_by_inflight_pr_unkeyed" in capsys.readouterr().out

    def test_issue_shaped_json_skipped(self):
        """缺 headRefName 的 issue list mock 不得计入。"""
        fake_issues = [{"number": 1, "body": f"**Rule ID**: {RULE}\n**Location**: x"}]
        with patch("evolution_scanner.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=json.dumps(fake_issues), stderr=""
            )
            state = get_open_prs_dedup_keys("evolution-found")
        assert state == InflightPrDedupState.empty()

    def test_env_disables_pr_keys(self, monkeypatch):
        monkeypatch.setenv("EVOLUTION_DEDUP_PR_KEYS", "0")
        with patch("evolution_scanner.subprocess.run") as mock_run:
            state = get_open_prs_dedup_keys("evolution-found")
        mock_run.assert_not_called()
        assert state == InflightPrDedupState.empty()


class TestExactKeyUnion:
    def test_pr_exact_key_suppresses(self):
        findings = [_finding("a.py <-> b.py")]
        pr_keys = frozenset({(RULE, "a.py <-> b.py")})
        out = deduplicate(findings, [], pr_keys=pr_keys)
        assert out == []


class TestMaxInflight:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("EVOLUTION_MAX_INFLIGHT_PRS", "0")
        assert _resolve_max_inflight_prs({"max_inflight_prs": 10}) == 0
        monkeypatch.setenv("EVOLUTION_MAX_INFLIGHT_PRS", "8")
        assert _resolve_max_inflight_prs({"max_inflight_prs": 10}) == 8

    def test_config_value(self, monkeypatch):
        monkeypatch.delenv("EVOLUTION_MAX_INFLIGHT_PRS", raising=False)
        assert _resolve_max_inflight_prs({"max_inflight_prs": 10}) == 10


class TestDedupIntraTickNormalize:
    def test_line_drift_collapsed_in_tick(self):
        findings = [
            _finding("a.py::L1 <-> b.py::L2"),
            _finding("a.py::L3 <-> b.py::L4"),  # 跨文件归一化后同键
        ]
        out = dedup_intra_tick(findings)
        assert len(out) == 1
        assert _normalize_dedup_location(out[0].location) == "a.py <-> b.py"

    def test_same_file_keeps_distinct(self):
        findings = [
            _finding("solo.py::L1 <-> solo.py::L20"),
            _finding("solo.py::L30 <-> solo.py::L40"),
        ]
        out = dedup_intra_tick(findings)
        assert len(out) == 2


class TestMergedPrIssueStillOpen:
    def test_open_issue_still_suppresses_after_hypothetical_merge(self):
        """v2 不查 merged PR：issue 仍 open 时精确键继续抑制（supplement §I）。"""
        open_issues = [_issue(RULE, "a.py <-> b.py", number=442)]
        findings = [_finding("a.py <-> b.py")]
        # 无 PR 键（已合并不在 open pr list）
        out = deduplicate(findings, open_issues, pr_keys=frozenset())
        assert out == []
