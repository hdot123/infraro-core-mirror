"""Tests for the zero-red check-runs mechanism.

User mandate (2026-08-28): "写死不允许红色合并，一个都不允许"
These tests verify that the ci-ok gate correctly fails when any check-run
(including advisory jobs) has a non-success conclusion. INFRA-595: advisory
jobs carry no continue-on-error, so a failure is a red check-run that both
the needs.result gate and the GitHub API scan block.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml


class TestZeroRedScript:
    """Test the check_zero_red.sh script logic."""

    @pytest.fixture
    def script_path(self):
        """Path to the zero-red check script."""
        return Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"

    def test_script_exists_and_executable(self, script_path):
        """Verify the script exists and is executable."""
        assert script_path.exists(), f"Script not found: {script_path}"
        assert script_path.stat().st_mode & 0o111, "Script is not executable"

    def test_script_usage_on_missing_args(self, script_path):
        """Script should print usage when called without arguments."""
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Usage:" in result.stderr or "Usage:" in result.stdout

    def test_script_logic_mock_success(self):
        """Test zero-red logic with all-success check-runs (mock)."""
        # Simulate the check-runs output format
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
type-bundle\tsuccess
advisory-bundle\tsuccess
advisory-bundle\tneutral
advisory-bundle\tskipped
ci-ok\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # All success/skipped/neutral should pass
        assert len(red_checks) == 0, "Should have no red checks when all are green"

    def test_script_logic_mock_failure_advisory_red(self):
        """Test zero-red logic with a red advisory check (mock)."""
        # Simulate a scenario where the advisory bundle fails
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
type-bundle\tsuccess
advisory-bundle\tfailure
ci-ok\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should detect the red advisory check
        assert len(red_checks) == 1
        assert "advisory-bundle" in red_checks[0]
        assert "failure" in red_checks[0]

    def test_script_logic_mock_multiple_red(self):
        """Test zero-red logic with multiple red checks (mock)."""
        # Simulate multiple failures
        mock_output = """pytest\tsuccess
