"""Adjudicated-findings baseline suppression + blocking-never-folds contract (U2').

User-approved 2026-10-22 (D2): the review gate gains adjudication memory —
findings already adjudicated as design-intent or false-positive are suppressed
by a main-maintained baseline file, while remaining explicitly listed in the
summary as [adjudicated-suppressed] (never silently dropped).

Contract surface (this file):
- hit / miss / code-change-invalidation / line-shift tolerance for the
  content-hash key (path + anchor + ±radius window hash)
- dual-tree key (P1 fix): the PR-head window (gh api raw contents) must hash
  equal too — BASE-only hashing let PR edits inside the window slip through
- baseline trust boundary (P1 fix, round-2): the baseline file itself must come
  from the server-side BASE ref (gh api contents?ref=<base_sha>), never from
  the PR-controlled workspace checkout — a PR tree can carry a forged
  adjudicated_findings.json and suppress its own findings
- main() forwards --baseline-ref verbatim (P1 fix, round-3): an explicit empty
  ref (CI, BASE unresolved) must reach the fail-closed empty-table branch —
  the old `args.baseline_ref or None` coerced it to None and silently
  reinstated the workspace disk fallback; omitting the flag (local/testing)
  keeps disk-mode semantics
- ref-channel fallthrough (P1 fix, round-5): a non-empty ref combined with an
  empty repository string must yield the empty table as well — `if repository:`
  used to skip the ref branch entirely and control flow fell through to the
  workspace disk read, the exact forged-baseline path the boundary exists to
  close; argparse required=True still accepts ''
- message anchor (P1 fix): finding_message_pattern is required per entry and
  matched as a case/whitespace-normalized "/"-fragment of the finding message
- created_at required (P1 fix): the TTL <= 90-day cap can no longer be
  bypassed by omitting created_at
- guardrails: entry cap, expires_at required and honored, adjudication_ref
  required, zero non-empty message fragments = fail-closed (an empty anchor
  matches everything), future created_at = fail-closed (never-expiring entry),
  severity required and restricted to the findings vocabulary, missing
  baseline file = no-op, malformed baseline = fail-closed
- suppression leaves a trace: summary + terminal listing, stats accounting
- §3a fix: P0/P1 are never folded in shard summaries or the terminal summary
- committed baseline file shape + live anchor hash match (drift guard)
- publish wiring: --baseline is passed at both publish invocation sites
- workflow publish step pins the baseline to the server-side BASE sha
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine"
sys.path.insert(0, str(ENGINE_DIR))

from droid_review.publish_findings import (  # noqa: E402
    adjudicated_context_hash,
    apply_adjudicated_baseline,
    load_adjudicated_baseline,
    post_summary_comment,
    post_terminal_summary,
)

BASELINE_PATH = REPO_ROOT / "scripts" / "droid_review" / "adjudicated_findings.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "droid-review.yml"
ACTION_YML = REPO_ROOT / "actions" / "droid-review-aggregate" / "action.yml"

QA_ANCHOR = (
    "if: always() && (github.event_name != 'pull_request' "
    "|| contains(github.event.pull_request.labels.*.name, 'processing'))"
)
# 锚点窗口片段：条目与 finding message 都必须携带（P1 修复后 message 参与
# 匹配；fragment 形态容忍 shard 审查措辞漂移，非全字串等值）。
MSG_FRAGMENT = "qa-ok/bare-always"


def _window_hash(lines: list[str], center0: int, radius: int = 3) -> str:
    """Independent reference implementation of the key hash (pins the algorithm)."""
    lo = max(0, center0 - radius)
    hi = min(len(lines), center0 + radius + 1)
    return hashlib.sha256("\n".join(ln.strip() for ln in lines[lo:hi]).encode("utf-8")).hexdigest()


def _make_entry(**overrides: object) -> dict:
    entry = {
        "id": "qa-ok-fork-failclosed-design-pinned",
        "path": ".github/workflows/qa.yml",
        "anchor": QA_ANCHOR,
        "context_sha256": "0" * 64,
        "severity": "P1",
        "finding_message_pattern": MSG_FRAGMENT,
        "created_at": "2026-10-22",
        "expires_at": "2027-01-20",
        "adjudication_ref": "round-3 #5 + round-7 rejection (evidence/m2-round7-p1x3-verification.md)",
    }
    entry.update(overrides)
    return entry


def _write_baseline(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "adjudicated_findings.json"
    path.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return path


def _write_qa_fixture(tmp_path: Path) -> Path:
    """A minimal file whose ±3 window around its anchor matches the real anchor."""
    target = tmp_path / ".github" / "workflows" / "qa.yml"
    target.parent.mkdir(parents=True)
    lines = [
        "  # Aggregation gate for QA jobs",
        "  # preceding context line A",
        "  # preceding context line B",
        "    runs-on: [self-hosted, arc-pve-01]",
        f"    {QA_ANCHOR}",
        "    steps:",
        "      - name: Check all required QA jobs",
        "      # trailing padding line (outside the ±3 window)",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _finding(
    file: str = ".github/workflows/qa.yml", line: int = 5, message: str = MSG_FRAGMENT
) -> dict:
    return {"severity": "P1", "file": file, "line": line, "message": message}


def _stub_head_tree(monkeypatch, windows: dict[str, list[str]]) -> None:
    """Stub gh api contents calls so apply_adjudicated_baseline sees a
    PR-head tree whose windows equal the given per-path line lists."""
    import base64

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "contents" in joined and "--header" in cmd:
            url = next(a for a in cmd if "/contents/" in a)
            path = url.split("/contents/", 1)[1].split("?ref=", 1)[0]
            if path not in windows:
                raise subprocess.CalledProcessError(1, cmd)
            payload = {"content": base64.b64encode("\n".join(windows[path]).encode()).decode()}
            return MagicMock(stdout=json.dumps(payload).encode())
        return MagicMock(stdout=b"")

    monkeypatch.setattr("subprocess.run", fake_run)


class TestApplyAdjudicatedBaseline:
    """Key = path + anchor window content hash; code change kills the entry."""

    # 锚点文件按工作区相对路径读取（生产=BASE checkout 根）——测试同构 chdir。
    @pytest.fixture(autouse=True)
    def _workspace_isolation(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

    def _fixture_entry(self, tmp_path: Path, radius: int = 3) -> dict:
        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        return _make_entry(
            context_sha256=_window_hash(lines, center0, radius), context_radius=radius
        )

    def test_hit_suppresses_blocking_finding(self, tmp_path, monkeypatch):
        entry = self._fixture_entry(tmp_path)
        target_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        _stub_head_tree(monkeypatch, {entry["path"]: target_lines})
        findings = [
            {
                "severity": "P1",
                "file": ".github/workflows/qa.yml",
                "line": 5,
                "message": f"wording drift: {MSG_FRAGMENT}",
            },
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == []
        assert len(suppressed) == 1
        assert suppressed[0]["suppression_ref"] == entry["id"]

    def test_hit_at_line_shift_within_radius(self, tmp_path, monkeypatch):
        """Shards report against the PR head; BASE anchoring + shift tolerance must hold."""
        entry = self._fixture_entry(tmp_path)
        target_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        _stub_head_tree(monkeypatch, {entry["path"]: target_lines})
        findings = [
            {
                "severity": "P1",
                "file": ".github/workflows/qa.yml",
                "line": 4,
                "message": MSG_FRAGMENT,
            },
            {
                "severity": "P1",
                "file": ".github/workflows/qa.yml",
                "line": 6,
                "message": MSG_FRAGMENT,
            },
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert suppressed and len(suppressed) == 2

    def test_miss_different_file_still_blocks(self, tmp_path):
        entry = self._fixture_entry(tmp_path)
        findings = [{"severity": "P1", "file": "src/other.py", "line": 5, "message": "real bug"}]
        active, suppressed = apply_adjudicated_baseline(
            findings, _write_baseline(tmp_path, [entry])
        )
        assert active == findings
        assert suppressed == []

    def test_code_change_invalidates_entry(self, tmp_path):
        """Any content change inside the ±radius window re-arms the gate."""
        entry = self._fixture_entry(tmp_path)
        target = tmp_path / ".github" / "workflows" / "qa.yml"
        lines = target.read_text().splitlines()
        lines[1] = "  # preceding context line A — AMENDED"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        findings = [
            {"severity": "P1", "file": ".github/workflows/qa.yml", "line": 5, "message": "x"}
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings, _write_baseline(tmp_path, [entry])
        )
        assert active == findings
        assert suppressed == []

    def test_new_defect_at_same_window_not_suppressed(self, tmp_path):
        """Severity escalation at the adjudicated position must stay blocking."""
        entry = self._fixture_entry(tmp_path)
        findings = [
            {
                "severity": "P0",
                "file": ".github/workflows/qa.yml",
                "line": 5,
                "message": "worse now",
            },
            {"severity": "P2", "file": ".github/workflows/qa.yml", "line": 5, "message": "nit"},
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings, _write_baseline(tmp_path, [entry])
        )
        assert len(active) == 2
        assert suppressed == []


class TestDualTreeWindowHash(TestApplyAdjudicatedBaseline):
    """P1 fix: suppression key must cover the PR-head tree, not just BASE.

    Findings are reported against PR-head line numbers, but the window hash was
    computed over the BASE workspace copy only. A PR editing code inside the
    anchor ±radius window with ≤3 net line shift kept the BASE hash intact and
    got its brand-new defect suppressed. The head-side window is now fetched
    (gh api raw contents) and must hash equal too; any head/BASE window
    divergence re-arms the gate.
    """

    def test_head_edit_inside_window_not_suppressed(self, tmp_path, monkeypatch):
        """BASE window unchanged + PR head window edited inside anchor → no hit."""
        entry = self._fixture_entry(tmp_path)
        base_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        head_lines = list(base_lines)
        head_lines[1] = "  # preceding context line A — PR EDIT"  # inside ±3 of anchor
        _stub_head_tree(monkeypatch, {entry["path"]: head_lines})
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == findings
        assert suppressed == []

    def test_head_edit_outside_window_still_suppressed(self, tmp_path, monkeypatch):
        """A PR change beyond the ±radius window must not re-arm the gate."""
        entry = self._fixture_entry(tmp_path)
        base_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        head_lines = list(base_lines)
        head_lines[0] = "  # Aggregation gate for QA jobs — reworded"  # radius=3, anchor@4
        _stub_head_tree(monkeypatch, {entry["path"]: head_lines})
        findings = [_finding()]
        _, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert len(suppressed) == 1

    def test_head_file_missing_not_suppressed(self, tmp_path, monkeypatch):
        """Head tree without the anchored file → conservatively no hit."""
        entry = self._fixture_entry(tmp_path)
        _stub_head_tree(monkeypatch, {})  # head tree lacks the path entirely
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == findings
        assert suppressed == []

    def test_head_fetch_failure_not_suppressed(self, tmp_path, monkeypatch):
        """gh api error on head-side fetch → fail toward the gate (no suppression)."""
        entry = self._fixture_entry(tmp_path)

        def failing_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr("subprocess.run", failing_run)
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == findings
        assert suppressed == []

    def test_no_head_context_single_tree_semantics(self, tmp_path):
        """repository/head_sha omitted → legacy single-tree matching still works."""
        entry = self._fixture_entry(tmp_path)
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings, _write_baseline(tmp_path, [entry])
        )
        assert active == []
        assert len(suppressed) == 1

    def test_head_anchors_resolved_on_head_lines(self, tmp_path, monkeypatch):
        """Anchor lookup uses the head copy for candidate windows (findings are
        head-numbered), then demands BASE/HEAD window hash equality."""
        entry = self._fixture_entry(tmp_path)
        base_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        # Two padding lines added above the window push the anchor down
        # (index 4 → 6); the ±3 window keeps the same row sequence (the leading
        # line and the trailing line it displaces both stay outside the window)
        # — only the anchor INDEX drifts, hashes are equal.
        head_lines = base_lines[:1] + ["  # pad A", "  # pad B"] + base_lines[1:]
        _stub_head_tree(monkeypatch, {entry["path"]: head_lines})
        findings = [_finding(line=6)]  # head numbering: anchor sits at line 6
        _, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert len(suppressed) == 1


class TestMessageAnchor(TestApplyAdjudicatedBaseline):
    """P1 fix: finding_message_pattern participates in the match key.

    Position-only matching suppressed any new defect that merely landed in the
    same window at the entry severity. The documented per-entry message
    pattern is now required (fail-closed) and matched as a
    case/whitespace-normalized fragment containment — tolerant of review
    wording drift, unlike brittle full-string equality.
    """

    def test_message_without_anchor_fragment_not_suppressed(self, tmp_path, monkeypatch):
        """Same window, same severity, but an unrelated message → stays blocking."""
        entry = self._fixture_entry(tmp_path)
        target_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        _stub_head_tree(monkeypatch, {entry["path"]: target_lines})
        findings = [
            {
                "severity": "P1",
                "file": ".github/workflows/qa.yml",
                "line": 5,
                "message": "brand-new defect the PR just introduced",
            }
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == findings
        assert suppressed == []

    def test_message_match_tolerates_case_and_whitespace_drift(self, tmp_path, monkeypatch):
        """'QA-OK  bare   ALWAYS' normalizes to the entry fragment → hit."""
        entry = self._fixture_entry(tmp_path)
        target_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        _stub_head_tree(monkeypatch, {entry["path"]: target_lines})
        findings = [
            {
                "severity": "P1",
                "file": ".github/workflows/qa.yml",
                "line": 5,
                "message": "Shard wording v9: QA-OK  BARE   always() red on forks",
            }
        ]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert active == []
        assert len(suppressed) == 1

    def test_missing_message_pattern_fail_closed(self, tmp_path):
        entry = _make_entry()
        del entry["finding_message_pattern"]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))


class TestSuppressionAnnotationAndExpiry(TestApplyAdjudicatedBaseline):
    """Suppression annotates (never drops); expiry deactivates entries."""

    def test_suppression_is_annotation_not_removal(self, tmp_path, monkeypatch):
        entry = self._fixture_entry(tmp_path)
        target_lines = (tmp_path / ".github" / "workflows" / "qa.yml").read_text().splitlines()
        _stub_head_tree(monkeypatch, {entry["path"]: target_lines})
        findings = [_finding()]
        _, suppressed = apply_adjudicated_baseline(
            findings,
            _write_baseline(tmp_path, [entry]),
            repository="o/r",
            head_sha="a" * 40,
        )
        assert suppressed[0]["severity"] == "P1"
        assert suppressed[0]["message"] == MSG_FRAGMENT
        assert suppressed[0]["file"] == ".github/workflows/qa.yml"

    def test_missing_baseline_file_is_noop(self, tmp_path):
        findings = [{"severity": "P1", "file": "a.py", "line": 1, "message": "m"}]
        active, suppressed = apply_adjudicated_baseline(findings, tmp_path / "nope.json")
        assert active == findings
        assert suppressed == []

    def test_expired_entry_inactive(self, tmp_path, monkeypatch):
        entry = self._fixture_entry(tmp_path)
        from datetime import date, timedelta

        entry["created_at"] = (date.today() - timedelta(days=100)).isoformat()
        entry["expires_at"] = (date.today() - timedelta(days=10)).isoformat()
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings, _write_baseline(tmp_path, [entry])
        )
        assert active == findings
        assert suppressed == []


class TestBaselineGuardrails:
    """Baseline only accepts adjudicated design items / false positives, capped."""

    def test_over_cap_fail_closed(self, tmp_path):
        entries = [_make_entry(id=f"e{i}") for i in range(11)]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, entries))

    def test_missing_adjudication_ref_fail_closed(self, tmp_path):
        entry = _make_entry()
        del entry["adjudication_ref"]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))

    def test_missing_expires_at_fail_closed(self, tmp_path):
        entry = _make_entry()
        del entry["expires_at"]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))

    def test_missing_created_at_fail_closed(self, tmp_path):
        """P1 fix: created_at omission must not bypass the TTL ≤90d guard."""
        entry = _make_entry(created_at="2099-12-31", expires_at="2099-12-31")
        del entry["created_at"]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))

    def test_nonstring_created_at_fail_closed(self, tmp_path):
        """P1 fix (review note): created_at must be type-checked — a non-string
        previously escaped date.fromisoformat's except ValueError as TypeError."""
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [_make_entry(created_at=123)]))

    def test_expiry_horizon_capped_at_90_days(self, tmp_path):
        entry = _make_entry(created_at="2026-10-22", expires_at="2027-01-21")
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))

    def test_malformed_json_fail_closed(self, tmp_path):
        path = tmp_path / "adjudicated_findings.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(path)

    def test_context_hash_shape_enforced(self, tmp_path):
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(
                _write_baseline(tmp_path, [_make_entry(context_sha256="abc")])
            )


