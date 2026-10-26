"""缺席哨兵并入 watchdog 契约（FINAL-PLAN §2 哨兵 + §1.2 NOT_READY）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
SENTINEL_SH = REPO_ROOT / "scripts" / "droid_absence_sentinel.sh"
WATCHDOG_YML = REPO_ROOT / ".github" / "workflows" / "droid-review-watchdog.yml"


def _classify(payload: list[dict] | dict) -> list[dict]:
    result = subprocess.run(
        ["bash", str(SENTINEL_SH), "classify"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestAbsenceSentinelMergedIntoWatchdog:
    def test_job_exists_on_schedule_dispatch(self) -> None:
        doc = yaml.safe_load(WATCHDOG_YML.read_text(encoding="utf-8"))
        job = doc["jobs"]["absence-sentinel"]
        assert "schedule" in job["if"]
        assert "workflow_dispatch" in job["if"]
        assert "WATCHDOG_KILL_SWITCH" in job["if"]

    def test_ttl_matches_two_cron_windows_not_thirty_min_iou(self) -> None:
        text = WATCHDOG_YML.read_text(encoding="utf-8")
        assert "WATCHDOG_ABSENCE_TTL_SECONDS || '3600'" in text
        assert "droid_absence_sentinel.sh" in text
        # cron 仍为 11,41（30min 窗）；TTL=2 窗；yaml 把 on: 解析为 True 键
        on = yaml.safe_load(text)[True]
        cron = on["schedule"][0]["cron"]
        assert cron.split()[0] == "11,41"
        assert "3600" in text

    def test_no_standalone_sentinel_workflow(self) -> None:
        workflows = list((REPO_ROOT / ".github" / "workflows").glob("*sentinel*"))
        assert workflows == [], f"禁止另起哨兵炉灶: {workflows}"


class TestAbsenceClassify:
    def _green_gates(self, *, not_ready: bool = False) -> list[dict]:
        q_conc = "failure" if not_ready else "success"
        return [
            {
                "id": 1,
                "name": "substrate-gate-suite",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 2,
                "name": "quality-gate",
                "status": "completed",
                "conclusion": q_conc,
                "not_ready": not_ready,
            },
        ]

    def test_absent_past_ttl_marks_explicit_red(self) -> None:
        out = _classify(
            [
                {
                    "number": 9,
                    "head_sha": "abc",
                    "labels": [],
                    "now_epoch": 10_000,
                    "gates_green_since_epoch": 0,
                    "check_runs": self._green_gates(),
                    "droid_runs": [],
                }
            ]
        )
        assert out[0]["decision"] == "mark_absent"
        assert out[0]["action"] == "explicit_red"

    def test_does_not_kill_in_progress(self) -> None:
        runs = self._green_gates()
        runs.append({"id": 3, "name": "droid-review", "status": "in_progress", "conclusion": None})
        out = _classify(
            [
                {
                    "number": 9,
                    "labels": [],
                    "now_epoch": 10_000,
                    "gates_green_since_epoch": 0,
                    "check_runs": runs,
                    "droid_runs": [],
                }
            ]
        )
        assert out[0]["decision"] == "skip_in_progress"

    def test_within_ttl_skips_even_if_queued_label(self) -> None:
        out = _classify(
            [
                {
                    "number": 9,
                    "labels": ["merge-queue"],
                    "now_epoch": 100,
                    "gates_green_since_epoch": 0,
                    "check_runs": self._green_gates(),
                    "droid_runs": [],
                }
            ]
        )
        assert out[0]["decision"] == "skip_ttl"

    def test_not_ready_is_not_quality_red_escalation(self) -> None:
        out = _classify(
            [
                {
                    "number": 8,
                    "labels": [],
                    "now_epoch": 10_000,
                    "gates_green_since_epoch": 0,
                    "check_runs": self._green_gates(not_ready=True),
                    "droid_runs": [],
                }
            ]
        )
        assert out[0]["decision"] == "skip_not_ready"
        assert "NOT_READY" in out[0]["reason"]