lint-bundle\tfailure
advisory-bundle\tfailure
type-bundle\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should detect both red checks
        assert len(red_checks) == 2
        assert any("lint-bundle" in check for check in red_checks)
        assert any("advisory-bundle" in check for check in red_checks)

    def test_script_logic_mock_ci_ok_exclusion(self):
        """Test that ci-ok check is excluded to prevent circular self-reference (regression test)."""
        # Simulate a scenario where ci-ok has a failure but should be excluded
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
ci-ok\tfailure"""

        # Parse the output the same way the script does (excluding ci-ok)
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                # This simulates the script's logic: exclude ci-ok and check for non-success conclusions
                if name != "ci-ok" and conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should have no red checks since ci-ok failure is excluded
        assert len(red_checks) == 0, "ci-ok failure should be excluded and not count as red"

    def test_script_excludes_quality_gate(self):
        """quality-gate 必须排除（环形引用回归测试，PR #248 死锁）。

        quality-gate 是独立 workflow，唯一输入是 ci-ok 自身结论（聚合面
        ci-ok + substrate-gate-suite + qa-ok）。慢尾路径下（PR #248，
        2026-10-04：droid-review 单跑 81 分钟）quality-gate 会在 ci-ok 完成
        前超时失败，本扫描把该旧 failure 当作该 name 的「最新 completed」→
        ci-ok 在同一 SHA 上判红——任何 rerun 都无法恢复：quality-gate 只在
        ci-ok 结束后才重报，而红扫描已经把它阻断。quality-gate 是 ruleset
        required check，真实 failure 仍由 branch protection 阻断合并。
        """
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()

        # The jq filter must exclude both circular names
        assert '.name != "ci-ok" and .name != "quality-gate"' in content, (
            "check_zero_red.sh 的 jq 过滤必须同时排除 ci-ok 与 quality-gate"
            "（PR #248 环形死锁：quality-gate 聚合 ci-ok，反向扫描形成环）"
        )
        assert '.name != "droid-review"' in content, (
            "check_zero_red.sh 必须排除 droid-review（ci-ok 不再轮询，解耦廉价门与 LLM 审查）"
        )

    def test_zero_red_excludes_exact_notify_job_name(self):
        """notify job 显示名必须与 ci.yml 同步出现在排除脚本中。"""
        ci_yml = Path(__file__).parent.parent / ".github/workflows/ci.yml"
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        jobs = yaml.safe_load(ci_yml.read_text(encoding="utf-8"))["jobs"]
        notify_name = jobs["notify-ci-complete"]["name"]
        content = script_path.read_text(encoding="utf-8")
        assert f'.name != "{notify_name}"' in content, (
            "check_zero_red.sh 排除名必须与 ci.yml notify-ci-complete.name 字节同步"
        )

    def test_zero_red_exclusion_set_is_closed(self):
        """零红 jq 排除集封闭：ci-ok、quality-gate、droid-review、notify。"""
        import re

        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text(encoding="utf-8")
        ci_yml = Path(__file__).parent.parent / ".github/workflows/ci.yml"
        notify_name = yaml.safe_load(ci_yml.read_text(encoding="utf-8"))["jobs"][
            "notify-ci-complete"
        ]["name"]
        found = set(re.findall(r'\.name != "([^"]+)"', content))
        expected = {"ci-ok", "quality-gate", "droid-review", notify_name}
        assert found == expected, f"排除集必须恰好为 {expected!r}，实际 {found!r}"

    def test_script_excludes_notify_ci_complete(self):
        """round-4 P1（shard-2）：Notify CI 完成腿必须排除（侧信道红墓碑防毒化）。

        notify-ci-complete 在同一 workflow 内于 ci-ok 之后运行（needs:
        [ci-ok, gate-tests]），配置缺失或 WEBHOOK_NOTIFY_STRICT 下投递失败
        会落红 check-run。它不在任何其他门的面里；若不排除，同一 SHA 上
        任何后续 ci-ok run（rerun / labeled / ready_for_review 重触发）的
        零红扫描都会看到该红墓碑 → 永久红，只能靠新 commit 恢复——与
        quality-gate 排除同构的环形毒化。通知健康度在 notify check 自身
        可观测，不得参与合并门禁。
        """
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()

        # jq 过滤必须排除 notify 腿（名字字节级 = ci.yml notify job 的显示名）
        assert '.name != "Notify CI complete to webhook gateway"' in content, (
            "check_zero_red.sh 的 jq 过滤必须排除 Notify CI 完成腿（round-4 P1："
            "侧信道红墓碑在同 SHA 后续 ci-ok 上永久毒化零红扫描）"
        )

    def test_script_logic_mock_notify_exclusion(self):
        """行为级模拟：notify 腿红墓碑不毒化同 SHA 后续 ci-ok 零红扫描。"""
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
droid-review\tsuccess
Notify CI complete to webhook gateway\tfailure"""

        EXCLUDED = {
            "ci-ok",
            "quality-gate",
            "droid-review",
            "Notify CI complete to webhook gateway",
        }
        red_checks = []
        for line in mock_output.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if name not in EXCLUDED and conclusion not in [
                    "success",
                    "skipped",
                    "neutral",
                    "cancelled",
                ]:
                    red_checks.append(line)

        assert len(red_checks) == 0, "notify 腿 failure 必须被排除（侧信道），不得毒化零红扫描"

        # 反向保证：非排除名的 failure 仍判红（零红铁律不削弱）
        mock_output_red = mock_output.replace("lint-bundle\tsuccess", "lint-bundle\tfailure")
        red_checks_2 = [
            line
            for line in mock_output_red.strip().split("\n")
            if len(line.split("\t")) == 2
            and line.split("\t")[0] not in EXCLUDED
            and line.split("\t")[1] not in ("success", "skipped", "neutral", "cancelled")
        ]
        assert len(red_checks_2) == 1 and "lint-bundle" in red_checks_2[0], (
            "排除 notify 腿不得放松对其他 check 的零红判定"
        )

    def test_script_logic_mock_quality_gate_exclusion(self):
        """行为级模拟：quality-gate 旧 failure 不判红（PR #248 场景）。"""
        # PR #248 场景：quality-gate 已超时 failure，其余 check 全绿
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
droid-review\tsuccess
quality-gate\tfailure"""

        EXCLUDED = {"ci-ok", "quality-gate"}
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if name not in EXCLUDED and conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # quality-gate failure 被排除，其余全绿 → 零红通过
        assert len(red_checks) == 0, (
            "quality-gate 旧 failure 必须被排除（环形引用），不得毒化 ci-ok"
        )

        # 反向保证：非排除名的 failure 仍然判红（零红铁律不被削弱）
        mock_output_red = mock_output.replace("pytest\tsuccess", "pytest\tfailure")
        red_checks_2 = []
        for line in mock_output_red.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if name not in EXCLUDED and conclusion not in ["success", "skipped", "neutral"]:
                    red_checks_2.append(line)
        assert len(red_checks_2) == 1 and "pytest" in red_checks_2[0], (
            "排除 quality-gate 不得放松对其他 check 的零红判定"
        )

    def test_script_logic_mock_name_deduplication(self):
        """Test that check-runs are deduplicated by name, taking the latest (regression test)."""
        # Simulate scenario with multiple runs of the same name, where the latest one is success
        mock_output = """pytest\tfailure