class TestMessagePatternGuardrails(TestApplyAdjudicatedBaseline):
    """③ Zero non-empty fragments = fail-closed (empty anchor matches everything)."""

    def test_slash_only_message_pattern_fail_closed(self, tmp_path):
        """'/' normalizes to '' → zero fragments → would substring-match every message."""
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(
                _write_baseline(tmp_path, [_make_entry(finding_message_pattern="/")])
            )

    def test_whitespace_only_message_pattern_fail_closed(self, tmp_path):
        """Fragments are taken after normalization; whitespace-only == empty anchor."""
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(
                _write_baseline(tmp_path, [_make_entry(finding_message_pattern="   ")])
            )


class TestTtlCreatedGuardrails(TestApplyAdjudicatedBaseline):
    """④ TTL anchored to a real past date: future created_at must not mint
    a never-expiring entry (created <= today, checked against the real clock)."""

    def test_future_created_at_fail_closed(self, tmp_path):
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(
                _write_baseline(
                    tmp_path,
                    [_make_entry(created_at="2099-01-01", expires_at="2099-04-01")],
                )
            )

    def test_created_at_after_expires_at_fail_closed(self, tmp_path):
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(
                _write_baseline(
                    tmp_path,
                    [_make_entry(created_at="2026-10-23", expires_at="2026-10-22")],
                )
            )

    def test_today_created_at_accepted(self, tmp_path):
        from datetime import date, timedelta

        today = date.today().isoformat()
        entry = _make_entry(
            created_at=today, expires_at=(date.today() + timedelta(days=30)).isoformat()
        )
        active = load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))
        assert len(active) == 1


