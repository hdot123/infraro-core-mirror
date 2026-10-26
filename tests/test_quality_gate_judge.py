"""Unit tests for scripts/quality_gate_judge.py (FINAL-PLAN §1.2 三出口)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.script_module_helpers import load_script_module

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE_PATH = REPO_ROOT / "scripts" / "quality_gate_judge.py"

pytestmark = pytest.mark.schema


@pytest.fixture(scope="module")
def judge():
    return load_script_module(JUDGE_PATH, "quality_gate_judge_under_test")


def _run(name: str, status: str, conclusion: str | None, run_id: int = 1) -> dict:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
    }


class TestExpectedChecks:
    def test_pr_includes_qa(self, judge) -> None:
        assert judge.expected_checks_for("pull_request") == (
            "ci-ok",
            "substrate-gate-suite",
            "qa-ok",
        )

    def test_push_drops_qa(self, judge) -> None:
        assert judge.expected_checks_for("push") == ("ci-ok", "substrate-gate-suite")


class TestThreeOutcomes:
    def test_success_both_green(self, judge) -> None:
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[
                _run("ci-ok", "completed", "success", 1),
                _run("substrate-gate-suite", "completed", "success", 2),
                _run("qa-ok", "completed", "success", 3),
            ],
        )
        assert result["outcome"] == "SUCCESS"
        assert judge.outcome_exit_code(result["outcome"]) == 0

    def test_not_ready_peer_missing(self, judge) -> None:
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[
                _run("ci-ok", "completed", "success", 1),
                _run("substrate-gate-suite", "completed", "success", 2),
            ],
        )
        assert result["outcome"] == "NOT_READY"
        assert "qa-ok" in result["not_ready"] or "qa-ok" in result["missing"]
        assert judge.outcome_exit_code(result["outcome"]) == 1

    def test_not_ready_peer_in_flight(self, judge) -> None:
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[
                _run("ci-ok", "completed", "success", 1),
                _run("substrate-gate-suite", "completed", "success", 2),
                _run("qa-ok", "in_progress", None, 3),
            ],
        )
        assert result["outcome"] == "NOT_READY"
        assert judge.outcome_exit_code(result["outcome"]) == 1

    def test_failure_peer_red(self, judge) -> None:
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[
                _run("ci-ok", "completed", "failure", 1),
                _run("substrate-gate-suite", "completed", "success", 2),
                _run("qa-ok", "completed", "success", 3),
            ],
        )
        assert result["outcome"] == "FAILURE"
        assert "ci-ok" in result["failed"]
        assert judge.outcome_exit_code(result["outcome"]) == 1

    def test_failure_beats_not_ready(self, judge) -> None:
        """已红优先于未齐：不得用 NOT_READY 掩盖质量红。"""
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[
                _run("ci-ok", "completed", "failure", 1),
                _run("substrate-gate-suite", "completed", "success", 2),
                # qa-ok missing
            ],
        )
        assert result["outcome"] == "FAILURE"

    def test_latest_by_id_prefers_newer_success(self, judge) -> None:
        result = judge.judge_gates(
            expected=("ci-ok",),
            check_runs=[
                _run("ci-ok", "completed", "failure", 10),
                _run("ci-ok", "completed", "success", 20),
            ],
        )
        assert result["outcome"] == "SUCCESS"

    def test_in_flight_beats_old_success(self, judge) -> None:
        result = judge.judge_gates(
            expected=("ci-ok",),
            check_runs=[
                _run("ci-ok", "completed", "success", 10),
                _run("ci-ok", "queued", None, 30),
            ],
        )
        assert result["outcome"] == "NOT_READY"


class TestNotReadyNotClean:
    def test_not_ready_maps_to_non_success_check(self, judge) -> None:
        """契约：NOT_READY 不得让 quality-gate 以 success/skipped 进入 CLEAN。"""
        result = judge.judge_gates(
            expected=judge.PR_EXPECTED,
            check_runs=[_run("ci-ok", "completed", "success", 1)],
        )
        assert result["outcome"] == "NOT_READY"
        # workflow yml 映射：NOT_READY → check conclusion=failure
        assert judge.outcome_exit_code("NOT_READY") == 1
        assert "SUCCESS" != result["outcome"]


class TestEmitAndMainFixture:
    def test_main_fixture_not_ready(
        self, judge, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        fixture = tmp_path / "checks.json"
        fixture.write_text(
            json.dumps(
                {
                    "check_runs": [
                        _run("ci-ok", "completed", "success", 1),
                        _run("substrate-gate-suite", "completed", "success", 2),
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc = judge.main(
            [
                "--upstream-event",
                "pull_request",
                "--pr-number-hint",
                "42",
                "--head-sha",
                "deadbeef",
                "--check-runs-json",
                str(fixture),
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "QG_RESULT=" in out
        line = [ln for ln in out.splitlines() if ln.startswith("QG_RESULT=")][-1]
        payload = json.loads(line.removeprefix("QG_RESULT="))
        assert payload["outcome"] == "NOT_READY"
        assert payload["pr_number"] == "42"

    def test_main_fixture_success_push(
        self, judge, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        fixture = tmp_path / "checks.json"
        fixture.write_text(
            json.dumps(
                {
                    "check_runs": [
                        _run("ci-ok", "completed", "success", 1),
                        _run("substrate-gate-suite", "completed", "success", 2),
                    ]
                }
            ),
            encoding="utf-8",
        )
        rc = judge.main(
            [
                "--upstream-event",
                "push",
                "--head-sha",
                "abc",
                "--check-runs-json",
                str(fixture),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(
            [ln for ln in out.splitlines() if ln.startswith("QG_RESULT=")][-1].removeprefix(
                "QG_RESULT="
            )
        )
        assert payload["outcome"] == "SUCCESS"

    def test_main_unresolved_pr_without_hint(
        self, judge, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        fixture = tmp_path / "checks.json"
        fixture.write_text(json.dumps({"check_runs": []}), encoding="utf-8")
        rc = judge.main(
            [
                "--upstream-event",
                "pull_request",
                "--head-sha",
                "abc",
                "--check-runs-json",
                str(fixture),
                "--resolve-pr",
            ]
        )
        assert rc == 1
        out = capsys.readouterr().out
        payload = json.loads(
            [ln for ln in out.splitlines() if ln.startswith("QG_RESULT=")][-1].removeprefix(
                "QG_RESULT="
            )
        )
        assert payload["outcome"] == "FAILURE"
        assert payload.get("reason") == "unresolved_pr"