pytest\tsuccess
lint-bundle\tfailure
lint-bundle\tsuccess
type-check\tsuccess"""

        # Parse the output and simulate grouping by name, taking latest
        lines = mock_output.strip().split("\n")

        # Group by name and take the last occurrence for each name (simulating sort_by(.started_at) | last)
        check_dict = {}
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                # Overwrite with latest (last in list) - simulating the group_by(.name) | map(sort_by(.started_at) | last) pattern
                check_dict[name] = (name, conclusion)

        # Now check for non-success conclusions among unique names
        red_checks = []
        for name, (name_key, conclusion) in check_dict.items():
            if conclusion not in ["success", "skipped", "neutral"]:
                red_checks.append(f"{name_key}\t{conclusion}")

        # Should have no red checks since the latest of each name is success
        assert len(red_checks) == 0, (
            f"Latest conclusion of each name should be checked, got: {red_checks}"
        )

        # Verify that the dict contains the latest conclusion for each name
        assert check_dict["pytest"][1] == "success", (
            "Should take latest pytest conclusion (success)"
        )
        assert check_dict["lint-bundle"][1] == "success", (
            "Should take latest lint-bundle conclusion (success)"
        )
        assert check_dict["type-check"][1] == "success", (
            "Should take latest type-check conclusion (success)"
        )


class TestCIWorkflowZeroRed:
    """Test that the CI workflow includes zero-red enforcement."""

    def test_ci_ok_includes_advisory_in_needs(self):
        """Verify ci-ok job lists the advisory bundle in needs.

        2026-08-29 bundle 化：advisory 三 job（advisory-dependency-security-scan
        / advisory-deptry / advisory-telemetry-audit）合并为 advisory-bundle，
        零红语义由该 bundle 承接。
        """
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        assert "advisory-bundle" in content, "Should have advisory-bundle job (bundle 化后零红载体)"

    def test_ci_ok_checks_advisory_jobs(self):
        """Verify ci-ok job actually checks advisory bundle results (not just prints)."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Find the ci-ok job section
        ci_ok_start = content.find("  ci-ok:")
        assert ci_ok_start != -1, "ci-ok job not found"

        # Extract ci-ok section (up to next top-level job or end)
        ci_ok_section = content[ci_ok_start:]

        # Verify the advisory bundle is checked with FAILED=1 logic
        # INFRA-595: advisory must NOT set continue-on-error (which makes
        # .result always "success"); with it removed, .result is the real
        # conclusion and this gate is effective.
        assert "advisory-bundle.result" in ci_ok_section, (
            "Should check advisory-bundle result (bundle 化后零红判定载体)"
        )

        # Verify they set FAILED=1 on non-success
        assert "FAILED=1" in ci_ok_section, "Should set FAILED=1 on failure"

    def test_ci_ok_includes_zero_red_api_scan(self):
        """Verify ci-ok includes the zero-red aggregation step via GitHub API."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Check for the zero-red step
        assert "Zero-red aggregation" in content or "zero-red" in content.lower(), (
            "Should have a zero-red aggregation step"
        )
        # The zero-red logic is delegated to the standalone script
        assert "check_zero_red.sh" in content, "Should invoke the check_zero_red.sh script"


class TestZeroRedPolicy:
    """Test the zero-red policy documentation and intent."""

    def test_user_mandate_in_ci(self):
        """Verify the user mandate comment is present in CI."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Check for the user mandate reference
        assert "写死不允许红色合并" in content or "zero-red" in content.lower(), (
            "Should reference the user mandate or zero-red policy"
        )

    def test_advisory_jobs_no_continue_on_error(self):
        """INFRA-595: advisory must NOT have continue-on-error (zero-red).

        continue-on-error masks failures: needs.<job>.result reports the
        post-masking value (always "success"), making the ci-ok gate a no-op
        (proven in run 33129232081). Zero-red requires red check-runs.
        2026-08-29 bundle 化：advisory-bundle 为三 advisory 的零红载体。

        2026-09-18 解冻判据③：gate-tests 已转正 required check，五个门步不再保留
        continue-on-error（存量红暴露为 failure，阻断合并）。job 级 continue-on-error
        始终全面禁止（infra 失败保持红可见）。
        """
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Advisory bundle must exist
        assert "advisory-bundle:" in content, "Should have advisory-bundle job"

        jobs = yaml.safe_load(content)["jobs"]
        gate_job = "gate-tests"
        gate_steps = 0
        for name, job in jobs.items():
            assert not job.get("continue-on-error"), (
                f"job {name} 不得设置 job 级 continue-on-error（零红铁律；"
                "job 级 c-o-e 的 check-run 仍记 failure 且掩蔽 infra 红）"
            )
            for step in job.get("steps") or []:
                if step.get("continue-on-error"):
                    assert name == gate_job, (
                        f"步级 continue-on-error 仅允许出现在 {gate_job} 的门步：{name}"
                    )
                    gate_steps += 1

        # 先红着上线裁定已转正 required：五个门步全部无步级 continue-on-error（转正 required check）
        assert gate_steps == 0, (
            f"gate-tests 转正后应有 0 个步级 continue-on-error（转正 required check）：存量红需暴露为 failure，found {gate_steps}"
        )