class TestSeverityGuardrails(TestApplyAdjudicatedBaseline):
    """⑤ severity participates in the match key — must be required and in-vocabulary."""

    def test_missing_severity_fail_closed(self, tmp_path):
        entry = _make_entry()
        del entry["severity"]
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [entry]))

    def test_nonstring_severity_fail_closed(self, tmp_path):
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [_make_entry(severity=1)]))

    def test_unknown_severity_fail_closed(self, tmp_path):
        """Vocabulary = findings vocabulary (P0-P3); 'critical' etc. can never hit."""
        with pytest.raises(SystemExit):
            load_adjudicated_baseline(_write_baseline(tmp_path, [_make_entry(severity="P4")]))


class TestSuppressionLeaveATrace:
    """Suppressed findings stay visible: [adjudicated-suppressed] in summaries."""

    FINDINGS = [
        {
            "severity": "P1",
            "file": ".github/workflows/qa.yml",
            "line": 5,
            "message": MSG_FRAGMENT,
        }
    ]

    def test_summary_lists_suppressed_with_marker(self):
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd and "/issues/" in " ".join(cmd):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            assert post_summary_comment(
                [],
                pr_number=1,
                repository="o/r",
                by_shard={},
                round_number=1,
                suppressed=self.FINDINGS,
            )
        assert "[adjudicated-suppressed]" in captured["body"]
        assert ".github/workflows/qa.yml:5" in captured["body"]

    def test_terminal_summary_lists_suppressed_with_marker(self):
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd and "/issues/" in " ".join(cmd):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            assert post_terminal_summary(
                [], pr_number=1, repository="o/r", round_number=4, suppressed=self.FINDINGS
            )
        assert "[adjudicated-suppressed]" in captured["body"]

    def test_no_suppressed_section_when_none(self):
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd and "/issues/" in " ".join(cmd):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            post_summary_comment([], pr_number=1, repository="o/r", by_shard={}, round_number=1)
        assert "[adjudicated-suppressed]" not in captured["body"]

    def test_main_exit_zero_when_all_blocking_suppressed(self, monkeypatch, tmp_path):
        """Gate reads post-filter severity: suppressed P1 → no blocking exit."""
        from droid_review.publish_findings import main

        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        _write_baseline(tmp_path, [entry])
        (tmp_path / "findings-shard-0.json").write_text(
            json.dumps({"shard_id": 0, "findings": self.FINDINGS}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--baseline",
                "adjudicated_findings.json",
            ],
        )
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers", lambda pr, repo: ([], 0)
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        with patch("subprocess.run", return_value=MagicMock(stdout=b"")) as mock_run:
            main()  # no SystemExit → gate green
        # Positive evidence (P2 review note): the summary POST carries the
        # suppression disclosure — proves suppression ran, not a vacuous return.
        summary_posts = [
            json.loads(c.kwargs["input"].decode())["body"]
            for c in mock_run.call_args_list
            if "--method" in c.args[0] and "/issues/" in " ".join(c.args[0])
        ]
        assert any("[adjudicated-suppressed]" in body for body in summary_posts)


