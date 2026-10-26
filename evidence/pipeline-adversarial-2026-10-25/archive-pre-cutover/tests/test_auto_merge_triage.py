"""Tests for src/infra_core/shell/auto_merge_triage.sh (auto-merge blind-spot triage).

Blind-spot forms (PR #829 / INFRA-416 / INFRA-428):
1. CONFLICTING: PR silently skipped, no notification (#794 停滞 11h+)
2. BEHIND + all green: needs update-branch self-heal (#814/#819/#825/#827/#828)
3. Early-fire + no re-trigger: schedule sweep should catch BEHIND PRs
4. BLOCKED/DRAFT misclassified as mergeable → blind merge attempts
5. Checks incomplete/failed at triage time → premature or poisoned merges

Contract (v3.2.2 §3.2 三门 REST 绿判):
- Input:  JSON array of PRs via stdin (gh pr view/list --json
          number,mergeable,mergeStateStatus,isDraft,statusCheckRollup,labels,headRefOid
          + checkRuns: REST check-runs(head_sha) enrichment by the caller —
          triage 本体零 API 副作用，REST 取数由调用方完成后以 checkRuns 字段注入)
- Output: JSON triage result with classified PRs + actions
- Exit 0 always (triage never fails the workflow)
- No side effects (pure classification + command generation)
- 绿判唯一入口 = REST 三门（droid-review / quality-gate / substrate-gate-suite）
  各取「全部实例（不按 status 预过滤）」中 check-run id 最新（id 单调分配永
  非 null，round-3 ①：started_at 的 null 排序无解）且 completed 的实例
  conclusion 全 success；statusCheckRollup 仅辅助 pending（未报齐 → wait），
  禁止参与 mergeable 判定。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration


def get_script_path() -> Path:
    """Get path to auto_merge_triage.sh script."""
    return (
        Path(__file__).resolve().parent.parent
        / "src"
        / "infra_core"
        / "shell"
        / "auto_merge_triage.sh"
    )


def get_workflow_path() -> Path:
    """Workflow 载体承载 triage/notify/stalled/merge 业务体。

    B-2 收敛后业务体单源 auto-merge-pipeline.yml（auto-merge.yml 是触发壳，
    uses-only 调用 job 零 steps）。历史内联形态（PR #829/#850 期）曾是
    auto-merge.yml——载体切换见 tests/test_auto_merge_workflow_contract.py
    TestAutoMergeShellContract。
    """
    return (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "auto-merge-pipeline.yml"
    )


# v3.2.2 §3.2 三门 REST 绿判：REST check-runs(head_sha) 同步门判用的共享
# fixtures。checkRuns 字段 = REST check-runs(head_sha) 的 .check_runs 数组
# （调用方注入，triage 本体零 API 副作用）。
REQUIRED_GATE = "droid-review"
THREE_GATES = ("substrate-gate-suite", "quality-gate", "droid-review")

# --paginate 多页响应形状（gh api 落盘形态）+ completed 实例全 success
CHECK_RUNS_ALL_SUCCESS = {
    "total_count": 3,
    "check_runs": [
        {
            "id": 1,
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "started_at": f"2026-10-19T00:0{i}:00Z",
        }
        for i, name in enumerate(THREE_GATES)
    ],
}


def _check_runs_from_rollup(rollup: list, started_at: str = "2026-10-19T00:00:00Z") -> dict:
    """由 rollup fixture 派生同构 REST check-runs 形状（status/conclusion 平移）。"""
    return {
        "total_count": len(rollup),
        "check_runs": [
            {
                "id": idx + 1,
                "name": entry.get("name", f"check-{idx + 1}"),
                "status": entry.get("status", "completed"),
                "conclusion": entry.get("conclusion"),
                "started_at": started_at,
            }
            for idx, entry in enumerate(rollup)
        ],
    }


def _pr(
    number: int,
    *,
    mergeable: str = "MERGEABLE",
    state: str = "CLEAN",
    draft: bool = False,
    rollup: list | None = None,
    labels: list[str] | None = None,
    check_runs: dict | None = None,
    head_sha: str = "0123456789abcdef0123456789abcdef01234567",
) -> dict:
    """构造 gh pr view 取数形态的 PR 对象（含 REST checkRuns 富集字段）。"""
    pr = {
        "number": number,
        "mergeable": mergeable,
        "mergeStateStatus": state,
        "isDraft": draft,
        "statusCheckRollup": CHECK_RUNS_ALL_SUCCESS["check_runs"] if rollup is None else rollup,
        "labels": [{"name": name} for name in (labels or [])],
        "headRefOid": head_sha,
        "checkRuns": CHECK_RUNS_ALL_SUCCESS if check_runs is None else check_runs,
    }
    return pr


def _run_triage(prs: list, *extra_args: str) -> dict:
    """Run the triage script on a PR list and return its JSON output.

    Shared by INFRA-428 / SKIPPED-checks test classes (dedup INFRA-439/445).
    """
    result = subprocess.run(
        ["bash", str(get_script_path()), *extra_args],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    return json.loads(result.stdout)


class TestAutoMergeTriage:
    """Triage classification tests."""

    def test_script_exists_and_executable(self):
        """Script exists and is executable."""
        script = get_script_path()
        assert script.exists(), f"{script} not found"
        assert os.access(script, os.X_OK), f"{script} not executable"

    def test_empty_input_returns_empty(self):
        """Empty PR list → empty triage result."""
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input="[]",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output == {
            "mergeable": [],
            "behind": [],
            "conflicting": [],
            "pending": [],
            "stalled": [],
            "unknown": [],
            "do_not_merge": [],
        }

    def test_mergeable_pr_classified(self):
        """PR with mergeable=MERGEABLE + REST 三门全 success → mergeable list."""
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps([_pr(123)]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["mergeable"]) == 1
        assert output["mergeable"][0]["number"] == 123
        assert output["behind"] == []
        assert output["conflicting"] == []

    def test_behind_pr_classified(self):
        """PR with mergeStateStatus=BEHIND → behind list."""
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(
                [{"number": 456, "mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"}]
            ),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["behind"]) == 1
        assert output["behind"][0]["number"] == 456
        assert output["mergeable"] == []

    def test_conflicting_pr_classified(self):
        """PR with mergeable=CONFLICTING → conflicting list."""
        prs = [{"number": 789, "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["conflicting"]) == 1
        assert output["conflicting"][0]["number"] == 789
        assert output["mergeable"] == []
        assert output["behind"] == []

    def test_unknown_state_classified(self):
        """PR with mergeable=UNKNOWN → unknown list."""
        prs = [{"number": 101, "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["unknown"]) == 1
        assert output["unknown"][0]["number"] == 101

    def test_mixed_prs_classified(self):
        """Mixed PRs → correct classification into all categories."""
        prs = [
            _pr(1),
            {"number": 2, "mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"},
            {"number": 3, "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"},
            {"number": 4, "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"},
            _pr(5, state="BLOCKED"),
            _pr(6, state="DRAFT", draft=True),
        ]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert [p["number"] for p in output["mergeable"]] == [1]
        assert [p["number"] for p in output["behind"]] == [2]
        assert [p["number"] for p in output["conflicting"]] == [3]
        assert [p["number"] for p in output["unknown"]] == [4]
        assert [p["number"] for p in output["pending"]] == [5, 6]

    def test_invalid_json_exits_zero(self):
        """Invalid JSON input → exit 0 with empty result (triage never fails workflow)."""
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input="not json",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output == {
            "mergeable": [],
            "behind": [],
            "conflicting": [],
            "pending": [],
            "stalled": [],
            "unknown": [],
            "do_not_merge": [],
        }


class TestAutoMergeTriageActions:
    """Action generation tests."""

    def test_behind_pr_generates_update_branch_command(self):
        """BEHIND PR → generates update-branch command."""
        prs = [{"number": 456, "mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"}]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["behind"]) == 1
        pr = output["behind"][0]
        assert "action" in pr
        assert pr["action"] == "update-branch"

    def test_conflicting_pr_generates_notify_command(self):
        """CONFLICTING PR → generates notify action."""
        prs = [{"number": 789, "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["conflicting"]) == 1
        pr = output["conflicting"][0]
        assert "action" in pr
        assert pr["action"] == "notify"

    def test_mergeable_pr_has_merge_action(self):
        """MERGEABLE + REST 三门全 success PR → action=merge."""
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps([_pr(123)]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert len(output["mergeable"]) == 1
        pr = output["mergeable"][0]
        assert pr["action"] == "merge"


class TestAutoMergeTriageWorkflow:
    """Workflow integration tests (YAML structure)."""

    def test_auto_merge_yaml_has_triage_step(self):
        """auto-merge.yml includes triage step in schedule path."""
        import yaml

        workflow_path = get_workflow_path()
        data = yaml.safe_load(workflow_path.read_text())

        # Find the auto-merge job
        assert "jobs" in data
        assert "auto-merge" in data["jobs"]

        # Check for triage-related steps or comments
        steps = data["jobs"]["auto-merge"].get("steps", [])
        step_names = [s.get("name", "") for s in steps]

        # At least one step should mention triage, conflicting, or behind
        has_triage = any(
            "triage" in name.lower() or "conflict" in name.lower() or "behind" in name.lower()
            for name in step_names
        )
        # Or the workflow uses the triage script
        uses_triage = any("auto_merge_triage" in str(s) for s in steps)

        assert has_triage or uses_triage, f"auto-merge.yml lacks triage logic. Steps: {step_names}"

    def test_auto_merge_yaml_schedule_not_just_resolve(self):
        """Schedule path does more than just resolve (has triage/self-heal)."""
        import yaml

        workflow_path = get_workflow_path()
        data = yaml.safe_load(workflow_path.read_text())

        # The workflow should have logic beyond just resolve → merge
        # Either a triage job or triage steps in auto-merge job
        jobs = data.get("jobs", {})

        # Check if there's a dedicated triage job
        has_triage_job = any("triage" in job_name.lower() for job_name in jobs)

        # Or check if auto-merge job has triage steps
        auto_merge_steps = jobs.get("auto-merge", {}).get("steps", [])
        has_triage_steps = any(
            "triage" in str(s).lower() or "auto_merge_triage" in str(s) for s in auto_merge_steps
        )

        assert has_triage_job or has_triage_steps, (
            "auto-merge.yml schedule path lacks triage/self-heal logic"
        )


class TestAutoMergeTriageHardeningInfra416:
    """INFRA-416：triage 第一版（PR #829）落地后的生产化硬化回归防护。

    审查发现的第一版缺陷与对应硬化：
    - CONFLICTING 评论刷屏：notify 每 10 分钟重复评论 → 按 head SHA
      sentinel 幂等（VAL-T416-001/002）
    - resolve/triage 竞态红腿：并行 matrix 腿合并后 gh pr view NotFound
      → 降级 skip 而非 fail（VAL-T416-003）
    - UNKNOWN 一次性放弃：push 后 GitHub 异步计算 mergeable → 重取一次
      后交 schedule 兜底（VAL-T416-004）
    - 分类缺陷：CONFLICTING+BEHIND 同时出现时 jq 分支互斥导致 PR 掉进
      两个 category → DIRTY 优先，绝不盲合并冲突 PR（VAL-T416-005+脚本测试）
    - job 无超时 → timeout-minutes: 10（VAL-T416-006）
    """

    def _load_workflow(self) -> dict:
        import yaml

        workflow_path = get_workflow_path()
        return yaml.safe_load(workflow_path.read_text())

    def _triage_step(self, data: dict) -> dict:
        steps = data["jobs"]["auto-merge"]["steps"]
        return next(s for s in steps if s.get("name") == "Triage PR mergeable state")

    def _notify_step(self, data: dict) -> dict:
        steps = data["jobs"]["auto-merge"]["steps"]
        return next(s for s in steps if s.get("name") == "Handle CONFLICTING PR (notify)")

    # ----- VAL-T416-001/002：CONFLICTING 通知幂等 -----

    def test_notify_step_has_sentinel_dedup(self):
        """VAL-T416-001：notify 步骤按 head SHA sentinel 去重评论。"""
        data = self._load_workflow()
        run_block = self._notify_step(data)["run"]
        assert "auto-merge-conflict-" in run_block, (
            "notify 步骤必须包含 head SHA sentinel（幂等去重）"
        )
        assert "--json comments" in run_block or "--json headRefOid" in run_block, (
            "notify 步骤必须查询既有评论/head SHA"
        )

    def test_notify_skip_branch_does_not_comment(self):
        """VAL-T416-002：已有同 SHA sentinel 时跳过评论（INFRA-428 起命中分支 exit 0，见 VAL-428-003）。"""
        data = self._load_workflow()
        run_block = self._notify_step(data)["run"]
        assert "Skipping duplicate notification" in run_block, (
            "去重命中分支必须显式跳过且不调 gh pr comment"
        )
        # 去重分支在 gh pr comment 之前短路
        assert run_block.index("Skipping duplicate notification") < run_block.index("gh pr comment")

    # ----- VAL-T416-003：resolve/triage 竞态守卫 -----

    def test_triage_step_handles_pr_not_found(self):
        """VAL-T416-003：PR 被并行腿合并后 gh pr view NotFound → skip 而非红腿。"""
        data = self._load_workflow()
        run_block = self._triage_step(data)["run"]
        assert "not found" in run_block or "Could not resolve" in run_block, (
            "triage 步骤必须识别 NotFound 并降级 skip（竞态守卫）"
        )
        assert "action=skip" in run_block

    # ----- VAL-T416-004：UNKNOWN 重试 -----

    def test_triage_step_retries_unknown_state(self):
        """VAL-T416-004：mergeable=UNKNOWN 时等待重取一次，仍未知交 schedule 兜底。"""
        data = self._load_workflow()
        run_block = self._triage_step(data)["run"]
        assert '"$MERGEABLE" = "UNKNOWN"' in run_block, "triage 步骤必须对 UNKNOWN 状态做一次重取"
        # UNKNOWN 路径最终动作只能是 skip（脚本分类保证），workflow 不猜测
        assert "UNKNOWN retry" in run_block

    # ----- VAL-T416-005：分类互斥（DIRTY 优先于 BEHIND） -----

    def test_conflicting_behind_goes_to_conflicting_only(self):
        """VAL-T416-005a：CONFLICTING+BEHIND 只进 conflicting（notify），不进 behind。

        第一版 jq 的各 category select 条件互不感知，同一 PR 可同时命中
        conflicting 与 behind 两个数组。本硬化版 behind 排除 CONFLICTING，
        保证绝不 blind-merge 一个有冲突的 PR。
        """
        prs = [{"number": 7, "mergeable": "CONFLICTING", "mergeStateStatus": "BEHIND"}]
        result = subprocess.run(
            ["bash", str(get_script_path())],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert [p["number"] for p in output["conflicting"]] == [7]
        assert output["behind"] == [], (
            "CONFLICTING PR 不得进入 behind（会触发 update-branch 掩盖冲突）"
        )
        assert output["mergeable"] == []

    def test_get_action_mode_single_action(self):
        """VAL-T416-005b：--get-action 返回单一 action，workflow 不再重复 jq。"""
        prs = [{"number": 9, "mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"}]
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-action", "9"],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "update-branch"

    def test_get_action_mode_unknown_pr_skips(self):
        """--get-action 对不在输入中的 PR 返回 skip（容错）。"""
        prs = [{"number": 11, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}]
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-action", "999"],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "skip"

    def test_get_action_mode_invalid_input_skips(self):
        """--get-action 对非法 JSON 输入返回 skip（triage never fails workflow）。"""
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-action", "5"],
            input="not json",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "skip"

    # ----- VAL-T416-006：job 超时 -----

    def test_auto_merge_job_has_timeout(self):
        """VAL-T416-006：auto-merge job 限时 10 分钟，防 gh/网络挂死堆叠。"""
        data = self._load_workflow()
        assert data["jobs"]["auto-merge"].get("timeout-minutes") == 10


class TestAutoMergeTriageHardeningInfra428:
    """INFRA-428：triage 第二轮硬化——盲区分类补全 + 红腿治理 + 竞态收敛。

    2026-08-19 审计发现的残留盲区与对应硬化：
    - BLOCKED/DRAFT 掉进 mergeable → 每 10 分钟盲合并尝试 → 红腿 +
      shared action 自身失败 check 毒化 head SHA（2026-08-18 自毒死锁）
      → 新增 pending 类别，action=wait（VAL-428-001）
    - early-fire：pull_request_target(opened)/workflow_run 单工作流完成即
      触发，mergeStateStatus 异步缓存可短暂 stale-CLEAN → cross-check
      statusCheckRollup，merge 仅在 rollup 全绿时发出；空 rollup =
      check 未报齐 = fail-closed wait（VAL-428-002）
    - CONFLICTING 去重命中仍 exit 1 → 未解决冲突每 10 分钟永久红腿 →
      去重命中 exit 0，仅首次通知失败保持红色（VAL-428-003）
    - update-branch 无竞态守卫：并行腿刚更新完，本腿再调用报错红腿 →
      失败降级绿腿 skip（VAL-428-004）
    - rollup 报齐但非全绿（check 失败/残留毒化）无任何信号 → stalled
      类别 + head SHA sentinel 幂等评论（VAL-428-005）
    - 无 workflow 级并发控制：schedule + workflow_run 腿竞态（评论
      TOCTOU / update-branch 双发）→ concurrency 组排队（VAL-428-006）
    """

    def _load_workflow(self) -> dict:
        import yaml

        workflow_path = get_workflow_path()
        return yaml.safe_load(workflow_path.read_text())

    # v3.2.2 §3.2：默认全绿 fixture = REST 三门全 success + 同构 rollup。
    GREEN = [
        {"name": name, "conclusion": "SUCCESS"}
        for name in ("substrate-gate-suite", "quality-gate", "droid-review")
    ]

    # ----- VAL-428-001：BLOCKED/DRAFT → pending，绝不 merge -----

    def test_blocked_pr_waits_not_merges(self):
        """VAL-428-001a：mergeStateStatus=BLOCKED → pending/wait，不再盲合并。"""
        output = _run_triage([_pr(5, state="BLOCKED")])
        assert output["pending"][0]["number"] == 5
        assert output["pending"][0]["action"] == "wait"
        assert output["mergeable"] == [], "BLOCKED PR 不得进入 mergeable（盲合并 + 毒化 head SHA）"

    def test_draft_pr_by_flag_waits(self):
        """VAL-428-001b：isDraft=true → pending/wait（即使三门全绿）。"""
        output = _run_triage([_pr(6, draft=True)])
        assert output["pending"][0]["number"] == 6
        assert output["pending"][0]["action"] == "wait"
        assert output["mergeable"] == []

    def test_draft_pr_by_state_waits(self):
        """VAL-428-001c：mergeStateStatus=DRAFT → pending/wait。"""
        output = _run_triage([_pr(7, state="DRAFT", draft=True, rollup=[])])
        assert output["pending"][0]["number"] == 7
        assert output["mergeable"] == []

    # ----- VAL-428-002：early-fire 防护（rollup cross-check） -----

    def test_early_fire_no_rollup_waits(self):
        """VAL-428-002a：rollup 为空（check 未报）→ pending/wait，fail-closed。"""
        output = _run_triage([_pr(8, rollup=[])])
        assert output["pending"][0]["number"] == 8
        assert output["mergeable"] == [], "check 未报齐时不得 merge（early-fire 盲合并）"

    def test_early_fire_pending_check_waits(self):
        """VAL-428-002b：rollup 有 in_progress check → pending/wait。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"status": "in_progress", "conclusion": None},
        ]
        output = _run_triage([_pr(9, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["pending"][0]["number"] == 9
        assert output["mergeable"] == []

    def test_green_rollup_merges(self):
        """VAL-428-002c：MERGEABLE + REST 三门全 success → mergeable/merge（正向路径）。"""
        output = _run_triage([_pr(10)])
        assert output["mergeable"][0]["number"] == 10
        assert output["mergeable"][0]["action"] == "merge"

    def test_conflicting_wins_over_rollup(self):
        """VAL-428-002d：CONFLICTING 优先级最高，三门全绿也不掩盖冲突。"""
        output = _run_triage([_pr(11, mergeable="CONFLICTING", state="BEHIND")])
        assert output["conflicting"][0]["number"] == 11
        assert output["behind"] == [], "冲突 PR 不得 update-branch 盲合并"
        assert output["mergeable"] == []

    def test_behind_wins_over_pending_states(self):
        """VAL-428-002e：BEHIND 优先于 draft/blocked/rollup（先追平再谈其他）。"""
        output = _run_triage([_pr(12, state="BEHIND", draft=True)])
        assert output["behind"][0]["number"] == 12
        assert output["pending"] == []

    def test_unknown_mergeable_skips_even_with_rollup(self):
        """VAL-428-002f：mergeable=UNKNOWN → skip（GitHub 未算完，不猜测）。"""
        output = _run_triage([_pr(13, mergeable="UNKNOWN", state="UNKNOWN")])
        assert output["unknown"][0]["number"] == 13

    def test_workflow_triage_fetches_rollup_and_draft(self):
        """VAL-428-002g：workflow 取数必须包含 isDraft + statusCheckRollup。"""
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        triage_step = next(s for s in steps if s.get("name") == "Triage PR mergeable state")
        fetch_line = next(
            (ln for ln in triage_step["run"].splitlines() if "--json" in ln and "gh pr view" in ln),
            None,
        )
        assert fetch_line is not None, "triage 步骤未找到 gh pr view --json 取数行"
        assert "statusCheckRollup" in fetch_line, "取数缺少 statusCheckRollup（pending 辅助失效）"
        assert "isDraft" in fetch_line, "取数缺少 isDraft（draft 盲合并防护失效）"

    def test_workflow_triage_fetch_includes_created_at(self):
        """FIFO 取数契约（round-1 P1-6）：triage 取数必须含 createdAt。

        triage 的 truncate_fleet 按 .createdAt 对 behind 类排序（先来先服务），
        但取数若缺 createdAt 字段，sort_by 退化输入序 = 排序键恒空串，FIFO
        语义名存实亡。所有 gh pr view 取数行（初取 + UNKNOWN 重取）都必须
        携带 createdAt。
        """
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        triage_step = next(s for s in steps if s.get("name") == "Triage PR mergeable state")
        fetch_lines = [
            ln
            for ln in triage_step["run"].splitlines()
            if "--json" in ln and 'gh pr view "$PR_NUMBER"' in ln
        ]
        assert fetch_lines, "triage 步骤未找到 gh pr view --json 取数行"
        for ln in fetch_lines:
            assert "createdAt" in ln, f"取数缺 createdAt（FIFO sort_by 退化输入序）: {ln}"

    # ----- VAL-428-003：notify 去重命中 exit 0（红腿治理） -----

    def test_notify_dedup_hit_exits_zero(self):
        """VAL-428-003：去重命中分支必须 exit 0（通知已送达=职责完成）。"""
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        notify_step = next(s for s in steps if s.get("name") == "Handle CONFLICTING PR (notify)")
        run_block = notify_step["run"]
        assert "Skipping duplicate notification" in run_block
        segment = run_block.split("Skipping duplicate notification", 1)[1]
        segment_before_comment = segment.split("gh pr comment", 1)[0]
        assert "exit 0" in segment_before_comment, (
            "去重命中必须 exit 0，否则未解决冲突每 10 分钟产生永久红腿"
        )
        assert "exit 1" not in segment_before_comment

    # ----- VAL-428-004：update-branch 竞态守卫 -----

    def test_update_branch_race_guard(self):
        """VAL-428-004：update-branch 失败降级绿腿（并行腿竞态正常收敛）。"""
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        behind_step = next(
            s for s in steps if s.get("name") == "Handle BEHIND PR (update-branch self-heal)"
        )
        run_block = behind_step["run"]
        assert "if ! gh pr update-branch" in run_block or (
            "gh pr update-branch" in run_block and "exit 0" in run_block
        ), "update-branch 必须有失败降级分支（竞态守卫），不允许裸 exit 1"

    # ----- VAL-428-005：stalled 类别 -----

    def test_stalled_check_reported_not_success(self):
        """VAL-428-005a：rollup 报齐但含 FAILURE → stalled/wait。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]
        output = _run_triage([_pr(14, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 14
        assert output["stalled"][0]["action"] == "wait"
        assert output["mergeable"] == [], "存在失败 check 时不得 merge"

    def test_workflow_has_stalled_step_with_sentinel(self):
        """VAL-428-005b：workflow 必须有 stalled 处理步骤且幂等（sentinel 去重）。"""
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        stalled_step = next(
            (s for s in steps if s.get("name") and "stalled" in s.get("name", "").lower()),
            None,
        )
        assert stalled_step is not None, "缺少 stalled 处理步骤"
        run_block = stalled_step["run"]
        assert "auto-merge-stalled-" in run_block, "stalled 步骤必须按 head SHA sentinel 幂等"
        assert "gh pr comment" in run_block

    def test_workflow_triage_outputs_stalled_flag(self):
        """VAL-428-005c：triage 步骤输出 stalled 标志供 stalled 步骤门控。"""
        data = self._load_workflow()
        steps = data["jobs"]["auto-merge"]["steps"]
        triage_step = next(s for s in steps if s.get("name") == "Triage PR mergeable state")
        assert "stalled=" in triage_step["run"], "triage 步骤未输出 stalled 标志"
        stalled_step = next(
            s for s in steps if s.get("name") and "stalled" in s.get("name", "").lower()
        )
        assert "steps.triage.outputs.stalled" in stalled_step.get("if", ""), (
            "stalled 步骤的 if 条件未消费 stalled 标志"
        )

    # ----- VAL-428-006：workflow 级并发控制 -----

    def test_workflow_has_concurrency_group(self):
        """VAL-428-006：workflow 必须有 concurrency 组（消除跨 run 竞态窗口）。

        B-2 收敛后并发面归壳：auto-merge.yml 顶层 group 'auto-merge-pipeline'
        （INFRA-626 禁 reusable 自带顶层 concurrency——与 caller 组名组合会
        触发 GitHub run 级自死锁检测）。组名等价由
        TestAutoMergeShellContract.test_shell_concurrency_group_unchanged 锁定。
        """
        shell = yaml.safe_load(
            (
                Path(__file__).resolve().parent.parent / ".github/workflows/auto-merge.yml"
            ).read_text()
        )
        concurrency = shell.get("concurrency")
        assert concurrency is not None, (
            "缺少 workflow 级 concurrency（评论 TOCTOU/update-branch 双发）"
        )
        assert concurrency.get("group") == "auto-merge-pipeline"
        assert concurrency.get("cancel-in-progress") is False

    # ----- --get-category 模式 -----

    def test_get_category_mode(self):
        """--get-category 返回类别名（stalled 辅助输出的事实源）。"""
        rollup = [{"conclusion": "FAILURE"}]
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "14"],
            input=json.dumps([_pr(14, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "stalled"

    def test_get_category_missing_pr_unknown(self):
        """--get-category 对不在输入中的 PR 返回 unknown（容错）。"""
        prs = [{"number": 1, "mergeable": "MERGEABLE", "mergeStateStatus": "BEHIND"}]
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "999"],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "unknown"

    def test_get_category_invalid_input_unknown(self):
        """--get-category 对非法 JSON 输入返回 unknown（never fails workflow）。"""
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "5"],
            input="not json",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "unknown"

    # ----- 防退化：分类完备性 -----

    def test_categories_are_exhaustive_and_exclusive(self):
        """全状态矩阵每个 PR 恰好落一个类别（分类完备性 + 互斥性）。

        v3.2.2 §3.2：rollup 全 SUCCESS 仅在 REST 三门全 success 时可判
        mergeable（绿判唯一入口）；未报齐 → pending；报齐非全绿 → stalled。
        v3.2.2 §3.4（VAL-M1-005）：矩阵含两个 behind 行（#6/#7），default
        模式输出前按 createdAt 截 behind=1——#7 createdAt 较新被剔除，其
        「BEHIND 优先于 draft」分类优先级改由 --get-category 单 PR 判定
        验证（test_categories_behind_priority_via_get_category）。
        """
        matrix = [
            # (mergeable, mergeStateStatus, isDraft, rollup) -> 期望类别
            ("MERGEABLE", "CLEAN", False, self.GREEN),  # mergeable（三门 REST 全 success）
            ("MERGEABLE", "CLEAN", False, []),  # pending (early-fire)
            ("MERGEABLE", "CLEAN", False, [{"conclusion": "FAILURE"}]),  # stalled
            ("MERGEABLE", "CLEAN", False, [{"status": "in_progress"}]),  # pending
            ("MERGEABLE", "CLEAN", False, [{"conclusion": "SUCCESS"}]),  # stalled（缺三门 REST 绿）
            ("MERGEABLE", "BEHIND", False, self.GREEN),  # behind
            ("MERGEABLE", "BEHIND", True, []),  # behind (BEHIND 优先)
            ("MERGEABLE", "BLOCKED", False, self.GREEN),  # pending
            ("MERGEABLE", "DRAFT", True, []),  # pending
            ("CONFLICTING", "BEHIND", False, self.GREEN),  # conflicting
            ("CONFLICTING", "CLEAN", False, self.GREEN),  # conflicting
            ("UNKNOWN", "UNKNOWN", False, []),  # unknown
            ("UNKNOWN", "BLOCKED", False, self.GREEN),  # unknown (UNKNOWN 优先)
        ]
        expected = [
            "mergeable",
            "pending",
            "stalled",
            "pending",
            "stalled",
            "behind",
            "behind",
            "pending",
            "pending",
            "conflicting",
            "conflicting",
            "unknown",
            "unknown",
        ]

        def _rest_for(rollup: list) -> dict:
            # REST 形状与 rollup 同构（本矩阵考察分类优先级，非绿判本身）；
            # conclusion 统一小写（REST API 实际形态，rollup 为 GraphQL 大写形态）。
            lowered = [
                {
                    **entry,
                    "conclusion": (
                        entry["conclusion"].lower()
                        if entry.get("conclusion")
                        else entry.get("conclusion")
                    ),
                }
                for entry in rollup
            ]
            return _check_runs_from_rollup(lowered)

        prs = [
            _pr(i + 1, mergeable=m, state=s, draft=d, rollup=r, check_runs=_rest_for(r))
            for i, (m, s, d, r) in enumerate(matrix)
        ]
        # §3.4 截断按 createdAt 排序：显式赋值使最老（#6）可预期胜出
        prs[5]["createdAt"] = "2026-01-01T00:00:00Z"
        prs[6]["createdAt"] = "2026-02-01T00:00:00Z"
        output = _run_triage(prs)
        category_numbers: dict[str, list[int]] = {
            cat: [p["number"] for p in prs_] for cat, prs_ in output.items()
        }
        for idx, exp in enumerate(expected):
            if exp == "behind" and (idx + 1) == 7:
                # §3.4：第二个 behind 行被截断剔除（已有更老 behind 在场）
                found = [cat for cat, nums in category_numbers.items() if (idx + 1) in nums]
                assert found == [], f"PR #{idx + 1} 应被 behind=1 截断剔除，实际 {found}"
                continue
            found = [cat for cat, nums in category_numbers.items() if (idx + 1) in nums]
            assert found == [exp], (
                f"PR #{idx + 1} (mergeable={matrix[idx][0]}, state={matrix[idx][1]}, "
                f"draft={matrix[idx][2]}, rollup={matrix[idx][3]}) 应为 {exp}，实际 {found}"
            )

    def test_categories_behind_priority_via_get_category(self):
        """--get-category 单 PR 判定不受 fleet 截断影响（BEHIND 优先于 draft）。

        §3.4 截断发生在 default 输出面；分类优先级（BEHIND > isDraft）是
        classify_pr 的单 PR 属性，须经单 PR 查询路径独立锁定。
        """
        pr = _pr(7, state="BEHIND", draft=True, rollup=[])
        pr["createdAt"] = "2026-02-01T00:00:00Z"
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "7"],
            input=json.dumps([pr]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "behind"


def _behind_pr(number: int, created_at: str) -> dict:
    """构造 behind 类 PR（REST 三门全绿背景；createdAt 是 fleet 截断排序键）。"""
    pr = _pr(number, state="BEHIND")
    pr["createdAt"] = created_at
    return pr


class TestGetActionCategoryTruncationObservability:
    """round-6 P1（shard-9 ②）：--get-action/--get-category 必须可观测真实分类。

    truncate_fleet 只服务 default 模式的 FIFO 合并出口（behind=1 槽 +
    max-parallel:1 串行）；查询模式的职责是回答「该 PR 的真实分类是什么」，
    与「fleet 本轮要不要执行它」是两个问题。截断发生在查询模式会把被截
    behind PR 的分类改写成 unknown/skip——调用方无法区分「真 skip」与
    「本轮被 FIFO 推迟」。修复语义：查询模式跳过 truncate_fleet（全量分类）；
    default 模式截断语义不变（FIFO 不回摆，由 test_fifo_trust_gates 锁定）。
    """

    def test_get_action_truncated_behind_reports_update_branch(self):
        """被截掉的 younger behind PR 查询 action 必须报 update-branch（非 skip）。"""
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-action", "202"],
            input=json.dumps(
                [_behind_pr(101, "2026-01-01T00:00:00Z"), _behind_pr(202, "2026-02-01T00:00:00Z")]
            ),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "update-branch", (
            "FIFO 截断只影响 default 出口执行面；--get-action 必须可观测被截 PR 的"
            "真实分类（skip = 无法区分真 skip 与本轮被推迟）"
        )

    def test_get_category_truncated_behind_reports_behind(self):
        """被截掉的 younger behind PR 查询 category 必须报 behind（非 unknown）。"""
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "202"],
            input=json.dumps(
                [_behind_pr(101, "2026-01-01T00:00:00Z"), _behind_pr(202, "2026-02-01T00:00:00Z")]
            ),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "behind"

    def test_default_output_keeps_behind_truncation(self):
        """查询模式不截断 ≠ 截断删除：default 模式仍只留最老 1 个 behind（FIFO 不回摆）。"""
        output = _run_triage(
            [_behind_pr(101, "2026-01-01T00:00:00Z"), _behind_pr(202, "2026-02-01T00:00:00Z")]
        )
        assert [p["number"] for p in output["behind"]] == [101]
        for category, entries in output.items():
            assert all(p["number"] != 202 for p in entries), (
                f"default 模式 younger behind PR 仍须被截掉（{category} 不应含 202）"
            )

    def test_get_category_fork_behind_reports_behind(self):
        """查询模式下 fork BEHIND 也报 behind（资格过滤是 default 截断面的事）。

        资格过滤（fork/do-not-merge 剔出 behind 槽位竞争）只发生在 default
        模式的 truncate_fleet；查询模式回答的是分类事实——mergeStateStatus=
        BEHIND 的 PR 分类就是 behind，与单 PR 输入语义
        （test_categories_behind_priority_via_get_category）一致。
        """
        fork = _behind_pr(303, "2026-03-01T00:00:00Z")
        fork["isCrossRepository"] = True
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "303"],
            input=json.dumps([_behind_pr(101, "2026-01-01T00:00:00Z"), fork]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "behind"


class TestLabelsShapeInputDefense:
    """round-6 P1（shard-9 ③）：labels 标量形状不得塌缩全部分类。

    取数形状漂移（labels 落成字符串/数字）时，labels[] 遍历使整个 jq 程序
    失败 → EMPTY_RESULT → 全部 PR 分类静默归零（mergeable 输出为空不携带
    「为什么」）。修复语义：两处 labels 消费（eligible_for_update_branch 与
    has_do_not_merge）都按「labels // [] | map(.name? // empty)」归一——
    非数组标量与「无标签」同义：不命中 do-not-merge，分类照常进行。
    """

    @staticmethod
    def _get_category(pr: dict) -> str:
        result = subprocess.run(
            ["bash", str(get_script_path()), "--get-category", "101"],
            input=json.dumps([pr]),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        return result.stdout.strip()

    def test_string_labels_do_not_collapse_classification(self):
        pr = _pr(101, state="BEHIND")
        pr["labels"] = "not-an-array"
        assert self._get_category(pr) == "behind", (
            "labels 为字符串时分类必须照常进行（按无标签归一），不得整程序失败"
            "塌缩到 EMPTY_RESULT/unknown"
        )

    def test_numeric_labels_do_not_collapse_classification(self):
        pr = _pr(101, state="BEHIND")
        pr["labels"] = 42
        assert self._get_category(pr) == "behind"

    def test_empty_string_labels_do_not_collapse_classification(self):
        pr = _pr(101, state="BEHIND")
        pr["labels"] = ""
        assert self._get_category(pr) == "behind", (
            "空串 labels 在 has_do_not_merge 的 .[] 遍历上同样炸穿整个 jq——"
            "防御必须覆盖两处 labels 消费"
        )

    def test_scalar_labels_default_output_stays_total(self):
        """default 模式同样不塌缩：分类总数保持（EMPTY_RESULT 是静默归零形态）。"""
        pr = _pr(101, state="BEHIND")
        pr["labels"] = "not-an-array"
        pr["createdAt"] = "2026-01-01T00:00:00Z"
        output = _run_triage([pr])
        assert set(output) == {
            "mergeable",
            "behind",
            "conflicting",
            "pending",
            "stalled",
            "unknown",
            "do_not_merge",
        }
        assert [p["number"] for p in output["behind"]] == [101]

    def test_mixed_null_label_entry_ignored(self):
        """行为保持：labels 数组内的 null 条目按无名字处理（不命中 do-not-merge）。"""
        pr = _pr(101, state="BEHIND")
        pr["labels"] = [{"name": "bug"}, None]
        assert self._get_category(pr) == "behind"


class TestAutoMergeTriageSkippedChecks:
    """v3.2.2 §3.2 三门 REST 绿判：SKIPPED 不再以任何形式参与绿判。

    历史（INFRA-428 / PR #862 时代）：rollup 判定接受 SKIPPED/NEUTRAL 为非失败，
    辅以 any(SUCCESS) 防假绿下限——该语义基于 job 级 if 产生的 SKIPPED 在
    Ruleset required 判定中等价于通过的漏洞（#372 假绿合入实证）。

    终态（v3.2.2 §3.2）：绿判唯一入口 = REST check-runs(head_sha) 三门
    （substrate-gate-suite / quality-gate / droid-review）各取 started_at 最新
    且 completed 的实例 conclusion 全 success。statusCheckRollup 仅辅助
    pending（未报齐 → wait）；checks_green(rollup) 从 mergeable 判定路径删除。

    判定矩阵（rollup 维度）：
    - pending：checks 未报齐（null conclusion / 空 rollup）→ wait
    - 三门齐全全 success（REST）→ mergeable
    - 报齐但三门未全 success（SKIPPED/FAILURE/缺门，含旁路 SUCCESS）→ 非 mergeable
    """

    # ----- 判定矩阵：SKIPPED 场景 -----

    def test_success_plus_skipped_is_green(self):
        """REST 三门全 success + rollup 含旁路 SKIPPED → mergeable（三门绿判唯一入口）。

        v3.2.2 §3.2：夜间 job 在 PR 事件报 SKIPPED 属旁路层噪音，不再参与
        绿判；mergeable 只看 REST 三门（checkRuns 全 success）。
        """
        rollup = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "SKIPPED"},  # 夜间 job 在 PR 事件报 SKIPPED
            {"conclusion": "SUCCESS"},
        ]
        output = _run_triage([_pr(100, rollup=rollup)])
        assert output["mergeable"][0]["number"] == 100, (
            "REST 三门全 success 时旁路 SKIPPED 不得阻塞合并（绿判唯一入口=三门）"
        )
        assert output["mergeable"][0]["action"] == "merge"
        assert output["stalled"] == [], "旁路 SKIPPED 不应触发 stalled"

    def test_all_skipped_is_not_mergeable(self):
        """防假绿：三门 SKIPPED（REST）→ 非 mergeable。

        v3.2.2 §3.2 / VAL-M1-004：SKIPPED 在 Ruleset required 判定中等价于
        通过的旁路已封死——skipped/neutral/missing/pending 一律非 mergeable。
        """
        rollup = [{"conclusion": "SKIPPED"}, {"conclusion": "SKIPPED"}]
        output = _run_triage([_pr(101, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["mergeable"] == [], (
            "三门 SKIPPED 不得判 mergeable（REST 三门 conclusion 全 success 才绿）"
        )
        assert len(output["stalled"]) == 1 or len(output["pending"]) == 1

    def test_skipped_plus_failure_is_stalled(self):
        """FAILURE 仍阻塞合并 → 非 mergeable。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "SKIPPED"},
            {"conclusion": "FAILURE"},
        ]
        output = _run_triage([_pr(102, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 102, (
            "FAILURE 仍应触发 stalled（SKIPPED 不应掩盖真实失败）"
        )
        assert output["mergeable"] == []

    def test_failure_only_is_stalled(self):
        """仅 FAILURE → stalled（失败结论阻塞合并）。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]
        output = _run_triage([_pr(103, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 103
        assert output["mergeable"] == []

    def test_timed_out_is_stalled(self):
        """TIMED_OUT → stalled（失败结论之一）。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "TIMED_OUT"}]
        output = _run_triage([_pr(104, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 104
        assert output["mergeable"] == []

    def test_cancelled_is_stalled(self):
        """CANCELLED → stalled（失败结论之一）。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "CANCELLED"}]
        output = _run_triage([_pr(105, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 105
        assert output["mergeable"] == []

    def test_action_required_is_stalled(self):
        """ACTION_REQUIRED → stalled（失败结论之一）。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "ACTION_REQUIRED"}]
        output = _run_triage([_pr(106, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 106
        assert output["mergeable"] == []

    # ----- 判定矩阵：NEUTRAL 场景 -----

    def test_success_plus_neutral_is_green(self):
        """REST 三门全 success + 旁路 NEUTRAL → mergeable（NEUTRAL 不参与绿判）。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "NEUTRAL"},  # 某些 check 可能报 NEUTRAL
            {"conclusion": "SUCCESS"},
        ]
        output = _run_triage([_pr(107, rollup=rollup)])
        assert output["mergeable"][0]["number"] == 107
        assert output["stalled"] == []

    def test_all_neutral_is_not_mergeable(self):
        """防假绿：三门 NEUTRAL（REST）→ 非 mergeable。

        skipped/neutral 单独存在时不代表三门被验证过（v3.2.2 §3.2 落定）。
        """
        rollup = [{"conclusion": "NEUTRAL"}, {"conclusion": "NEUTRAL"}]
        output = _run_triage([_pr(108, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["mergeable"] == [], (
            "三门 NEUTRAL 不得判 mergeable（REST 三门 conclusion 全 success 才绿）"
        )
        assert len(output["stalled"]) == 1 or len(output["pending"]) == 1

    def test_neutral_plus_failure_is_stalled(self):
        """NEUTRAL + FAILURE → stalled（FAILURE 仍阻塞）。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "NEUTRAL"},
            {"conclusion": "FAILURE"},
        ]
        output = _run_triage([_pr(109, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["stalled"][0]["number"] == 109
        assert output["mergeable"] == []

    # ----- 混合场景：SKIPPED + NEUTRAL + 失败结论 -----

    def test_complex_matrix_real_world_scenario(self):
        """真实场景：REST 三门全 success + 旁路 SKIPPED/NEUTRAL → mergeable。"""
        rollup = [
            {"conclusion": "SUCCESS"},  # CI
            {"conclusion": "SUCCESS"},  # Droid Auto Review
            {"conclusion": "SKIPPED"},  # Coverage Audit（夜间 job）
            {"conclusion": "SKIPPED"},  # Full Regression（夜间 job）
            {"conclusion": "NEUTRAL"},  # 某些 lint check
            {"conclusion": "SUCCESS"},  # Evolution Governance
        ]
        output = _run_triage([_pr(110, rollup=rollup)])
        assert output["mergeable"][0]["number"] == 110, (
            "真实 PR 场景（REST 三门全 success + 旁路 SKIPPED/NEUTRAL）应判 mergeable"
        )
        assert output["mergeable"][0]["action"] == "merge"
        assert output["stalled"] == []

    def test_regression_old_behavior_preserved(self):
        """回归测试：REST 三门全 success + 纯 SUCCESS rollup 仍判 mergeable。"""
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]
        output = _run_triage([_pr(111, rollup=rollup)])
        assert output["mergeable"][0]["number"] == 111
        assert output["mergeable"][0]["action"] == "merge"

    def test_in_progress_plus_skipped_still_pending(self):
        """in_progress + SKIPPED → pending（checks 未报齐，fail-closed 辅助 pending）。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "SKIPPED"},
            {"status": "in_progress", "conclusion": None},
        ]
        output = _run_triage([_pr(112, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert output["pending"][0]["number"] == 112, (
            "in_progress check 未报齐时应判 pending（statusCheckRollup 辅助 pending）"
        )
        assert output["mergeable"] == []

    # ----- VAL-M1-004：旁路 SUCCESS 不再绿（必测） -----

    def test_three_gates_skipped_with_parasitic_success_not_mergeable(self):
        """必测（VAL-M1-004 / v3.2.2 §3.2）：三门 SKIPPED + rollup 存在旁路 SUCCESS
        （Auto-merge/Reconcile 等无关 SUCCESS）→ mergeable=false。

        #372 假绿合入（mergeCommit f1d8d94）的契约化封堵：旧 checks_green 的
        any(SUCCESS) 被无关 workflow SUCCESS 满足、SKIPPED 在 Ruleset required
        判定又等价于通过，无标签 PR 零检查合入。终态语义：绿判唯一入口 =
        REST check-runs(head_sha) 三门各取 started_at 最新且 completed 的
        conclusion 全 success；本用例 rollup 含旁路 SUCCESS 且 REST 三门
        全 SKIPPED，任何路径都不得判 mergeable。
        """
        gate_rows = [
            {"name": "substrate-gate-suite", "conclusion": "skipped"},
            {"name": "quality-gate", "conclusion": "skipped"},
            {"name": "droid-review", "conclusion": "skipped"},
            {"name": "Auto Merge", "conclusion": "success"},
            {"name": "Reconcile", "conclusion": "success"},
        ]
        output = _run_triage(
            [
                _pr(
                    300,
                    rollup=[
                        # 三门 job 全 SKIPPED（job 级 if 拆门前的历史形态）
                        {"name": "substrate-gate-suite", "conclusion": "SKIPPED"},
                        {"name": "quality-gate", "conclusion": "SKIPPED"},
                        {"name": "droid-review", "conclusion": "SKIPPED"},
                        # 旁路 SUCCESS：无关 workflow（Auto-merge/Reconcile 等）
                        {"name": "Auto Merge", "conclusion": "SUCCESS"},
                        {"name": "Reconcile", "conclusion": "SUCCESS"},
                    ],
                    check_runs={
                        "total_count": 5,
                        "check_runs": [
                            {
                                "id": idx + 1,
                                "name": row["name"],
                                "status": "completed",
                                "conclusion": row["conclusion"],
                                "started_at": "2026-10-19T00:00:00Z",
                            }
                            for idx, row in enumerate(gate_rows)
                        ],
                    },
                )
            ]
        )
        assert output["mergeable"] == [], (
            "三门 SKIPPED + 旁路 SUCCESS（Auto-merge/Reconcile 等）不得判 mergeable"
            "（#372 假绿合入的契约化封堵）"
        )
        # 三门 skipped = REST 报齐但非全 success → stalled（wait + escalate）
        assert [p["number"] for p in output["stalled"]] == [300], (
            "三门 SKIPPED（报齐非全 success）应判 stalled 而非 pending/silent"
        )

    def test_three_gates_missing_one_rest_not_mergeable(self):
        """三门缺一（REST 两门 success、一门缺失/未报）→ 非 mergeable。

        v3.2.2 §3.2：skipped/neutral/missing/pending 一律非 mergeable；
        任何一门无 completed 实例即非绿。
        """
        check_runs = {
            "total_count": 2,
            "check_runs": [
                {
                    "id": 1,
                    "name": "substrate-gate-suite",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 2,
                    "name": "quality-gate",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:01:00Z",
                },
            ],
        }
        output = _run_triage([_pr(301, check_runs=check_runs)])
        assert output["mergeable"] == [], "三门缺一（droid-review 缺失）不得判 mergeable"

    def test_three_gates_latest_started_at_wins(self):
        """三门各取 started_at 最新且 completed 的实例（rerun 历史实例不污染绿判）。

        droid-review 先 failure 后 rerun success：取 started_at 最新（completed）
        实例 → 三门全 success → mergeable。反向用例（最新实例 failure）见
        stalled 矩阵——取数语义唯一：按名分组、started_at 最新、completed。
        """
        check_runs = {
            "total_count": 5,
            "check_runs": [
                {
                    "id": 1,
                    "name": "substrate-gate-suite",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 2,
                    "name": "quality-gate",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 3,
                    "name": "droid-review",
                    "status": "completed",
                    "conclusion": "failure",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 4,
                    "name": "droid-review",
                    "status": "in_progress",
                    "conclusion": None,
                    "started_at": "2026-10-19T00:05:00Z",
                },
            ],
        }
        # 最新 droid-review 实例 in_progress（未 completed）→ completed 实例里
        # 最新的是 failure → 三门非全 success → 非 mergeable
        output = _run_triage([_pr(302, check_runs=check_runs)])
        assert output["mergeable"] == [], "最新 completed 实例为 failure 时不得判 mergeable"

    def test_three_gates_latest_started_at_wins_semantics_note(self):
        """（round-2 语义修订占位）旧注释表述「completed 实例里最新」已由
        TestRestThreeGatesInstanceSelection 的 fail-closed 语义取代：
        最新实例在全部实例（含 in_progress/queued）中选取，不再先滤 completed。
        """
        check_runs = {
            "total_count": 4,
            "check_runs": [
                {
                    "id": 1,
                    "name": "substrate-gate-suite",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 2,
                    "name": "quality-gate",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 3,
                    "name": "droid-review",
                    "status": "completed",
                    "conclusion": "failure",
                    "started_at": "2026-10-19T00:00:00Z",
                },
                {
                    "id": 4,
                    "name": "droid-review",
                    "status": "queued",
                    "conclusion": None,
                    "started_at": "2026-10-19T00:06:00Z",
                },
            ],
        }
        # 最新实例 queued（未完成）→ 三门非全 success → 非 mergeable；
        # round-3 ②：红扫描亦 latest-per-name——FAILURE 残留被更新的未报齐
        # 实例（queued，最新）取代 → 不再 stalled，落 pending（未报齐语义）。
        rollup = [
            {"name": "substrate-gate-suite", "conclusion": "SUCCESS"},
            {"name": "quality-gate", "conclusion": "SUCCESS"},
            {"name": "droid-review", "conclusion": "FAILURE"},
            {"name": "droid-review", "conclusion": None},
        ]
        output = _run_triage([_pr(303, rollup=rollup, check_runs=check_runs)])
        assert output["mergeable"] == [], "最新实例 queued（未完成）时不得判 mergeable"
        assert [p["number"] for p in output["pending"]] == [303]
        assert [p["number"] for p in output["stalled"]] == []


class TestRestThreeGatesInstanceSelection:
    """Round-2 P1 #6/#7（#381 终局分级）+ Round-3 ① 修订：实例选择语义。

    失效模式（62ac55c 基线，triage 双载体与 action.yml 同构缺陷）：
    - #6 fail-open：先按 status=="completed" 过滤再取 started_at 最新——同门
      更新的 in_progress/queued 实例被完全忽略，旧 success 伪装成「最新实例」
      → 误判 mergeable 合入（labeled/ready_for_review 重触发窗口，与
      「pending → 非 mergeable」规格相反）。
    - #7 fail-open：jq sort_by(.started_at) 把 null 排最前——completed 且
      conclusion=cancelled 的实例常带 null started_at，取消的新实例被当作
      「最旧」，更早的旧 success 反而赢得 latest → 误判 mergeable（#372 同类）。
    - Round-3 ①（#168 类回摆）：8afdb92 的 9999 哨兵把 started_at=null 一律
      视为最新——旧 cancelled 实例（取消即取消，永不有 started_at）压过一切
      更新实例 → 永久拒合。时间戳列（started_at/completed_at）都存在
      null-and-never-populated 语义，无法同时满足 #6/#7 与 ①。

    修复语义（fail-closed，排序键换轴）：按 check-run id（GitHub 单调分配、
    永非 null）取全部实例（不按 status 预过滤）中最新者，仅当该实例
    status=completed 且 conclusion=success 才算绿。id 单调性同时保证：
    #6（新 in_progress id 更大 → 压制旧 success）、#7（新 cancelled id 更大
    → 压制旧 success）、①（旧 null-cancelled id 更小 → 被新 success 取代）。
    """

    def _check_runs_with_newer_instance(
        self, gate: str, newer: dict, base_started: str = "2026-10-19T00:00:00Z"
    ) -> dict:
        """三门旧 completed success + 指定门一条更新实例。"""
        runs = [
            {
                "id": idx + 1,
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "started_at": base_started,
            }
            for idx, name in enumerate(THREE_GATES)
        ]
        runs.append({"id": 99, "name": gate, **newer})
        return {"total_count": len(runs), "check_runs": runs}

    def test_newer_in_progress_instance_blocks_merge(self):
        """#6：同门更新的 in_progress 实例存在时，旧 completed success 不得赢。"""
        check_runs = self._check_runs_with_newer_instance(
            "droid-review",
            {"status": "in_progress", "conclusion": None, "started_at": "2026-10-19T00:05:00Z"},
        )
        # rollup 与 REST 同窗（真实形态：in_progress 未报齐 → pending）
        rollup = [
            {"name": "substrate-gate-suite", "conclusion": "SUCCESS"},
            {"name": "quality-gate", "conclusion": "SUCCESS"},
            {"name": "droid-review", "conclusion": "SUCCESS"},
            {"name": "droid-review", "conclusion": None},
        ]
        output = _run_triage([_pr(400, rollup=rollup, check_runs=check_runs)])
        assert output["mergeable"] == [], (
            "更新的 in_progress 实例存在时旧 success 不得判 mergeable（#372 同类 fail-open）"
        )
        assert [p["number"] for p in output["pending"]] == [400], (
            "REST 最新实例未完成 → checks 未报齐 → pending（wait），下一轮 sweep 收敛"
        )

    def test_newer_queued_instance_blocks_merge(self):
        """#6 变体：更新的 queued 实例同样压制旧 success。"""
        check_runs = self._check_runs_with_newer_instance(
            "quality-gate",
            {"status": "queued", "conclusion": None, "started_at": "2026-10-19T00:05:00Z"},
        )
        output = _run_triage([_pr(401, check_runs=check_runs)])
        assert output["mergeable"] == [], "更新的 queued 实例存在时旧 success 不得判 mergeable"

    def test_newer_cancelled_instance_blocks_merge(self):
        """#7：最新实例 completed+cancelled → 非绿（旧 success 不得顶替）。

        cancelled 是真实终态（编排取消/rerun 竞争），只要它是该门最新实例
        （id 更大），绝不允许被更旧的 success 实例冒名顶替判绿。
        """
        check_runs = self._check_runs_with_newer_instance(
            "droid-review",
            {
                "status": "completed",
                "conclusion": "cancelled",
                "started_at": "2026-10-19T00:05:00Z",
            },
        )
        output = _run_triage([_pr(402, check_runs=check_runs)])
        assert output["mergeable"] == [], "最新实例 cancelled 时旧 success 不得判 mergeable"
        # REST 报齐非全 success → stalled（wait + escalate），显面而非静默
        assert [p["number"] for p in output["stalled"]] == [402]

    def test_null_started_at_newer_instance_blocks_merge(self):
        """#7 核心：新实例（id 更大）completed 且 started_at=null → 门判实例。

        jq sort_by 把 null 排最前（旧实现）→ 取消的未开跑实例被当作最旧 →
        旧 success 冒充 latest → mergeable（#372 同类 fail-open）。id 排序
        修复后：该实例 id 更大即最新 → 成为门判实例 → 非绿。
        """
        check_runs = self._check_runs_with_newer_instance(
            "quality-gate",
            {"status": "completed", "conclusion": "cancelled", "started_at": None},
        )
        output = _run_triage([_pr(403, check_runs=check_runs)])
        assert output["mergeable"] == [], (
            "started_at=null 的 completed 实例不得被排到最旧（null 不排最旧，#372 同类 fail-open）"
        )
        assert [p["number"] for p in output["stalled"]] == [403]

    def test_all_latest_success_still_mergeable(self):
        """keep-green：全部实例中最新者（id 最大）completed+success → mergeable。

        rerun 收敛后的正常绿形态不受新语义影响（id 排序确定性）。
        """
        check_runs = self._check_runs_with_newer_instance(
            "droid-review",
            {
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-10-19T00:05:00Z",
            },
        )
        output = _run_triage([_pr(404, check_runs=check_runs)])
        assert [p["number"] for p in output["mergeable"]] == [404], (
            "最新实例 completed+success 必须照常判绿（语义收紧不误伤真绿）"
        )

    def test_source_gate_selection_fail_closed(self):
        """结构性锁定：实例选择必须先取最新、后验 completed（禁止预过滤）。"""
        script = get_script_path().read_text(encoding="utf-8")
        match = re.search(
            r"def gate_latest_completed\(pr; gate\):\n(.*?)# 绿判收敛点",
            script,
            re.DOTALL,
        )
        assert match, "triage 缺 gate_latest_completed 函数"
        body = match.group(1)
        # #6 修复形态：select 不再钉死 status=="completed"（运行中实例参与排序）
        assert '.status == "completed"' not in body, (
            "gate_latest_completed 禁止在实例选择时按 status==completed 预过滤"
            "（#6 fail-open：滤掉 in_progress/queued 新实例 → 旧 success 冒充最新）"
        )
        # round-3 ① 修复形态：排序键必须不含 started_at（时间戳列的
        # null-and-never-populated 语义在 #7 与 ① 之间无解）——按 id 排序
        assert ".started_at" not in body, (
            "gate_latest_completed 禁止以 started_at 为排序键（round-3 ① #168 类"
            "回摆：9999 哨兵把旧 null-cancelled 实例当最新 → 永久拒合；"
            "check-run id 单调分配才是无 null 的时序代理）"
        )
        assert "sort_by(.id" in body, (
            "gate_latest_completed 必须按 check-run id 取最新实例（round-3 ①）"
        )
        # 绿判收敛点必须同时验 status 与 conclusion（最新实例未完成 ≠ 绿）
        verdict = re.search(
            r"def required_gate_success\(pr\):\n(.*?)# complete =", script, re.DOTALL
        )
        assert verdict, "triage 缺 required_gate_success 函数"
        vbody = verdict.group(1)
        assert 'status == "completed"' in vbody, (
            "required_gate_success 必须验证最新实例 status=completed"
            "（#6：仅验 conclusion 会让 in_progress/queued 最新实例被判绿）"
        )


class TestRound3NoRevert:
    """Round-3 ①②「修复不回摆」窄域锁定。

    ①（#168 类回摆）：8afdb92 的 9999 哨兵把 started_at=null 一律视为最新。
    cancelled 实例如果从未开跑（queued 期被 cancel-in-progress 取消），
    started_at=null 且**永不回填**——该实例会永久压制其后一切更新实例：
    即使同 head SHA 上三门随后全 success，green 判定也永远过不了 → 永久拒合。
    修复（id 排序）后该形态必须恢复 mergeable（keep-green 断言，防回摆）。
    反向（#7 不回退）：更新实例（id 更大）cancelled → 仍必须压制旧 success。

    ②（round-2 修复回摆）：has_red_conclusion 的 any-red-wins 与
    gate_latest_completed 的 latest-wins 不一致——门重触发（如 rerun/编排
    取消）留下被取代的红实例时，绿 PR 永久 stalled。修复（红扫描同样
    latest-per-name）后残留红 + 新绿必须 mergeable（keep-green 断言）。
    反向（round-1 P1-1 不回退）：最新实例红 → 仍 stalled 可达。
    """

    def _runs(self, instances: list[dict]) -> dict:
        return {"total_count": len(instances), "check_runs": instances}

    @staticmethod
    def _gate_run(run_id: int, name: str, status: str, conclusion: str | None, started_at):
        return {
            "id": run_id,
            "name": name,
            "status": status,
            "conclusion": conclusion,
            "started_at": started_at,
        }

    def _three_gate_success(self, start_id: int, started_at: str) -> list[dict]:
        return [
            self._gate_run(start_id + i, name, "completed", "success", started_at)
            for i, name in enumerate(THREE_GATES)
        ]

    # ---------- ① 修复不回摆（#168 类） ----------

    def test_old_null_cancelled_superseded_by_new_success_is_mergeable(self):
        """①：旧 null-started_at cancelled + 新 success（id 更大）→ mergeable。

        9999 哨兵（已废）会把这个永不回填 started_at 的旧取消实例当最新，
        三门全绿仍永久拒合（#168 类「merge never happens」）。id 排序后新
        success（id 更大）赢得门判 → 照常判绿。此断言在哨兵回摆时必红。
        """
        check_runs = self._runs(
            self._three_gate_success(1, "2026-10-19T00:00:00Z")
            + [self._gate_run(50, "quality-gate", "completed", "cancelled", None)]
            + [
                self._gate_run(100 + i, name, "completed", "success", "2026-10-19T00:10:00Z")
                for i, name in enumerate(THREE_GATES)
            ]
        )
        output = _run_triage([_pr(500, check_runs=check_runs)])
        assert [p["number"] for p in output["mergeable"]] == [500], (
            "被取代的旧 null-cancelled 实例不得永久拒合（round-3 ① 回摆守卫："
            "9999 哨兵回摆时本断言必红）"
        )

    def test_old_started_cancelled_superseded_by_new_success_is_mergeable(self):
        """① 变体：旧「开跑后被取消」实例同样被新 success 取代 → mergeable。"""
        check_runs = self._runs(
            self._three_gate_success(1, "2026-10-19T00:00:00Z")
            + [self._gate_run(50, "quality-gate", "completed", "cancelled", "2026-10-19T00:02:00Z")]
            + [
                self._gate_run(100 + i, name, "completed", "success", "2026-10-19T00:10:00Z")
                for i, name in enumerate(THREE_GATES)
            ]
        )
        output = _run_triage([_pr(501, check_runs=check_runs)])
        assert [p["number"] for p in output["mergeable"]] == [501]

    def test_id_ordering_ignores_timestamp_values(self):
        """① 排序键语义：id 是唯一时序真相——旧实例带更晚时间戳也不赢。

        run id 分配单调递增；started_at 仅是记录字段。若实现回摆成时间戳
        排序（9999 哨兵把 null 当最新 / 裸 started_at 升序），本用例会取
        started_at 最晚的旧 cancelled 实例当门判 → 误判非绿（红）；id 排序
        取 id 最大的新 success → 绿。
        """
        check_runs = self._runs(
            [
                # id=1 旧 cancelled，started_at 反常地晚（回填/时钟漂移）
                self._gate_run(1, "quality-gate", "completed", "cancelled", "2026-10-19T23:59:00Z"),
                # id=100 新 success，started_at 更早
                self._gate_run(100, "quality-gate", "completed", "success", "2026-10-19T00:00:00Z"),
            ]
            + [
                self._gate_run(2 + i, name, "completed", "success", "2026-10-19T00:00:00Z")
                for i, name in enumerate(("substrate-gate-suite", "droid-review"))
            ]
        )
        output = _run_triage([_pr(502, check_runs=check_runs)])
        assert [p["number"] for p in output["mergeable"]] == [502]

    # ---------- ① 反向：#7 不回退 ----------

    def test_newer_cancelled_still_blocks(self):
        """#7 不回退：最新（id 更大）cancelled 实例仍压制旧 success → 非绿。"""
        check_runs = self._runs(
            self._three_gate_success(1, "2026-10-19T00:00:00Z")
            + [self._gate_run(999, "quality-gate", "completed", "cancelled", None)]
        )
        output = _run_triage([_pr(503, check_runs=check_runs)])
        assert output["mergeable"] == [], "最新 cancelled 必须非绿（#7 语义不回退）"
        assert [p["number"] for p in output["stalled"]] == [503]

    def test_newer_in_progress_still_blocks(self):
        """#6 不回退：最新（id 更大）in_progress 实例仍压制旧 success → pending。"""
        check_runs = self._runs(
            self._three_gate_success(1, "2026-10-19T00:00:00Z")
            + [self._gate_run(999, "droid-review", "in_progress", None, "2026-10-19T00:05:00Z")]
        )
        rollup = [
            {"name": "substrate-gate-suite", "conclusion": "SUCCESS"},
            {"name": "quality-gate", "conclusion": "SUCCESS"},
            {"name": "droid-review", "conclusion": None},
        ]
        output = _run_triage([_pr(504, rollup=rollup, check_runs=check_runs)])
        assert output["mergeable"] == []
        assert [p["number"] for p in output["pending"]] == [504]

    # ---------- ② 修复不回摆：红扫描 latest-per-name ----------

    def test_superseded_red_residue_with_new_green_is_mergeable(self):
        """②：残留红（被取代）+ 新绿 → mergeable（回摆守卫，哨兵形态必红）。

        any-red-wins（已废）会把被取代的 FAILURE/CANCELLED 残留当成红腿，
        绿 PR 永久 stalled（wait + escalate，永不合并）。红扫描改 latest-per-name
        后：残留红不再阻塞，最新旁路实例绿 + 三门绿 → 照常判绿。
        """
        pr = _pr(505)
        rollup = [
            {"name": "substrate-gate-suite", "conclusion": "SUCCESS"},
            {"name": "quality-gate", "conclusion": "SUCCESS"},
            {"name": "droid-review", "conclusion": "FAILURE"},  # 被取代的红残留
            {"name": "droid-review", "conclusion": "SUCCESS"},  # 最新（列表序即 id 序）
            {"name": "lint", "conclusion": "SUCCESS"},
        ]
        output = _run_triage([{**pr, "statusCheckRollup": rollup}])
        assert [p["number"] for p in output["mergeable"]] == [505], (
            "被取代的红残留不得把绿 PR 永久 stalled（round-3 ② 回摆守卫）"
        )

    def test_superseded_cancelled_residue_with_new_green_is_mergeable(self):
        """② 变体：残留 CANCELLED + 新 SUCCESS（同名旁路）→ mergeable。"""
        pr = _pr(506)
        rollup = [
            {"name": "droid-review", "conclusion": "SUCCESS"},
            {"name": "lint", "conclusion": "CANCELLED"},  # 编排取消残留
            {"name": "lint", "conclusion": "SUCCESS"},  # 最新
        ]
        output = _run_triage([{**pr, "statusCheckRollup": rollup}])
        assert [p["number"] for p in output["mergeable"]] == [506]

    # ---------- 两门禁语义一致性（round-8 sh11 P1） ----------

    def test_superseded_red_residue_two_gates_same_verdict(self):
        """round-8 sh11 P1：被取代红残留场景 triage 与 action 两门禁同判绿。

        同一份 check-run 数据喂给两个绿判载体：triage（红腿 latest-per-name）
        与 merge action 旁路零红扫描（修复前 any-instance → FAILED 永久
        stall）。一致性断言：triage mergeable ⇔ action FAILED=0；红在最新
        实例上时两侧同红。
        """
        superseded_residue = [
            {
                "id": 1,
                "name": "substrate-gate-suite",
                "status": "completed",
                "conclusion": "success",
            },
            {"id": 2, "name": "quality-gate", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "droid-review", "status": "completed", "conclusion": "success"},
            {"id": 10, "name": "lint", "status": "completed", "conclusion": "failure"},
            {"id": 11, "name": "lint", "status": "completed", "conclusion": "success"},
            {"id": 12, "name": "build", "status": "completed", "conclusion": "success"},
        ]
        pr = _pr(509)
        pr["statusCheckRollup"] = [
            {"name": c["name"], "conclusion": c["conclusion"].upper()} for c in superseded_residue
        ]
        pr["checkRuns"] = {"total_count": 6, "check_runs": superseded_residue}
        output = _run_triage([pr])
        triage_green = [p["number"] for p in output["mergeable"]] == [509]

        from tests.test_auto_merge_read_write_token_contract import (
            _failed_scan_program,
            _run_failed_scan,
        )

        action_failed = _run_failed_scan(_failed_scan_program(), superseded_residue)
        action_green = action_failed == "0"
        assert triage_green and action_green, (
            "两门禁必须同判绿（被取代红残留不阻塞）："
            f"triage mergeable={triage_green}, action FAILED={action_failed}"
        )

    def test_latest_red_two_gates_same_verdict(self):
        """round-8 sh11 P1 反向：最新实例红场景两门禁同判红。"""
        latest_red = [
            {
                "id": 1,
                "name": "substrate-gate-suite",
                "status": "completed",
                "conclusion": "success",
            },
            {"id": 2, "name": "quality-gate", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "droid-review", "status": "completed", "conclusion": "success"},
            {"id": 20, "name": "lint", "status": "completed", "conclusion": "success"},
            {"id": 21, "name": "lint", "status": "completed", "conclusion": "failure"},
            {"id": 22, "name": "build", "status": "completed", "conclusion": "success"},
        ]
        pr = _pr(510)
        pr["statusCheckRollup"] = [
            {"name": c["name"], "conclusion": c["conclusion"].upper()} for c in latest_red
        ]
        pr["checkRuns"] = {"total_count": 6, "check_runs": latest_red}
        output = _run_triage([pr])
        triage_red = [p["number"] for p in output["stalled"]] == [510]

        from tests.test_auto_merge_read_write_token_contract import (
            _failed_scan_program,
            _run_failed_scan,
        )

        action_failed = _run_failed_scan(_failed_scan_program(), latest_red)
        action_red = action_failed != "0"
        assert triage_red and action_red, (
            "两门禁必须同判红（最新实例红不消失）："
            f"triage stalled={triage_red}, action FAILED={action_failed}"
        )

    # ---------- ② 反向：round-1 P1-1 不回退 ----------

    def test_latest_red_still_stalled(self):
        """round-1 P1-1 不回退：最新实例红 → 仍 stalled（显面非静默）。"""
        pr = _pr(507)
        rollup = [
            {"name": "lint", "conclusion": "SUCCESS"},
            {"name": "lint", "conclusion": "FAILURE"},  # 最新
        ]
        output = _run_triage([{**pr, "statusCheckRollup": rollup}])
        assert output["mergeable"] == []
        assert [p["number"] for p in output["stalled"]] == [507]

    def test_latest_null_conclusion_with_residue_still_pending(self):
        """未报齐不回退：最新实例 conclusion=null → pending（非 stalled/绿）。"""
        pr = _pr(508)
        rollup = [
            {"name": "lint", "conclusion": "FAILURE"},  # 残留
            {"name": "lint", "conclusion": None},  # 最新：仍在跑
        ]
        output = _run_triage([{**pr, "statusCheckRollup": rollup}])
        assert [p["number"] for p in output["pending"]] == [508]
        assert output["mergeable"] == []
        assert output["stalled"] == []

    # ----- 判定矩阵覆盖总结 -----

    def test_judgment_matrix_documentation(self):
        """判定矩阵文档：mergeable 只由 REST 三门全 success 决定。

        - 三门 REST 全 success → mergeable（rollup 形状无关）
        - 三门含 skipped/neutral（REST）→ 非 mergeable
        - 失败结论 → stalled
        - checks 未报齐 → pending
        """
        # 三门全 success（REST）→ mergeable
        output = _run_triage([_pr(200)])
        assert output["mergeable"], "三门 REST 全 success 应判为 green"

        # 三门含 SKIPPED/NEUTRAL（REST）→ NOT mergeable
        for conclusion in ("SKIPPED", "NEUTRAL"):
            rollup = [{"conclusion": conclusion}]
            output = _run_triage(
                [_pr(201, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))]
            )
            assert not output["mergeable"], (
                f"三门 REST 含 {conclusion} 时 NOT 判 mergeable（skipped/neutral/missing/"
                "pending 一律非 mergeable）"
            )

        # 失败结论（REST）→ stalled
        for conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"):
            rollup = [{"conclusion": conclusion}]
            output = _run_triage(
                [_pr(202, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))]
            )
            assert output["stalled"], f"{conclusion} 应判为 stalled（失败）"

    # ----- checks_reported 语义：非 success conclusion ≠ reported -----

    @pytest.mark.parametrize("conclusion", ["FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"])
    def test_rollup_red_conclusion_not_mergeable_and_not_pending(self, conclusion):
        """round-1 P1-1：红腿 conclusion → stalled，绝不 mergeable。

        旧 checks_reported 只验「conclusion 是非空字符串」——FAILURE/CANCELLED/
        TIMED_OUT 也算「已报齐」，导致 REST 三门全绿（checkRuns）+ 旁路腿红
        （rollup）时 PR 仍可 classified mergeable（stalled 分支不可达，零红
        铁律被旁路穿透）。终态语义：has_red_conclusion 先于 mergeable 分支，
        任何红 conclusion 一律 stalled（wait + escalate）。
        """
        rollup = [{"conclusion": "SUCCESS"}, {"conclusion": conclusion}]
        # checkRuns 保持默认（REST 三门全绿）——finding ① 的暴露面正是
        # 「三门绿 + 旁路红」：绿判成立、报齐判定被红 conclusion 骗过。
        output = _run_triage([_pr(320, rollup=rollup)])
        assert output["mergeable"] == [], f"rollup 含 {conclusion} 时不得判 mergeable"
        assert [p["number"] for p in output["stalled"]] == [320], (
            f"rollup 含 {conclusion}（红腿）必须 stalled（wait + escalate），"
            "不得落入 pending/mergeable"
        )

    @pytest.mark.parametrize("conclusion", ["SUCCESS", "SKIPPED", "NEUTRAL"])
    def test_rollup_green_conclusion_counts_as_reported(self, conclusion):
        """灰绿结论（success/skipped/neutral）= 已报齐（配合 REST 三门绿判）。

        三门全绿 + 旁路灰绿 → mergeable（旁路灰绿不参与绿判，v3.2.2 既有
        契约 test_success_plus_skipped_is_green 不回退）；红 conclusion 不算
        已报齐（→ stalled，上一用例锁定）。
        """
        rollup = [{"conclusion": conclusion}, {"conclusion": conclusion}]
        output = _run_triage([_pr(321, rollup=rollup)])
        assert output["mergeable"][0]["number"] == 321, (
            f"旁路 {conclusion}（三门 REST 全绿背景）不得阻塞 mergeable"
        )

    def test_rollup_null_conclusion_still_pending(self):
        """null conclusion（in_progress/queued）= 未报齐 → pending（fail-closed 不变）。"""
        rollup = [
            {"conclusion": "SUCCESS"},
            {"status": "in_progress", "conclusion": None},
        ]
        output = _run_triage([_pr(322, rollup=rollup, check_runs=_check_runs_from_rollup(rollup))])
        assert [p["number"] for p in output["pending"]] == [322]
        assert output["mergeable"] == []
        assert output["stalled"] == [], "null conclusion 是未报齐，不是 stalled"

    def test_workflow_stalled_step_documentation_matches_semantics(self):
        """workflow stalled 步骤文案与 checks_reported 新语义一致。

        stalled 步骤的注释/文案曾表述「存在非 SUCCESS 结论」；语义修订后
        stalled = 报齐且含非灰绿结论（红腿），文案必须同步，防运维误读。
        """
        data = yaml.safe_load(get_workflow_path().read_text())
        steps = data["jobs"]["auto-merge"]["steps"]
        stalled_step = next(
            s for s in steps if s.get("name") and "stalled" in s.get("name", "").lower()
        )
        run_block = stalled_step["run"]
        assert "grey-green" in run_block, (
            "stalled 步骤必须按新语义（红腿 vs 灰绿旁路）表述，禁残留「非 SUCCESS 一刀切」"
        )


def _slim_check_runs(check_runs: dict) -> dict:
    """复刻 pipeline 归一瘦身投影（sh1 P1 消费面五字段），供载荷等价性对照。"""
    return {
        "total_count": len(check_runs["check_runs"]),
        "check_runs": [
            {k: r[k] for k in ("id", "name", "status", "conclusion", "started_at")}
            for r in check_runs["check_runs"]
        ],
    }


class TestCheckRunsFatPayloadVerdictEquivalence:
    """sh1 P1（PR-C #411 round-1）行为级回归：巨体 check-run 载荷判定不变。

    E2BIG 本体在 pipeline 载体（全量对象单 --argjson 内嵌，busy head SHA 上
    超 Linux MAX_ARG_STRLEN 128KiB → jq 红 → set -e 红腿 triage）；argv 上限
    是 Linux 特有常量，macOS 不可直接复现——本类锁修复的可观测不变量：
    「瘦身投影不改变三门判定」。每个场景 fat（output.summary/text 各 64KB、
    总载 ~200KB+/run）与 slim（消费面五字段）两次分类输出必须逐字节相等；
    triage 若开始消费被剥字段（.output/.details_url），两态发散即红，等价性
    锁同时充当瘦身正确性的行为证据。载荷边界（禁全量对象进 argv）由
    test_auto_merge_pipeline_hardening.TestCheckRunsArgLimitHardening 锁定。
    """

    @staticmethod
    def _fat_run(idx: int, name: str, conclusion: str = "success") -> dict:
        run = {
            "id": idx,
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
            "started_at": "2026-10-24T00:00:00Z",
            "output": {
                "title": "review report",
                "summary": "S" * (64 * 1024),
                "text": "F" * (200 * 1024),
            },
            "details_url": "https://example.invalid/runs/1",
        }
        if conclusion is None:
            run["status"] = "in_progress"
        return run

    def _scenarios(self) -> dict[int, dict]:
        gates = ("substrate-gate-suite", "quality-gate", "droid-review")
        all_green_fat = {
            "total_count": 4,
            "check_runs": [self._fat_run(i + 1, g) for i, g in enumerate(gates)]
            + [self._fat_run(4, "bypass-heavy")],
        }
        newer_gate_red_fat = {
            "total_count": 5,
            "check_runs": [self._fat_run(i + 1, g) for i, g in enumerate(gates)]
            + [self._fat_run(4, "droid-review", "failure")]
            + [self._fat_run(5, "bypass-heavy")],
        }
        return {
            330: all_green_fat,
            331: newer_gate_red_fat,
            332: {"total_count": 0, "check_runs": []},
        }

    def test_three_gate_verdict_invariant_under_fat_payload(self):
        """三门判定在 fat/slim 两态下逐类相等；全绿场景必须 mergeable、
        新红实例场景必须非 mergeable（防等价性断言双坏漂移空转）。"""

        def strip_check_runs(obj):
            """分类输出回显 PR 本体（含注入的 checkRuns 原文）——剥离后比对，
            等价性断言只针对分类语义而非载荷回显。"""
            if isinstance(obj, dict):
                return {k: strip_check_runs(v) for k, v in obj.items() if k != "checkRuns"}
            if isinstance(obj, list):
                return [strip_check_runs(x) for x in obj]
            return obj

        for number, fat in self._scenarios().items():
            out_fat = _run_triage([_pr(number, check_runs=fat)])
            out_slim = _run_triage([_pr(number, check_runs=_slim_check_runs(fat))])
            assert strip_check_runs(out_fat) == strip_check_runs(out_slim), (
                f"PR {number}: 瘦身投影改变分类——triage 消费了被剥字段"
                f"（.output/.details_url），E2BIG 瘦身非语义保持"
            )
        all_green = _run_triage([_pr(330, check_runs=self._scenarios()[330])])
        assert [p["number"] for p in all_green["mergeable"]] == [330]
        red_gate = _run_triage([_pr(331, check_runs=self._scenarios()[331])])
        assert red_gate["mergeable"] == []
        assert [p["number"] for p in red_gate["stalled"]] == [331]
        missing = _run_triage([_pr(332, check_runs=self._scenarios()[332])])
        assert missing["mergeable"] == [], "checkRuns 空 → fail-closed 非绿（绝不 mergeable）"