class TestCancelledTolerance:
    """INFRA-1321: cancelled check-runs must NOT count as red.

    A cancelled conclusion is never produced by the code under test — it is
    always an orchestration side-effect (watchdog cancel-on-ci-fail killing a
    healthy in-flight review on a slow tail, superseded rerun attempts,
    draft-ready workflow races). Treating cancelled as red made every ci-ok
    rerun fail on the tombstone of the previously cancelled run:
    ci-ok red → watchdog cancels review → cancelled check keeps ci-ok red →
    circular quality-gate/ci-ok deadlock where no rerun could ever recover
    to green.

    Gating of the review itself is NOT weakened: check_droid_review.sh's
    early-exit probe dies on cancelled upstream runs and the ruleset still
    requires a real droid-review conclusion — cancelled only stops poisoning
    the zero-red scan. Semantic map (locked here):
      success/skipped/neutral → green (original zero-red semantics)
      cancelled               → grey (superseded run; per-check gates decide)
      failure/timed_out/…     → red (blocks merge)
    """

    GREEN_CONCLUSIONS = ["success", "skipped", "neutral", "cancelled"]
    RED_CONCLUSIONS = ["failure", "timed_out", "action_required", "startup_failure"]

    @pytest.mark.parametrize("conclusion", GREEN_CONCLUSIONS)
    def test_green_conclusions_pass(self, conclusion):
        """success/skipped/neutral/cancelled must all pass zero-red."""
        mock_output = f"pytest\t{conclusion}"
        lines = mock_output.strip().split("\n")
        red_checks = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2 and parts[1] not in self.GREEN_CONCLUSIONS:
                red_checks.append(line)
        assert red_checks == [], f"{conclusion} must be tolerated (grey), not red"

    @pytest.mark.parametrize("conclusion", RED_CONCLUSIONS)
    def test_red_conclusions_still_block(self, conclusion):
        """Real failure conclusions must still block (zero-red mandate intact)."""
        mock_output = f"pytest\tsuccess\nadvisory-bundle\t{conclusion}"
        lines = mock_output.strip().split("\n")
        red_checks = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2 and parts[1] not in self.GREEN_CONCLUSIONS:
                red_checks.append(line)
        assert red_checks == [f"advisory-bundle\t{conclusion}"]

    def test_script_tolerates_cancelled_conclusion(self):
        """The shipped grep pattern must include cancelled (structural lock).

        Mirrors the mock-logic tests above at the script level: the grep -vE
        allowlist in check_zero_red.sh must accept cancelled or the INFRA-1321
        deadlock regresses.
        """
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        assert "success|skipped|neutral|cancelled" in content, (
            "check_zero_red.sh grep allowlist must tolerate cancelled "
            "(INFRA-1321: cancelled = grey orchestration tombstone, not red)"
        )

    def test_deadlock_scenario_mock(self):
        """Full INFRA-1321 scenario: cancelled droid-review tombstone on the
        same SHA must not poison zero-red for a rerun whose own checks pass."""
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
droid-review\tcancelled
quality-gate\tfailure"""
        EXCLUDED = {"ci-ok", "quality-gate"}
        lines = mock_output.strip().split("\n")
        red_checks = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if name not in EXCLUDED and conclusion not in self.GREEN_CONCLUSIONS:
                    red_checks.append(line)
        assert red_checks == [], (
            "a cancelled droid-review tombstone must not keep ci-ok red "
            "(circular deadlock: no rerun could ever recover to green)"
        )


class TestLatestInstanceSelection:
    """round-6 handoff: latest-selection 与 merge-gate sentinel 对齐。

    缺陷（sentinel 修复前同款）：`select(.status == "completed")` 预滤 +
    `sort_by(.started_at)` —— ① status 预滤让刚被 rerun 触发的 in-flight
    实例被跳过，扫描读旧兄弟实例的旧结论（branch push 后 rerun 场景会把
    「正在重判」的 check 按旧红打回）；② started_at 同秒 rerun 序不保证，
    id 才单调。对齐后：group_by(name) 内取 id 最大实例（无 status 预滤）；
    in-flight 不判红（own 结论落地后由自身门 + 重跑扫描裁决）。
    """

    def test_no_status_prefilter_in_selection(self):
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        assert 'select(.status == "completed")' not in content, (
            "latest-instance 选择禁止 status 预滤（sentinel 同款缺陷：in-flight "
            "rerun 被跳过 → 读旧兄弟实例的旧结论）"
        )

    def test_selection_orders_by_id_not_started_at(self):
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        assert "sort_by(.id)" in content, "必须按 id 取最新实例（started_at 同秒不保证序）"
        assert "sort_by(.started_at)" not in content

    def test_inflight_not_red(self):
        """最新实例 in-flight → 不判红（重判中；落地后由自身门裁决）。"""
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        # red verdict 行必须带 completed 前置条件（awk 判定面）
        assert '$2 == "completed"' in content, "红判定必须只针对 completed 实例"

    def test_selection_lock_mock(self):
        """行为级：最新 completed 红 + 更新 in-flight 兄弟 → 不判红（重判中）。"""
        # 模拟脚本选择逻辑：group_by(name) → id 最大实例（无预滤）→ in-flight 不判红
        checks = [
            {"name": "droid-review", "id": 1, "status": "completed", "conclusion": "failure"},
            {"name": "droid-review", "id": 2, "status": "in_progress", "conclusion": None},
            {"name": "pytest", "id": 3, "status": "completed", "conclusion": "success"},
        ]
        by_name: dict[str, dict] = {}
        for c in checks:
            by_name[c["name"]] = max(by_name.get(c["name"], c), c, key=lambda x: x["id"])
        red = [
            name
            for name, c in by_name.items()
            if c["status"] == "completed"
            and c["conclusion"] not in ("success", "skipped", "neutral", "cancelled")
        ]
        assert red == [], "in-flight rerun 期间旧红不得判红（否则 update-branch 后死锁）"

    def test_completed_red_still_blocks(self):
        """最新实例 completed 且 failure → 仍判红（零红铁律不弱化）。"""
        checks = [
            {"name": "droid-review", "id": 1, "status": "completed", "conclusion": "success"},
            {"name": "droid-review", "id": 2, "status": "completed", "conclusion": "failure"},
        ]
        by_name: dict[str, dict] = {}
        for c in checks:
            by_name[c["name"]] = max(by_name.get(c["name"], c), c, key=lambda x: x["id"])
        red = [
            name
            for name, c in by_name.items()
            if c["status"] == "completed"
            and c["conclusion"] not in ("success", "skipped", "neutral", "cancelled")
        ]
        assert red == ["droid-review"]

    def test_paginate_never_combined_with_jq(self):
        """② P1 (round-3) 结构锁：--paginate 的 --jq 按页过滤。

        gh api --paginate 对每个响应页独立应用 --jq，group_by(.name)|
        sort_by(.id)|last 于是每页各选最新——重跑历史超过一页时，后续页上的
        旧红实例作为「该页最新」额外成行进入 awk 判定：第 1 页上的绿/flight
        最新实例 + 第 2 页上的旧红实例 ⇒ 假红（该缺陷类不可由 rerun 恢复，
        PR #248 同款死锁面）。修法（INFRA-766 同款）：--paginate 落盘/收集
        全量后，用一次全局 jq -s（slurp）聚合再做 per-name latest 选择。
        """
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        # 同一 gh api 调用内禁止 --paginate 与 --jq 组合（调用可跨行续行，
        # 先折叠 `\<换行>` 再逐逻辑行检查）
        joined = content.replace("\\\n", " ")
        for line in joined.splitlines():
            if "gh api" in line:
                assert not ("--paginate" in line and "--jq" in line), (
                    "check_zero_red.sh 禁止 --paginate 与 --jq 同调用组合"
                    "（--jq 按页过滤，多页时旧红实例假红）"
                )
        # 全量页必须经 jq -s（slurp）聚合后统一分组（全局 latest，非每页 latest）
        assert "jq -s" in content, (
            "check_zero_red.sh 必须先收集全量分页再用 jq -s 全局聚合——"
            "按页 group_by 在多页场景把旧红实例误判为独立行"
        )

    def test_two_page_scenario_old_red_new_green_judged_green(self):
        """② P1 (round-3) 行为级：两页数据，第 2 页旧红 + 第 1 页最新绿/flight
        ⇒ 判绿（分页不得让旧红实例泄漏成额外判定行）。"""
        script_path = Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"
        content = script_path.read_text()
        # Extract the jq program the script actually runs (single-quoted arg),
        # flags included — raw output (-r) matters: without it jq JSON-encodes
        # the interpolated strings and the awk columns never see tabs.
        jq_match = re.search(r"jq\s+(-s(?:\s+-r)?)\s+'((?:[^'\\]|\\.)*)'", content, re.DOTALL)
        assert jq_match, "check_zero_red.sh 必须包含 jq -s 全局聚合程序"
        jq_flags = jq_match.group(1).split()
        jq_program = jq_match.group(2)

        # Page 1: latest instances — green + in-flight rerun.
        # Page 2: older instances of the same names — the stale red tombstone.
        page1 = json.dumps(
            {
                "total_count": 4,
                "check_runs": [
                    {
                        "name": "droid-review",
                        "id": 900,
                        "status": "in_progress",
                        "conclusion": None,
                    },
                    {"name": "pytest", "id": 910, "status": "completed", "conclusion": "success"},
                ],
            }
        )
        page2 = json.dumps(
            {
                "total_count": 4,
                "check_runs": [
                    {
                        "name": "droid-review",
                        "id": 100,
                        "status": "completed",
                        "conclusion": "failure",
                    },
                    {"name": "pytest", "id": 110, "status": "completed", "conclusion": "success"},
                ],
            }
        )
        # gh --paginate 形态：每页一个 JSON 文档，换行分隔（jq -s 消费此流）
        proc = subprocess.run(
            ["jq", *jq_flags, jq_program],
            input=f"{page1}\n{page2}\n",
            capture_output=True,
            text=True,
            check=True,
        )
        all_rows = [ln for ln in proc.stdout.strip().splitlines() if ln]
        # Script verdict semantics, verbatim: awk -F'\t' completed + non-green.
        GREEN = ("success", "skipped", "neutral", "cancelled")
        red_rows = []
        inflight_rows = []
        for row in all_rows:
            parts = row.split("\t")
            assert len(parts) == 3, f"行必须是 name/status/conclusion 三列: {row!r}"
            name, status, conclusion = parts
            if status != "completed":
                inflight_rows.append(name)
            elif conclusion not in GREEN:
                red_rows.append(f"{name}\t{conclusion}")
        # droid-review 已排除在零红扫描外（ci-ok 与审查解耦）；旧红 failure 不参与判定。
        assert red_rows == [], f"两页数据下旧红实例泄漏为判定行（假红）: {red_rows}"
        assert "droid-review" not in inflight_rows, "排除名不得进入 in-flight 列表"
        assert "pytest" not in inflight_rows, "pytest 全绿不应出现在 in-flight 列表"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