class TestBlockingNeverFolds:
    """§3a fix: P0/P1 are never collapsed into "... and N more"."""

    def test_terminal_summary_lists_every_p1_unfolded(self):
        findings = [
            {"severity": "P1", "file": f"b{i}.py", "line": i, "message": f"bug {i}"}
            for i in range(51)
        ] + [
            {"severity": "P2", "file": f"c{i}.py", "line": i, "message": f"nit {i}"}
            for i in range(60)
        ]
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd:
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            assert post_terminal_summary(findings, pr_number=1, repository="o/r", round_number=4)

        body = captured["body"]
        for i in range(51):
            assert f"b{i}.py:{i}" in body, f"P1 #{i} folded in terminal summary"
        assert "c49.py:49" not in body  # P2 beyond cap folds
        assert "c59.py:59" not in body  # P2 beyond cap still folds
        assert "... and 60 more" in body  # fold count covers the 60 folded P2 only

    def test_shard_summary_lists_every_p1_unfolded(self):
        findings = [
            {"severity": "P1", "file": f"b{i}.py", "line": i, "message": f"bug {i}"}
            for i in range(25)
        ] + [
            {"severity": "P3", "file": f"d{i}.py", "line": i, "message": f"nit {i}"}
            for i in range(30)
        ]
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd and "/issues/" in " ".join(cmd):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            assert post_summary_comment(
                [], pr_number=1, repository="o/r", by_shard={0: findings}, round_number=1
            )

        body = captured["body"]
        for i in range(25):
            assert f"b{i}.py:{i}" in body, f"P1 #{i} folded in shard summary"
        assert "... and 30 more" in body  # only the 30 P3 beyond cap


