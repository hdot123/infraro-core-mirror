"""合并 FIFO 队列控制器契约（FINAL-PLAN §2）。

三要素：
  1. label 介质 merge-queue / merge-queue-cooldown
  2. 排序键 createdAt → 队头
  3. 唯一写者 = pr-queue-controller（auto-merge 只读 isQueueHead）

禁止：mergeable 无序扫合；快路径自称队头直合。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_SH = REPO_ROOT / "scripts" / "pr_queue_controller.sh"
CONTROLLER_YML = REPO_ROOT / ".github" / "workflows" / "pr-queue-controller.yml"
PIPELINE_YML = REPO_ROOT / ".github" / "workflows" / "auto-merge-pipeline.yml"
TRIAGE_SH = REPO_ROOT / "src" / "infra_core" / "shell" / "auto_merge_triage.sh"
THREE_GATES = ("substrate-gate-suite", "quality-gate", "droid-review")


def _run_controller(args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CONTROLLER_SH), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _pr(
    number: int,
    *,
    created_at: str = "2026-01-01T00:00:00Z",
    labels: list[str] | None = None,
    is_queue_head: bool = True,
    merge_state: str = "CLEAN",
) -> dict[str, Any]:
    labels = labels if labels is not None else ["merge-queue"]
    check_runs = {
        "total_count": len(THREE_GATES),
        "check_runs": [
            {
                "id": idx + 1,
                "name": gate,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-10-19T00:00:00Z",
                "not_ready": False,
            }
            for idx, gate in enumerate(THREE_GATES)
        ],
    }
    return {
        "number": number,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": merge_state,
        "isDraft": False,
        "statusCheckRollup": [
            {"name": gate, "status": "completed", "conclusion": "success"} for gate in THREE_GATES
        ],
        "labels": [{"name": name} for name in labels],
        "headRefOid": f"{number:040x}",
        "checkRuns": check_runs,
        "createdAt": created_at,
        "isCrossRepository": False,
        "isQueueHead": is_queue_head,
    }


def _run_triage(prs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = subprocess.run(
        ["bash", str(TRIAGE_SH)],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TestFifoThreeElements:
    def test_controller_script_documents_three_elements(self) -> None:
        text = CONTROLLER_SH.read_text(encoding="utf-8")
        assert "merge-queue" in text
        assert "merge-queue-cooldown" in text
        assert "createdAt" in text
        assert "唯一写者" in text

    def test_workflow_is_sole_label_writer_surface(self) -> None:
        doc = yaml.safe_load(CONTROLLER_YML.read_text(encoding="utf-8"))
        assert doc["name"] == "PR Queue Controller"
        assert "queue-tick" in doc["jobs"]
        run = CONTROLLER_YML.read_text(encoding="utf-8")
        assert "pr_queue_controller.sh tick" in run
        assert "max-parallel" not in run  # 合并单飞在 auto-merge，不在本写者

    def test_compute_head_oldest_queued_excludes_cooldown(self) -> None:
        fleet = [
            {
                "number": 2,
                "createdAt": "2026-06-01T00:00:00Z",
                "labels": [{"name": "merge-queue"}],
            },
            {
                "number": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "labels": [{"name": "merge-queue"}],
            },
            {
                "number": 3,
                "createdAt": "2025-01-01T00:00:00Z",
                "labels": [{"name": "merge-queue"}, {"name": "merge-queue-cooldown"}],
            },
        ]
        result = _run_controller(["compute-head"], json.dumps(fleet))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "1"

    def test_decide_kick_timeout_and_in_progress_guard(self) -> None:
        kick = _run_controller(
            ["decide-kick"],
            json.dumps(
                {
                    "head_since_epoch": 0,
                    "now_epoch": 10_000,
                    "head_timeout_minutes": 90,
                    "is_in_progress": False,
                    "is_legit_queued": False,
                }
            ),
        )
        assert kick.stdout.startswith("kick\t"), kick.stdout
        keep = _run_controller(
            ["decide-kick"],
            json.dumps(
                {
                    "head_since_epoch": 0,
                    "now_epoch": 10_000,
                    "head_timeout_minutes": 90,
                    "is_in_progress": True,
                    "is_legit_queued": False,
                }
            ),
        )
        assert keep.stdout.startswith("keep\tin_progress"), keep.stdout


class TestMergeableOnlyQueueHead:
    def test_non_head_green_is_pending_not_mergeable(self) -> None:
        out = _run_triage([_pr(2, is_queue_head=False, created_at="2026-06-01T00:00:00Z")])
        assert out["mergeable"] == []
        assert [p["number"] for p in out["pending"]] == [2]

    def test_only_head_among_two_greens_is_mergeable(self) -> None:
        head = _pr(1, is_queue_head=True, created_at="2026-01-01T00:00:00Z")
        other = _pr(2, is_queue_head=False, created_at="2026-06-01T00:00:00Z")
        out = _run_triage([other, head])
        assert [p["number"] for p in out["mergeable"]] == [1]
        assert 2 in [p["number"] for p in out["pending"]]

    def test_green_without_merge_queue_label_is_pending(self) -> None:
        out = _run_triage([_pr(5, labels=[], is_queue_head=True)])
        assert out["mergeable"] == []
        assert [p["number"] for p in out["pending"]] == [5]

    def test_pipeline_injects_is_queue_head_from_label_fleet(self) -> None:
        text = PIPELINE_YML.read_text(encoding="utf-8")
        assert "isQueueHead" in text
        assert "merge-queue" in text
        assert "QUEUE_HEAD" in text
        # 禁止快路径自称队头：必须从舰队计算，不得写死 true
        assert "isQueueHead: true" not in text.replace(" ", "")


class TestNotReadyNotQualityRed:
    def test_quality_not_ready_is_pending_not_stalled(self) -> None:
        pr = _pr(7)
        pr["checkRuns"]["check_runs"][1] = {
            "id": 2,
            "name": "quality-gate",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-10-19T00:00:00Z",
            "not_ready": True,
        }
        pr["statusCheckRollup"][1] = {
            "name": "quality-gate",
            "status": "completed",
            "conclusion": "failure",
        }
        out = _run_triage([pr])
        assert out["stalled"] == []
        assert [p["number"] for p in out["pending"]] == [7]
        assert out["mergeable"] == []

    def test_enrichment_preserves_not_ready_boolean(self) -> None:
        text = PIPELINE_YML.read_text(encoding="utf-8")
        assert "not_ready" in text
        assert "NOT_READY" in text


class TestMaxParallelOne:
    def test_merge_matrix_still_max_parallel_one(self) -> None:
        doc = yaml.safe_load(PIPELINE_YML.read_text(encoding="utf-8"))
        assert doc["jobs"]["auto-merge"]["strategy"]["max-parallel"] == 1
