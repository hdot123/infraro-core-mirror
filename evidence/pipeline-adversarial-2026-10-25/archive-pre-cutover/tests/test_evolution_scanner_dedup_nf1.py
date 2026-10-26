"""NF-1（VAL-FINAL-002）去重键归一化回归 — 独立文件减轻 droid shard 负担。

从 tests/test_evolution_scanner.py 拆出（PR #342），覆盖：
- deduplicate / detect_regressions / update_history / reopen 路径的对称归一化
- 行漂移、对序互换、legacy↔新形态桥接
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.e2e

_engine_dir = Path(__file__).resolve().parent.parent / "src" / "infra_core" / "engine"
sys.path.insert(0, str(_engine_dir))

from evolution_scanner import (  # noqa: E402
    Finding,
    _process_findings_with_reopen,
    deduplicate,
    detect_regressions,
    get_open_issues,
    update_history,
)


class TestDedupKeyStability:
    """同一 finding 的两种漂移形态都不再铸新卡（TDD 先行，修复前红）。"""

    RULE = "CODE_HYGIENE_DUPLICATE_BLOCK"
    NEW_PAIR = "tests/test_new.py <-> tests/test_old.py"

    def _closed_issue(self, number: int, location: str) -> dict:
        return {
            "title": f"[evolution] {self.RULE}",
            "body": f"**Rule ID**: {self.RULE}\n**Location**: {location}",
            "number": number,
            "closedAt": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        }

    def _finding(self, location: str) -> Finding:
        return Finding(self.RULE, "info", "code_hygiene", "Duplicate code block", location, "ev")

    def _get_open_issues(self, closed_bodies: list[dict]) -> list:
        closed_data = json.dumps(closed_bodies)
        with patch("evolution_scanner.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="[]", stderr=""),
                MagicMock(returncode=0, stdout=closed_data, stderr=""),
            ]
            return get_open_issues("evolution-found")

    def test_line_drift_does_not_recast(self):
        """形态一（行漂移）：关卡片 L329，复扫发现 L333 → 0 新卡。"""
        legacy_closed = "tests/test_dispatch_ledger.py::L329 <-> tests/test_chain_trigger_evolution_guard.py::L533"
        drifted = "tests/test_dispatch_ledger.py::L333 <-> tests/test_chain_trigger_evolution_guard.py::L533"
        issues = self._get_open_issues([self._closed_issue(291, legacy_closed)])
        deduped = deduplicate([self._finding(drifted)], issues)
        assert deduped == [], (
            f"行漂移重铸新卡（VAL-FINAL-002 fail 形态一）：{[f.location for f in deduped]}"
        )

    def test_pair_order_swap_does_not_recast(self):
        """形态二（对序互换）：关卡片 a<->b，复扫遍历翻面成 b<->a → 0 新卡。"""
        legacy_closed = "tests/test_infra_watch_contract.py::L35 <-> tests/test_release_announce_contract.py::L33"
        swapped = "tests/test_release_announce_contract.py::L33 <-> tests/test_infra_watch_contract.py::L35"
        issues = self._get_open_issues([self._closed_issue(336, legacy_closed)])
        deduped = deduplicate([self._finding(swapped)], issues)
        assert deduped == [], (
            f"对序互换重铸新卡（VAL-FINAL-002 fail 形态二）：{[f.location for f in deduped]}"
        )

    def test_new_format_finding_matches_legacy_closed_issue(self):
        """新生态 finding（排序文件对）与旧态关卡片同 finding → 0 新卡（桥接面）。"""
        issues = self._get_open_issues([self._closed_issue(339, "a.py::L10 <-> b.py::L20")])
        deduped = deduplicate([self._finding("a.py <-> b.py")], issues)
        assert deduped == []

    def test_new_format_closed_issue_blocks_new_format_finding(self):
        """新生态关卡片（修复后铸造）与新生态 finding 同键 → 0 新卡。"""
        issues = self._get_open_issues([self._closed_issue(341, self.NEW_PAIR)])
        deduped = deduplicate([self._finding(self.NEW_PAIR)], issues)
        assert deduped == []

    def test_genuinely_different_pair_still_casts(self):
        """真不同文件对 → 照常铸卡（归一化不得扩大吞并面）。"""
        issues = self._get_open_issues([self._closed_issue(342, "a.py::L10 <-> b.py::L20")])
        deduped = deduplicate([self._finding("a.py <-> c.py")], issues)
        assert len(deduped) == 1

    def test_same_file_pairs_keep_distinct_keys(self):
        """shard P1 实证回归：同文件对（a==b）必须原样保留行号键。"""
        issues = self._get_open_issues([self._closed_issue(343, "solo.py::L1 <-> solo.py::L20")])
        deduped = deduplicate([self._finding("solo.py::L30 <-> solo.py::L40")], issues)
        assert len(deduped) == 1, "同文件对被折叠成同键（shard P1）：不同重复块互相吞并"
        issues2 = self._get_open_issues([self._closed_issue(344, "solo.py::L1 <-> solo.py::L20")])
        deduped2 = deduplicate([self._finding("solo.py::L1 <-> solo.py::L20")], issues2)
        assert deduped2 == []

    def test_normalize_rejects_same_file_collapse(self):
        """归一化单元：同文件对原样透传，跨文件对排序折叠。"""
        from evolution_utils import _normalize_dedup_location

        assert (
            _normalize_dedup_location("solo.py::L1 <-> solo.py::L20")
            == "solo.py::L1 <-> solo.py::L20"
        )
        assert _normalize_dedup_location("b.py::L2 <-> a.py::L1") == "a.py <-> b.py"
        assert _normalize_dedup_location("plain/path.py") == "plain/path.py"
        assert _normalize_dedup_location("") == ""

    def test_auto_close_matches_legacy_body_against_new_findings(self):
        """auto_close：存量卡（旧态 body）与新生态 findings 键匹配 → 不误关。"""
        from evolution_utils import auto_close_resolved

        issues = [
            {
                "number": 336,
                "body": f"**Rule ID**: {self.RULE}\n**Location**: a.py::L10 <-> b.py::L20",
            }
        ]
        with (
            patch("evolution_utils._fetch_open_issues", return_value=issues),
            patch("evolution_utils._should_skip_close", return_value=None),
            patch("evolution_utils.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            auto_close_resolved([self._finding("a.py <-> b.py")], "evolution-found", set(), None)
            close_calls = [
                c for c in mock_run.call_args_list if c[0][0][:3] == ["gh", "issue", "close"]
            ]
            assert close_calls == [], "归一化前旧态 body 卡会被误判 resolved 并关闭"


def test_detect_regressions_legacy_pair_location_upgrades_severity(tmp_path):
    """P1: 存量 resolved 条目（带行号旧形态）必须与新形态（排序文件对）互相命中。"""
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            {
                "snapshots": [],
                "resolved_findings": [
                    {
                        "rule_id": "CODE_HYGIENE_DUPLICATE_BLOCK",
                        "location": "z.py::L10 <-> a.py::L99",
                        "resolved_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
    )

    findings = [
        Finding(
            "CODE_HYGIENE_DUPLICATE_BLOCK",
            "info",
            "code_hygiene",
            "Duplicate code block detected",
            "a.py <-> z.py",
            "evidence",
        )
    ]

    upgraded = detect_regressions(findings, history_path)
    assert upgraded[0].severity == "critical", (
        "新形态 finding 必须命中带行号的存量 resolved 条目（对称归一化）"
    )


def test_detect_regressions_same_file_pair_not_collapsed(tmp_path):
    """同文件对保留行号键：两条不同的同文件对不得被归一化互相吞并。"""
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            {
                "snapshots": [],
                "resolved_findings": [
                    {
                        "rule_id": "CODE_HYGIENE_DUPLICATE_BLOCK",
                        "location": "f.py::L10 <-> f.py::L40",
                        "resolved_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
    )

    other_block = Finding(
        "CODE_HYGIENE_DUPLICATE_BLOCK",
        "info",
        "code_hygiene",
        "Duplicate code block detected",
        "f.py::L80 <-> f.py::L120",
        "evidence",
    )

    upgraded = detect_regressions([other_block], history_path)
    assert upgraded[0].severity == "info", "不同行号的同文件对是不同键，不得误升级"


def test_update_history_no_spurious_resolved_on_format_transition(tmp_path):
    """P1: 形态过渡 tick（prev 旧形态 / current 新形态）不得写虚假 resolved 条目。"""
    history_path = tmp_path / "findings_over_time.json"

    legacy = Finding(
        "CODE_HYGIENE_DUPLICATE_BLOCK",
        "info",
        "code_hygiene",
        "Duplicate code block detected",
        "z.py::L10 <-> a.py::L99",
        "evidence",
    )
    update_history(history_path, [legacy], 0, 100)

    modern = Finding(
        "CODE_HYGIENE_DUPLICATE_BLOCK",
        "info",
        "code_hygiene",
        "Duplicate code block detected",
        "a.py <-> z.py",
        "evidence",
    )
    update_history(history_path, [modern], 0, 100)

    data = json.loads(history_path.read_text())
    assert data["resolved_findings"] == [], "同键 finding 的形态漂移不得产生虚假 resolved 条目"


def test_dup_004_guard_matches_legacy_line_numbered_finding(tmp_path):
    """P2: open_issue_keys（归一化）必须挡住带行号 legacy 形态 finding 的 fallback create。"""
    critical_finding = Finding(
        "CODE_HYGIENE_DUPLICATE_BLOCK",
        "critical",
        "code_hygiene",
        "Regression",
        "z.py::L10 <-> a.py::L99",
        "evidence",
    )
    resolved_keys = {("CODE_HYGIENE_DUPLICATE_BLOCK", "a.py <-> z.py")}
    open_issues = [
        {
            "rule_id": "CODE_HYGIENE_DUPLICATE_BLOCK",
            "location": "a.py <-> z.py",
            "number": 100,
            "state": "open",
        }
    ]

    with (
        patch("evolution_scanner._reopen_closed_issue", return_value=False),
        patch("evolution_scanner._reopen_limit_reached", return_value=False),
        patch("evolution_scanner.create_issue") as mock_create,
    ):
        created = _process_findings_with_reopen(
            [critical_finding],
            10,
            resolved_keys,
            "evolution-found",
            Path("/tmp/nonexistent_history.json"),
            open_issues,
        )

    mock_create.assert_not_called()
    assert created == 0


def test_issued_keys_normalized_for_legacy_line_numbered_finding(tmp_path):
    """P2: issued/suppressed_reopen 键必须归一化。"""
    critical_finding = Finding(
        "CODE_HYGIENE_DUPLICATE_BLOCK",
        "critical",
        "code_hygiene",
        "Regression",
        "z.py::L10 <-> a.py::L99",
        "evidence",
    )
    resolved_keys = {("CODE_HYGIENE_DUPLICATE_BLOCK", "a.py <-> z.py")}
    issued_keys: set = set()
    suppressed_reopen_keys: set = set()

    with patch("evolution_scanner._reopen_closed_issue", return_value=True):
        _process_findings_with_reopen(
            [critical_finding],
            10,
            resolved_keys,
            "evolution-found",
            Path("/tmp/nonexistent_history.json"),
            [],
            issued_keys=issued_keys,
        )

    assert issued_keys == {("CODE_HYGIENE_DUPLICATE_BLOCK", "a.py <-> z.py")}

    issued_keys_2: set = set()
    with (
        patch("evolution_scanner._reopen_closed_issue", return_value=False),
        patch("evolution_scanner._reopen_limit_reached", return_value=True),
    ):
        _process_findings_with_reopen(
            [critical_finding],
            10,
            resolved_keys,
            "evolution-found",
            Path("/tmp/nonexistent_history.json"),
            [],
            issued_keys=issued_keys_2,
            suppressed_reopen_keys=suppressed_reopen_keys,
        )

    assert suppressed_reopen_keys == {("CODE_HYGIENE_DUPLICATE_BLOCK", "a.py <-> z.py")}