class TestCommittedBaselineFile:
    """The shipped baseline: 1 entry (qa.yml:323 fork false-red design item)."""

    @pytest.fixture()
    def baseline(self) -> dict:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    def test_first_entry_is_qa_ok_anchor(self, baseline):
        entries = baseline["entries"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["path"] == ".github/workflows/qa.yml"
        assert "if: always()" in entry["anchor"]
        assert "processing" in entry["anchor"]

    def test_entry_hash_matches_live_main_content(self, baseline):
        """Drift guard: entry hash must equal the ±3 window hash on current main."""
        entry = baseline["entries"][0]
        lines = (REPO_ROOT / entry["path"]).read_text().splitlines()
        centers = [i for i, ln in enumerate(lines) if entry["anchor"] in ln.strip()]
        assert centers, "anchor line vanished from live main — baseline must be re-adjudicated"
        hashes = {
            adjudicated_context_hash(lines, c, entry.get("context_radius", 3)) for c in centers
        }
        assert entry["context_sha256"] in hashes

    def test_entry_carries_adjudication_chain(self, baseline):
        entry = baseline["entries"][0]
        blob = json.dumps(entry)
        assert "round-3" in blob
        assert "round-7" in blob
        assert "m2-round7-p1x3-verification" in blob
        assert "fork" in blob.lower()

    def test_entry_carries_message_pattern(self, baseline):
        """P1 fix: the message anchor is part of the match key and mandatory."""
        entry = baseline["entries"][0]
        pattern = entry.get("finding_message_pattern")
        assert isinstance(pattern, str) and pattern.strip()
        # "/"-fragment form: every fragment must survive message normalization
        # containment against the shipped pattern (self-consistency).
        fragments = [f.strip() for f in pattern.split("/") if f.strip()]
        assert fragments

    def test_entry_expiry_within_90_days(self, baseline):
        entry = baseline["entries"][0]
        from datetime import date

        created = date.fromisoformat(entry["created_at"])
        expires = date.fromisoformat(entry["expires_at"])
        assert 0 < (expires - created).days <= 90

    def test_under_cap(self, baseline):
        assert len(baseline["entries"]) <= 10


class TestPublishWiring:
    """Both publish invocation sites pass the baseline."""

    def test_workflow_publish_step_passes_baseline(self):
        content = WORKFLOW.read_text()
        assert "--baseline scripts/droid_review/adjudicated_findings.json" in content
        # Trust boundary: entries must come from the server-side BASE sha, not
        # whatever lands in the checkout (a PR tree can carry a forged file).
        assert "--baseline-ref" in content

    def test_aggregate_action_publish_passes_baseline(self):
        content = ACTION_YML.read_text()
        # Trust boundary: the action never passes a workspace --baseline path;
        # it always passes --baseline-ref (server-controlled BASE sha or empty
        # = no suppression). Comments mention the flag in prose only.
        assert '--baseline-ref "$BASELINE_REF"' in content
        publish_step = content.split("- name: Publish findings", 1)[1]
        assert re.search(r"(?m)^\s*--baseline\s", publish_step) is None

    def test_baseline_file_exists(self):
        assert BASELINE_PATH.exists()


class TestBaselineTrustBoundary:
    """① P1 (round-2): the baseline file itself sits on a trust boundary.

    The aggregate action runs after the caller checked out BASE into
    $GITHUB_WORKSPACE — but a PR tree carries its own
    scripts/droid_review/adjudicated_findings.json, and reading the baseline
    from the PR-influenced workspace lets a PR suppress its own findings with
    a forged entry. The baseline must therefore load from the server-side
    BASE ref via the same gh api contents channel as the head tree
    (_fetch_head_file_lines): entry content is only trusted when it comes
    from <base_sha>, not from the checkout.
    """

    def test_load_adjudicated_baseline_ref_fetches_from_base_ref(self, tmp_path, monkeypatch):
        """Entries come from gh api contents?ref=<base_sha>, not the local path."""
        import base64

        entry = _make_entry()
        ref_payload = {
            "content": base64.b64encode(
                json.dumps({"version": 1, "entries": [entry]}).encode()
            ).decode()
        }
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return MagicMock(stdout=json.dumps(ref_payload).encode())

        monkeypatch.setattr("subprocess.run", fake_run)
        active = load_adjudicated_baseline(
            tmp_path / "forged.json", repository="o/r", ref="deadbeef" * 5 + "0011"
        )
        assert len(active) == 1
        assert active[0]["id"] == entry["id"]
        joined = " ".join(captured["cmd"])
        assert "repos/o/r/contents/" in joined
        assert "?ref=" in joined
        assert "deadbeef" in joined
        # The PR-tree path must never be read for entries
        assert (tmp_path / "forged.json").exists() is False

    def test_load_adjudicated_baseline_ref_fetch_failure_yields_no_entries(
        self, tmp_path, monkeypatch
    ):
        """BASE fetch failure = no suppression (fail toward the gate), not a
        fallback to the workspace file (that fallback IS the vulnerability)."""

        def failing_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr("subprocess.run", failing_run)
        active = load_adjudicated_baseline(
            tmp_path / "whatever.json", repository="o/r", ref="f" * 40
        )
        assert active == []

    def test_load_adjudicated_baseline_without_ref_reads_path(self, tmp_path):
        """Local/testing path (no ref) keeps reading the given path directly."""
        entry = _make_entry()
        path = _write_baseline(tmp_path, [entry])
        active = load_adjudicated_baseline(path)
        assert len(active) == 1

    def test_load_adjudicated_baseline_empty_ref_never_falls_back_to_path(self, tmp_path):
        """Empty-string ref (CI asked for the ref channel, base unresolved) →
        no entries. Falling back to the workspace path would reopen the
        forged-baseline hole for the exact runs that most need the gate."""
        entry = _make_entry()
        path = _write_baseline(tmp_path, [entry])
        active = load_adjudicated_baseline(path, repository="o/r", ref="")
        assert active == []

    def test_load_adjudicated_baseline_ref_with_empty_repository_fail_closed(self, tmp_path):
        """① P1 (round-5): ref non-empty + repository='' → empty table.

        argparse required=True still accepts an empty string, so this
        combination is reachable from the CLI. The old `if repository:` guard
        skipped the ref branch entirely and control flow fell through to the
        workspace disk read — the exact untrusted path this boundary exists to
        close (a forged workspace baseline would then suppress findings). The
        ref channel was requested; serving it from disk is the vulnerability,
        so the only safe outcome is the empty table (no suppression)."""
        entry = _make_entry()
        path = _write_baseline(tmp_path, [entry])
        active = load_adjudicated_baseline(path, repository="", ref="b" * 40)
        assert active == []

    def test_load_adjudicated_baseline_empty_repository_makes_no_gh_call(
        self, tmp_path, monkeypatch
    ):
        """The fail-closed branch must fire before any gh api attempt: with
        an empty repository there is no valid ref target to fetch from, and a
        fetch attempted against `repos//contents/...` would be nonsense."""
        import base64

        entry = _make_entry()
        path = _write_baseline(tmp_path, [entry])
        payload = {
            "content": base64.b64encode(
                json.dumps({"version": 1, "entries": [entry]}).encode()
            ).decode()
        }
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(stdout=json.dumps(payload).encode())

        monkeypatch.setattr("subprocess.run", fake_run)
        active = load_adjudicated_baseline(path, repository="", ref="b" * 40)
        assert active == []
        assert calls == [], "空 repository + 有效 ref 必须直接空表，不得发起任何 gh api 调用"

    def test_apply_uses_ref_loader_when_base_ref_given(self, tmp_path, monkeypatch):
        """apply_adjudicated_baseline forwards base_ref to the ref loader."""
        import base64

        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        ref_payload = {
            "content": base64.b64encode(
                json.dumps({"version": 1, "entries": [entry]}).encode()
            ).decode()
        }
        captured = {}

        def fake_run(cmd, **kwargs):
            joined = " ".join(cmd)
            if "/contents/" in joined and "?ref=" in joined:
                # BASE channel (baseline entries) and HEAD channel (anchor
                # windows) must be distinct fetches with distinct refs.
                if "bbbbbbbb" in joined:
                    captured["baseline_url"] = joined
                    return MagicMock(stdout=json.dumps(ref_payload).encode())
                captured["head_url"] = joined
                return MagicMock(
                    stdout=json.dumps(
                        {"content": base64.b64encode("\n".join(lines).encode()).decode()}
                    ).encode()
                )
            return MagicMock(stdout=b"")

        monkeypatch.setattr("subprocess.run", fake_run)
        # BASE-side anchor windows are read from the workspace file; pin them
        # to the fixture explicitly so the window hashes line up regardless of
        # the process cwd (production: the workspace IS the BASE checkout).
        monkeypatch.setattr("droid_review.publish_findings._read_file_lines", lambda path: lines)
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            tmp_path / "adjudicated_findings.json",
            repository="o/r",
            head_sha="a" * 40,
            base_ref="b" * 40,
        )
        assert len(suppressed) == 1
        assert active == []
        assert "repos/o/r/contents/" in captured["baseline_url"]
        assert "?ref=bbbbbbbb" in captured["baseline_url"]
        assert "?ref=aaaaaaaa" in captured["head_url"]
        assert captured["baseline_url"] != captured["head_url"]

    def test_apply_without_base_ref_no_entries_no_suppression(self, tmp_path):
        """No base ref + no file = no entries (missing-file noop, unchanged)."""
        findings = [_finding()]
        active, suppressed = apply_adjudicated_baseline(
            findings, tmp_path / "nope.json", repository="o/r", head_sha="a" * 40
        )
        assert active == findings
        assert suppressed == []

    def test_apply_ref_with_empty_repository_never_falls_back_to_disk(self, tmp_path, monkeypatch):
        """① P1 (round-5) apply 侧：ref 通道 + 空 repository 不得回落工作区。

        发布工作区里的基线文件与锚点窗口都可能是 PR 树自带内容；一旦
        apply_adjudicated_baseline(base_ref=<sha>, repository='') 落到
        load 的磁盘分支，伪造基线即可抑制 P1（门禁被绕）。断言：不抑制、
        不落盘（active 原样返回 blocking finding）。"""
        # 锚点文件按工作区相对路径读取（生产=BASE checkout 根）——同构 chdir，
        # 确保磁盘回落一旦发生，伪造基线+工作区锚点窗口真能命中（红得真实）。
        monkeypatch.chdir(tmp_path)
        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        # 伪造基线落在 --baseline 工作区路径上（漏洞利用面本体）
        _write_baseline(tmp_path, [entry])
        findings = [_finding(line=center0 + 1)]
        active, suppressed = apply_adjudicated_baseline(
            findings,
            tmp_path / "adjudicated_findings.json",
            repository="",
            head_sha="a" * 40,
            base_ref="b" * 40,
        )
        assert active == findings, "ref 通道 + 空 repository → 不得走磁盘读（伪造基线会灭 P1）"
        assert suppressed == []

    def test_aggregate_action_resolves_base_ref_from_event_context(self):
        """The action must resolve the BASE ref from server-controlled event
        context (pull_request.base.sha / GITHUB_BASE_REF), never from anything
        the PR branch can influence; unresolved base = no --baseline-ref flag
        (publish falls back to no entries)."""
        content = ACTION_YML.read_text()
        assert "github.event.pull_request.base.sha" in content
        assert "GITHUB_BASE_REF" in content
        # The publish step consumes the resolved value via env, not interpolation
        assert "BASELINE_REF" in content

    def test_aggregate_action_does_not_read_baseline_from_workspace(self):
        """The vulnerable channel is gone: no $GITHUB_WORKSPACE baseline path."""
        content = ACTION_YML.read_text()
        assert "GITHUB_WORKSPACE/scripts/droid_review" not in content


