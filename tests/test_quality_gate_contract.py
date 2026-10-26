"""Quality Gate workflow structure contract (governance mission M1, VAL-CORE-002).

2026-10-25 pipeline cutover (FINAL-PLAN §1.2 / §2 QG):

* CI/QA ``workflow_run`` completed 双触发 + 单次判定（删 200×30s 轮询）
* 三出口：NOT_READY / FAILURE / SUCCESS；未齐禁 skipped / 假 success
* check 显示名仍为 ``quality-gate``；substrate-gate-suite 仍由 CI 产出（本文件不产）
* concurrency 组键含 PR 号、禁回落 head_branch/main
* NOT_READY 时 PR 不得因 quality-gate 变 CLEAN（failure 承载未齐）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.script_module_helpers import load_script_module

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
QG_YML = REPO_ROOT / ".github/workflows/quality-gate.yml"
JUDGE_PY = REPO_ROOT / "scripts/quality_gate_judge.py"


@pytest.fixture(scope="module")
def qg_doc() -> dict[str, Any]:
    doc = yaml.safe_load(QG_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "quality-gate.yml 必须是 YAML mapping"
    return doc


@pytest.fixture(scope="module")
def qg_raw() -> str:
    return QG_YML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qg_script(qg_doc: dict[str, Any]) -> str:
    """拼接 quality-gate job 全部 run 步骤脚本文本。"""
    jobs = qg_doc.get("jobs")
    assert isinstance(jobs, dict) and "quality-gate" in jobs, "必须有 quality-gate job"
    steps = jobs["quality-gate"].get("steps") or []
    runs = [str(step["run"]) for step in steps if "run" in step]
    assert runs, "quality-gate job 必须包含至少一个 run 步骤"
    return "\n".join(runs)


@pytest.fixture(scope="module")
def judge_mod():
    return load_script_module(JUDGE_PY, "quality_gate_judge_contract")


class TestQualityGateNaming:
    def test_workflow_name(self, qg_doc: dict[str, Any]) -> None:
        assert qg_doc["name"] == "Quality Gate"

    def test_check_context_is_quality_gate(self, qg_doc: dict[str, Any]) -> None:
        """job key 与显示名都是 quality-gate —— ruleset required context 精确匹配。"""
        job = qg_doc["jobs"]["quality-gate"]
        assert job.get("name") == "quality-gate", (
            "job 显示名必须字节级为 quality-gate（ruleset required context 按显示名匹配，"
            "改名 = 永久 pending 阻断合并）"
        )


class TestQualityGateTriggers:
    def test_ci_qa_workflow_run_dual_trigger(self, qg_doc: dict[str, Any]) -> None:
        """CI/QA completed + 过渡 pull_request；禁 push / 长轮询。"""
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        assert "workflow_run" in triggers, "必须有 workflow_run 触发"
        wr = triggers["workflow_run"]
        assert set(wr.get("workflows") or []) == {"CI", "QA"}
        assert wr.get("types") == ["completed"]
        # #451 鸡生蛋：workflow_run 读 main YAML；cutover 前须双持 pull_request
        # 用 PR-head YAML 铸造同名 check（单次判定，非 200×30s 长轮询）。
        assert "pull_request" in triggers, "过渡双持：pull_request 铸造 quality-gate"
        pr = triggers["pull_request"]
        assert set(pr.get("types") or []) >= {
            "opened",
            "synchronize",
            "reopened",
            "ready_for_review",
        }
        assert "push" not in triggers, "push 面由 CI workflow_run 覆盖，禁止重复触发"

    def test_no_paths_filter(self, qg_doc: dict[str, Any]) -> None:
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        for _event, config in triggers.items():
            if isinstance(config, dict):
                assert "paths" not in config
                assert "paths-ignore" not in config

    def test_not_reusable_workflow_call(self, qg_doc: dict[str, Any]) -> None:
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        assert "workflow_call" not in triggers


class TestQualityGateSingleShot:
    def test_no_long_poll_loop(self, qg_script: str, qg_raw: str) -> None:
        """禁 200×30s 轮询；单次判定。"""
        assert "MAX_ATTEMPTS" not in qg_script
        assert "MAX_ATTEMPTS" not in qg_raw
        assert "for attempt in" not in qg_script
        assert "sleep " not in qg_script
        assert "quality_gate_judge.py" in qg_script

    def test_judge_helper_exists(self) -> None:
        assert JUDGE_PY.is_file(), "scripts/quality_gate_judge.py 必须存在（单次判定真源）"


class TestQualityGateAggregationSurface:
    def test_aggregates_ci_ok_and_substrate(self, qg_script: str) -> None:
        judge = JUDGE_PY.read_text(encoding="utf-8")
        assert "ci-ok" in judge
        assert "substrate-gate-suite" in judge
        assert "quality_gate_judge.py" in qg_script

    def test_qa_surface_pr_only(self, judge_mod) -> None:
        assert "qa-ok" in judge_mod.expected_checks_for("pull_request")
        assert "qa-ok" not in judge_mod.expected_checks_for("push")

    def test_droid_review_not_in_surface(self) -> None:
        judge = JUDGE_PY.read_text(encoding="utf-8")
        assert 'PR_EXPECTED = ("ci-ok", "substrate-gate-suite", "qa-ok")' in judge
        assert "droid-review" not in judge.split("PR_EXPECTED", 1)[1].split("PUSH_EXPECTED", 1)[0]

    def test_three_outcomes_structured(self, qg_script: str) -> None:
        judge = JUDGE_PY.read_text(encoding="utf-8")
        for outcome in ("SUCCESS", "NOT_READY", "FAILURE"):
            assert f'"{outcome}"' in judge
        assert "QG_RESULT=" in judge
        assert "QG_RESULT=" in qg_script


class TestQualityGateNotReadyFailClosed:
    def test_not_ready_is_exit_one_not_skip(self, qg_doc: dict[str, Any], qg_script: str) -> None:
        """未齐必须进程失败；禁 jobs.*.if 把未齐落成 skipped。"""
        job = qg_doc["jobs"]["quality-gate"]
        job_if = str(job.get("if") or "")
        assert "NOT_READY" not in job_if
        assert "JUDGE_RC" in qg_script
        assert 'exit "$JUDGE_RC"' in qg_script

    def test_not_ready_posts_failure_not_success(self, qg_script: str) -> None:
        """NOT_READY 贴 check conclusion=failure（禁 success / skipped）。"""
        assert "NOT_READY) CHECK_CONCLUSION=failure" in qg_script
        assert "SUCCESS) CHECK_CONCLUSION=success" in qg_script
        assert '"quality-gate"' in qg_script

    def test_pull_request_skips_checks_api_write(self, qg_script: str) -> None:
        """pull_request 面禁 PATCH Actions 自建 check（403）；由原生 job 出口承载。"""
        assert '[ "$EVENT_NAME" = "pull_request" ]' in qg_script
        assert "skip Checks API write" in qg_script
        assert "gh api --method PATCH" not in qg_script
        assert "gh api --method POST" in qg_script  # workflow_run 仍 POST 到 head

    def test_not_ready_cannot_mark_pr_clean(self, judge_mod) -> None:
        """契约锁：NOT_READY → exit 1 且 check conclusion 非 success。

        ruleset / REST 三门只认 success；failure 承载未齐 ⇒ PR 不得因
        quality-gate 变 CLEAN（#358/#372 skipped≈通过 的对照面）。
        """
        result = judge_mod.judge_gates(
            expected=("ci-ok", "substrate-gate-suite", "qa-ok"),
            check_runs=[
                {"id": 1, "name": "ci-ok", "status": "completed", "conclusion": "success"},
                {
                    "id": 2,
                    "name": "substrate-gate-suite",
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
        )
        assert result["outcome"] == "NOT_READY"
        assert judge_mod.outcome_exit_code(result["outcome"]) == 1
        check_conclusion = {
            "SUCCESS": "success",
            "NOT_READY": "failure",
            "FAILURE": "failure",
        }[result["outcome"]]
        assert check_conclusion not in {"success", "skipped"}


class TestQualityGateConcurrency:
    def test_group_includes_pr_number_no_main_fallback(self, qg_doc: dict[str, Any]) -> None:
        conc = qg_doc.get("concurrency") or {}
        group = str(conc.get("group") or "")
        assert "pull_requests[0].number" in group or "pull_request.number" in group, (
            "concurrency 组键必须含 PR 号"
        )
        assert "head_branch" not in group, "禁回落 head_branch（大量样本 = main → 跨 PR 互砍）"
        assert "github.ref" not in group
        assert "run_id" in group or "github.run_id" in group
        assert re.search(r"\bmain\b", group) is None


class TestQualityGatePrHeadResolution:
    def test_uses_workflow_run_head_sha(self, qg_script: str, qg_raw: str) -> None:
        assert "workflow_run.head_sha" in qg_raw
        assert "HEAD_SHA" in qg_script
        assert "--resolve-pr" in qg_script


class TestQualityGateDroidEnqueue:
    def test_no_workflow_dispatch_enqueue_job(self, qg_doc: dict[str, Any]) -> None:
        assert "enqueue-droid-review" not in qg_doc["jobs"]
        raw = QG_YML.read_text(encoding="utf-8")
        assert "droid-review.yml/dispatches" not in raw
        assert "workflow run droid-review" not in raw

    def test_no_rest_mirror_droid_job(self, qg_doc: dict[str, Any]) -> None:
        """本文件只产 quality-gate；禁止再 REST 镜像 droid-review。"""
        assert "mirror-droid-review-to-pr-head" not in qg_doc["jobs"]
        raw = QG_YML.read_text(encoding="utf-8")
        assert 'name="droid-review"' not in raw
        assert "name: droid-review" not in raw


class TestQualityGatePermissions:
    def test_top_level_permissions(self, qg_doc: dict[str, Any]) -> None:
        perms = qg_doc.get("permissions")
        assert perms is not None
        assert perms.get("contents") == "read"
        assert perms.get("checks") == "write"
        assert perms.get("pull-requests") == "read"

    def test_job_level_permissions(self, qg_doc: dict[str, Any]) -> None:
        job = qg_doc["jobs"]["quality-gate"]
        assert job.get("permissions", {}).get("checks") == "write"
        assert job.get("permissions", {}).get("contents") == "read"


class TestQualityGateRunnerAndTimeout:
    def test_runs_on_hosted_for_short_judge(self, qg_doc: dict[str, Any]) -> None:
        """单次 API 判定上 hosted（病二）；禁长等占 pve。"""
        assert qg_doc["jobs"]["quality-gate"].get("runs-on") == "ubuntu-latest"

    def test_timeout_bounded_short(self, qg_doc: dict[str, Any]) -> None:
        timeout = qg_doc["jobs"]["quality-gate"].get("timeout-minutes")
        assert timeout is not None and timeout <= 15

    def test_fork_guard_on_workflow_run(self, qg_doc: dict[str, Any]) -> None:
        job_if = str(qg_doc["jobs"]["quality-gate"].get("if") or "")
        assert "head_repository.full_name == github.repository" in job_if
        assert "pull_request" in job_if and "push" in job_if