class TestMainBaselineRefPropagation:
    """① P1 (round-3): main() must not coerce the ref channel.

    `base_ref=args.baseline_ref or None` collapsed three distinct CLI states
    into one: flag omitted (local/testing disk mode), flag passed with a BASE
    sha (CI ref channel), and flag passed empty (CI ref channel, BASE
    unresolved). The empty-string case is the dangerous collapse: it is
    exactly the "CI asked for the ref channel but BASE is unresolved" state
    the round-2 fail-closed branch exists for (empty ref → empty table, never
    the workspace path), yet `or None` routed it to disk mode — so a
    workflow_dispatch on a PR-head branch with a forged workspace
    adjudicated_findings.json suppressed its own findings.

    Contract: main() forwards --baseline-ref verbatim; "omitted" and "passed
    empty" stay distinct end-to-end.
    """

    def test_baseline_ref_default_is_none_distinct_from_empty(self):
        """argparse default stays None: omitting the flag = disk mode semantics."""
        import inspect

        from droid_review import publish_findings as pf

        src = inspect.getsource(pf.main)
        assert '"--baseline-ref"' in src, "--baseline-ref flag must exist in main()"
        assert "base_ref=args.baseline_ref" in src, (
            "--baseline-ref 必须原样转发给 apply_adjudicated_baseline"
        )
        assert "args.baseline_ref or" not in src, (
            "main() 不得做 `args.baseline_ref or None` 压缩——空串必须原样"
            "传给 apply_adjudicated_baseline（None=本地磁盘模式，空串=CI "
            "fail-closed 空 table，两者语义不同，压缩即绕回信任边界）"
        )

    def test_main_empty_baseline_ref_disables_suppression(self, monkeypatch, tmp_path, capsys):
        """End-to-end red/green: CI passes --baseline-ref '' (BASE unresolved)
        and a forged baseline sits at the --baseline path → no suppression."""
        from droid_review.publish_findings import main

        # Forged baseline + fixture whose window hash matches the forged entry:
        # if the disk fallback fired, the P1 would be suppressed (gate bypassed).
        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        _write_baseline(tmp_path, [entry])
        (tmp_path / "findings-shard-0.json").write_text(
            json.dumps(
                {
                    "shard_id": 0,
                    "findings": [
                        {
                            "severity": "P1",
                            "file": ".github/workflows/qa.yml",
                            "line": center0 + 1,
                            "message": MSG_FRAGMENT,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--baseline",
                "adjudicated_findings.json",
                # CI 形态：ref 通道被显式请求但 BASE 未解析（action/workflow
                # 恒传该 flag，值可为空串）。
                "--baseline-ref",
                "",
            ],
        )
        # The empty-ref fail-closed branch must fire before any gh attempt —
        # stdout carries its marker line; downstream GitHub calls are no-ops.
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: MagicMock(stdout=b""))
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers", lambda pr, repo: ([], 0)
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        with pytest.raises(SystemExit) as excinfo:
            main()
        # The P1 finding stays active → gate red (exit 1), never suppressed.
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        # The fail-closed branch marker (never fell back to the disk path).
        assert "no BASE ref available — no suppression" in out
        assert "Adjudicated baseline: no hits" in out

    def test_main_ref_with_empty_repository_never_falls_back_to_disk(
        self, monkeypatch, tmp_path, capsys
    ):
        """① P1 (round-5) 端到端红绿：--repository '' + 有效 --baseline-ref。

        argparse required=True 不拒空串，故该组合可从 CLI 触达。旧守卫
        `if repository:` 被空串跳过 → 控制流落到工作区磁盘读（正是
        docstring 声称已堵的漏洞本体路径），伪造基线灭 P1、门禁变绿。
        修复后：空 table fail-closed，不抑制不落盘，gate 保持红。"""
        from droid_review.publish_findings import main

        # 伪造基线 + 窗口哈希命中的锚点文件：磁盘回落一旦触发，P1 即被抑制
        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        _write_baseline(tmp_path, [entry])
        (tmp_path / "findings-shard-0.json").write_text(
            json.dumps(
                {
                    "shard_id": 0,
                    "findings": [
                        {
                            "severity": "P1",
                            "file": ".github/workflows/qa.yml",
                            "line": center0 + 1,
                            "message": MSG_FRAGMENT,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                # argparse required=True 不拒空串——漏洞入口形态本体
                "--repository",
                "",
                "--baseline",
                "adjudicated_findings.json",
                # 有效 BASE sha：ref 通道被显式请求
                "--baseline-ref",
                "b" * 40,
            ],
        )
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: MagicMock(stdout=b""))
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers", lambda pr, repo: ([], 0)
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        with pytest.raises(SystemExit) as excinfo:
            main()
        # P1 保持 active → gate 红（exit 1）；伪造基线未能抑制任何 finding
        assert excinfo.value.code == 1
        out = capsys.readouterr().out
        assert "Adjudicated baseline: no hits" in out

    def test_main_omitted_baseline_ref_keeps_disk_mode(self, monkeypatch, tmp_path, capsys):
        """Flag omitted (local/testing) keeps reading the --baseline path
        directly — the documented disk-mode semantics must survive."""
        from droid_review.publish_findings import main

        target = _write_qa_fixture(tmp_path)
        lines = target.read_text().splitlines()
        center0 = next(i for i, ln in enumerate(lines) if QA_ANCHOR in ln)
        entry = _make_entry(context_sha256=_window_hash(lines, center0))
        _write_baseline(tmp_path, [entry])
        (tmp_path / "findings-shard-0.json").write_text(
            json.dumps(
                {
                    "shard_id": 0,
                    "findings": [
                        {
                            "severity": "P1",
                            "file": ".github/workflows/qa.yml",
                            "line": center0 + 1,
                            "message": MSG_FRAGMENT,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "publish_findings.py",
                "--pattern",
                "findings-shard-0.json",
                "--pr-number",
                "1",
                "--repository",
                "o/r",
                "--baseline",
                "adjudicated_findings.json",
                # no --baseline-ref → disk mode
            ],
        )
        monkeypatch.setattr("subprocess.run", lambda cmd, **kw: MagicMock(stdout=b""))
        monkeypatch.setattr(
            "droid_review.publish_findings.scan_round_markers", lambda pr, repo: ([], 0)
        )
        monkeypatch.setattr("droid_review.publish_findings.resolve_stale_bot_threads", MagicMock())
        captured = {}

        def capture_run(cmd, **kwargs):
            if "--method" in cmd and "POST" in cmd and "/issues/" in " ".join(cmd):
                captured["body"] = json.loads(kwargs["input"].decode())["body"]
            return MagicMock(stdout=b"")

        with patch("subprocess.run", side_effect=capture_run):
            main()  # suppressed locally → no blocking finding → returns without exit
        out = capsys.readouterr().out
        assert "Adjudicated baseline: 1 suppressed" in out
        assert "[adjudicated-suppressed]" in captured["body"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
