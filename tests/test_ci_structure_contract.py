"""CI 结构契约测试（INFRA-580）

锁定 12-job 结构化 CI 的拓扑不变量，防止 bundle 化后的结构回退（lint/type/
advisory/test-groups 四个 bundle job 与六个独立 job 的集合完整性）、ci-ok
needs 漏配、advisory 被恢复 continue-on-error 掩蔽失败、marker 参数漂移、
runner 标签漂移、concurrency 语义漂移。

与 tests/test_naming_contract.py 的分工：naming_contract 锁既有 check 名的
字节级契约（architecture.md §2）；本文件锁结构层——job 集合完整性、ci-ok
依赖收口与逐项阻断、advisory 零红语义（INFRA-595：无 continue-on-error，
失败即红）、关键命令参数、PR 限定 cancel-in-progress。

2026-08-29 容量收敛（runner-capacity-one-shot）：19 job → 12 job——
lint-bundle（ruff/shellcheck/actionlint/repo-consistency）、type-bundle
（mypy×2）、advisory-bundle（advisory×3）、test-groups（schema/security/
business_policy 三段顺序）；pytest / integration-tests / e2e-tests /
guards / health-check / ci-ok 六个独立保持。
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github/workflows/ci.yml"

# 12 个 job 的完整集合（19 → 10 容量收敛，2026-08-29；+1 notify-ci-complete
# webhook 注入，INFRA-569；+1 gate-tests required check（substrate-gate-suite，
# 2026-09-13 先红着上线，2026-09-18 转正 required checks；解冻判据③）：五道门
# 持续运行输出红/绿，存量红项登记后清理，全部转绿后作为 required check 阻断合并；
# 快照只对齐当前 main：后续 ci.yml 变更由各自 feature 同步本表）
EXPECTED_JOBS = frozenset(
    {
        # 聚合锚点（命名契约：不可重命名，见 architecture.md §2）
        "pytest",
        # bundle（容量收敛四合一）
        "lint-bundle",
        "type-bundle",
        "advisory-bundle",
        "test-groups",
        # 独立 job
        "guards",
        "integration-tests",
        "e2e-tests",
        "health-check",
        # 聚合门禁（branch protection required check）
        "ci-ok",
        # CI 完成 webhook 通知（INFRA-569：对齐 memory 仓同构 job）
        "notify-ci-complete",
        # substrate 五道门 required check（substrate-gate-suite，解冻判据③：转正 required）：五道门转正后阻断合并
        "gate-tests",
    }
)

ADVISORY_JOBS = frozenset({"advisory-bundle"})
# notify-ci-complete is a downstream notification job (needs ci-ok, not the
# other way around), so exclude it from BLOCKING_JOBS.
# gate-tests 已转正 required check（解冻判据③），不再属于 advisory，也不单独在 GATE_JOBS 辨识。
BLOCKING_JOBS = EXPECTED_JOBS - ADVISORY_JOBS - {"ci-ok", "notify-ci-complete"}

# 独立专项测试组 → marker
TEST_GROUP_MARKERS = {
    "integration-tests": "integration",
    "e2e-tests": "e2e",
}

# test-groups bundle 的三段 marker（顺序：schema → security → business_policy）
TEST_GROUPS_BUNDLE_MARKERS = ("schema", "security", "business_policy")

# BLOCKING_JOBS = EXPECTED_JOBS - ADVISORY_JOBS - {"ci-ok", "notify-ci-complete", "gate-tests"}
# 但 gate-tests 已转正 required check，需包含在 BLOCKING_JOBS 中（由 test_each_blocking_job_enforced 验证）

GUARD_SCRIPTS = (
    "scripts/check_boundary.py",
    "scripts/check_doc_classification.py",
    "scripts/check_fix_has_test.py",
    "scripts/check_pr_ref_consistency.py",
)


def _load_jobs() -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "ci.yml 必须是 YAML mapping"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict), "ci.yml 必须定义 jobs mapping"
    return jobs


@pytest.fixture(scope="module")
def ci_jobs() -> dict[str, dict[str, Any]]:
    return _load_jobs()


def _job_run_script(jobs: dict[str, dict[str, Any]], job: str) -> str:
    """拼接某 job 全部 run 步骤的脚本文本（YAML block scalar 已去缩进）。"""
    steps = jobs[job].get("steps") or []
    runs = [str(step["run"]) for step in steps if "run" in step]
    assert runs, f"{job} 必须包含至少一个 run 步骤"
    return "\n".join(runs)


class TestJobTopology:
    def test_expected_job_set_present(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """job 集合精确匹配：静默删除/新增 job 都会触发本契约。"""
        assert frozenset(ci_jobs) == EXPECTED_JOBS

    def test_ci_ok_needs_all_blocking_jobs(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """ci-ok 的 needs 必须收口全部 9 个前置 job（含 advisory-bundle，供结果透出）。"""
        needs = set(ci_jobs["ci-ok"].get("needs") or [])
        missing = (BLOCKING_JOBS | ADVISORY_JOBS) - needs
        assert not missing, f"ci-ok needs 缺失: {sorted(missing)}"

    def test_ci_ok_always_runs(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """聚合门禁必须 always() 运行：前置失败时 ci-ok 也要给出明确红叉。"""
        assert ci_jobs["ci-ok"].get("if") == "always()"


class TestConcurrencyContract:
    """2026-08-29 容量收敛的 concurrency 语义。

    PR 连环 push 取消同 ref 的进行中旧 run（省 pve 双机排队）；main push
    永不取消——合并后 main 全绿验证不能被后续事件打断（禁止裸 true）。
    """

    def test_concurrency_group_expression(self) -> None:
        doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        concurrency = doc.get("concurrency")
        assert isinstance(concurrency, dict), "ci.yml 必须声明顶层 concurrency"
        assert concurrency["group"] == "ci-${{ github.workflow }}-${{ github.ref }}"

    def test_cancel_in_progress_pr_only(self) -> None:
        """cancel-in-progress 必须是 PR 限定表达式，禁止裸 true。"""
        doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
        concurrency = doc["concurrency"]
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}", (
            "cancel-in-progress 必须 PR 限定（裸 true 会打断合并后 main 全绿验证）"
        )


class TestCiOkEnforcement:
    def test_each_blocking_job_enforced(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """needs 只是等待关系；每个阻塞 job 必须在聚合脚本中被显式判定。"""
        script = _job_run_script(ci_jobs, "ci-ok")
        for job in sorted(BLOCKING_JOBS):
            expected = (
                f'[[ "${{{{ needs.{job}.result }}}}" == "success" ]] '
                f'|| {{ echo "FAIL: {job}"; FAILED=1; }}'
            )
            assert expected in script, f"ci-ok 未显式阻断 {job}（缺失逐项判定行）"

    def test_advisory_blocks_merge(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """用户铁律（2026-08-28）：写死不允许红色合并，一个都不允许。

        advisory jobs 必须被接入 ci-ok 阻断判定，任一红则不可合并。
        bundle 化后由 advisory-bundle 承接三个 advisory 的零红语义。

        INFRA-595：job 级 continue-on-error 会让 needs.<job>.result 恒为
        success（continue-on-error 之后的值），ci-ok 的 .result 判定沦为空转
        ——run 33129232081 实证：advisory-deptry check-run 为 failure 而
        .result 报 success。修复：移除 advisory 的 continue-on-error，
        使失败成为红 check-run，.result 判定与 GitHub API 全 check-runs
        扫描双保险均真实生效。
        """
        script = _job_run_script(ci_jobs, "ci-ok")
        for job in ADVISORY_JOBS:
            expected = (
                f'[[ "${{{{ needs.{job}.result }}}}" == "success" ]]'
                f' || {{ echo "FAIL: {job}"; FAILED=1; }}'
            )
            assert expected in script, f"advisory {job} 未接入阻断判定（违反零红铁律）"


class TestAdvisorySemantics:
    @pytest.mark.parametrize("job", sorted(ADVISORY_JOBS))
    def test_advisory_no_continue_on_error(
        self, ci_jobs: dict[str, dict[str, Any]], job: str
    ) -> None:
        """INFRA-595 零红铁律：advisory 不得设置 job 级 continue-on-error。

        continue-on-error 会（a）让 needs.<job>.result 恒为 success，ci-ok
        判定空转；（b）PR checks 面板显示为橙而非红。零红政策下 advisory
        失败必须直接阻断合并。
        """
        assert ci_jobs[job].get("continue-on-error") is None, (
            f"{job} 不得设置 continue-on-error（零红铁律：advisory 失败必须红）"
        )


class TestSubstrateGates:
    """substrate 五道门 required check 契约（substrate-gate-suite，2026-09-13 裁定）。

    2026-09-18 解冻判据③转正：gate-tests 从 step-level continue-on-error (advisory)
    转为 required check。五道门持续运行输出红/绿；存量红项逐条登记于
    substrate/gate0-exemptions.md，由 declaration-template-interface-fix /
    engine-substrate-boundary / legacy-repo-disposition / bookkeeping 清理。
    转正后 gate-tests 成为 ci-ok needs 的阻断项（gate 红则 ci-ok 失败）。
    注意与 advisory-bundle 的零红铁律（TestAdvisorySemantics）区分：那是
    「advisory 红必须阻断」，这是「required check 红则 PR 合并失败」。
    """

    def test_gate_job_has_no_job_level_continue_on_error(
        self, ci_jobs: dict[str, dict[str, Any]]
    ) -> None:
        """gate-tests 禁止 job 级 continue-on-error（infra 失败保持红可见）。"""
        gate = ci_jobs["gate-tests"]
        assert gate.get("continue-on-error") is None, (
            "gate-tests 不得设置 job 级 continue-on-error（checkout/venv 等 infra 失败必须红可见）"
        )

    def test_gate_steps_have_no_continue_on_error(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """五个门步全部无 step-level continue-on-error（转正 required check）。"""
        steps = [
            s
            for s in ci_jobs["gate-tests"].get("steps") or []
            if "gate" in str(s.get("name", "")).lower()
        ]
        assert len(steps) == 5, f"应有 5 个门步，found {len(steps)}"
        for step in steps:
            assert step.get("continue-on-error") is None, (
                f"门步「{step.get('name')}」不得设置 continue-on-error（转正 required check，"
                "存量红需暴露为 failure）"
            )

    def test_gate_job_in_ci_ok_needs(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """gate-tests 已进 ci-ok needs（转正 required check 阻断）。"""
        needs = set(ci_jobs["ci-ok"].get("needs") or [])
        assert "gate-tests" in needs, (
            "gate-tests 必须进 ci-ok needs（转正 required check，红则阻断合并）"
        )

    def test_gate_job_runs_all_five_gates(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """五道门脚本全部在场（substrate/gates/ 门0-门4，迁出 tests/ 后路径）。"""
        script = _job_run_script(ci_jobs, "gate-tests")
        for gate in (
            "gate0_coverage",
            "gate1_interface",
            "gate2_cross_repo",
            "gate3_exposure",
            "gate4_timing_bootstrap",
        ):
            assert f"substrate/gates/{gate}.py" in script, f"gate-tests 缺 {gate}"


class TestNotifyCiComplete:
    """notify-ci-complete webhook 通知契约（INFRA-690）。

    INFRA-690 要求 payload 同时携带 CI status 与 run URL：status 供
    trigger-ci-droid.sh 注入链判定绿红，run_url 供 webhook 网关下游自动化与
    人工排查直达 CI 运行页。#137 已落地 job 骨架但 payload 缺 run_url，
    本契约锁定该字段不可回退。
    """

    def test_notify_job_needs_ci_ok_and_always(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """通知 job 必须 needs ci-ok 且 always()：CI 红也要通知（下游
        需要感知失败并触发修复流程），仅限 PR 事件。"""
        job = ci_jobs["notify-ci-complete"]
        raw_needs = job.get("needs")
        needs = {raw_needs} if isinstance(raw_needs, str) else set(raw_needs or [])
        assert needs == {"ci-ok", "gate-tests"}, (
            "notify-ci-complete 必须 needs [ci-ok, gate-tests]（聚合后通知，"
            "含 substrate advisory 门跑完再通知）"
        )
        assert job.get("if") == "always() && github.event_name == 'pull_request'"

    def test_payload_includes_status_and_run_url(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """payload 必须同时含 status 与 run_url（INFRA-690 核心要求）。"""
        script = _job_run_script(ci_jobs, "notify-ci-complete")
        assert "needs.ci-ok.result" in script, "payload status 必须取自 ci-ok 结果"
        # run_url 源自 step env（github.run_id），run 脚本经 $RUN_URL 引用
        job_dump = yaml.safe_dump(ci_jobs["notify-ci-complete"], allow_unicode=True)
        assert "github.run_id" in job_dump, "payload 缺 run_url 源（github.run_id 未注入 env）"
        assert "run_url" in script, "payload 缺 run_url 字段"
        jq_build = "{repo:$repo, pr_number:$pr_number, branch:$branch, sha:$sha, status:$status, run_url:$run_url}"
        assert jq_build in script, "jq payload 构造缺 run_url 键（字段不可回退）"

    def test_delivery_failure_non_blocking_with_telemetry(
        self, ci_jobs: dict[str, dict[str, Any]]
    ) -> None:
        """通知失败不得阻断（exit 0 + PostHog 事件）：通知是旁路，红 CI
        不能因 webhook 投递失败而误报为通过。"""
        script = _job_run_script(ci_jobs, "notify-ci-complete")
        assert "ci_webhook_send_failed" in script, "投递失败必须上报 PostHog 事件"
        assert "exit 0" in script, "通知失败必须 exit 0（旁路语义）"

    def test_run_steps_use_strict_mode(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """bash 加固契约（INFRA-710 回归锁定，INFRA-711）。

        #151（2026-08-31）为 secret 验证与状态 webhook 两个 run block 补
        ``set -euo pipefail`` 时未走 Droid 流程、无回归锁定，Linear 状态
        门禁因此回退并重开跟踪 Issue。本契约补锁：job 内全部 run 步骤
        首行必须是 ``set -euo pipefail``——既有步骤被摘除严格模式、或新增
        run 步骤未启用严格模式，都会在此变红。
        """
        steps = ci_jobs["notify-ci-complete"].get("steps") or []
        run_steps = [
            (step.get("name") or "<unnamed>", str(step["run"])) for step in steps if "run" in step
        ]
        assert run_steps, "notify-ci-complete 必须包含至少一个 run 步骤"
        offenders = []
        for name, script in run_steps:
            lines = script.strip().splitlines()
            if not lines or lines[0].strip() != "set -euo pipefail":
                offenders.append(name)
        assert not offenders, (
            "notify-ci-complete 以下 run 步骤首行缺少 set -euo pipefail"
            f"（严格模式契约，INFRA-710）: {offenders}"
        )


class TestRunnerLabels:
    @pytest.mark.parametrize("job", sorted(EXPECTED_JOBS))
    def test_all_jobs_on_self_hosted_runner(
        self, ci_jobs: dict[str, dict[str, Any]], job: str
    ) -> None:
        runs_on = ci_jobs[job].get("runs-on")
        assert runs_on == "ubuntu-latest", f"{job} runs-on 必须是 ubuntu-latest"


class TestBundles:
    """bundle 化步骤语义保持（2026-08-29 容量收敛）。"""

    def test_lint_bundle_four_in_one(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """lint-bundle 必须四合一：ruff（check+format 两半）/ shellcheck /
        actionlint / repo-consistency，缺一即门禁降级。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "ruff check ." in script
        assert "ruff format --check ." in script
        assert "shellcheck -x" in script
        assert "repo_health_check.sh --ci" in script

    def test_type_bundle_mypy_x2(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """type-bundle 必须 mypy×2：src 与 scripts 分域各跑一次
        （#61 bundle 化遗留的 Run mypy 重复步骤已去重，src 域禁止双跑）。"""
        script = _job_run_script(ci_jobs, "type-bundle")
        assert script.count("mypy --strict src/infra_core") == 1
        assert "mypy --strict scripts/" in script

    def test_advisory_bundle_three_in_one(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """advisory-bundle 必须三合一：pip-audit / deptry / 遥测覆盖率审计。"""
        script = _job_run_script(ci_jobs, "advisory-bundle")
        assert "pip-audit --progress-spinner off" in script
        assert "deptry ." in script
        assert "audit_telemetry_coverage.sh" in script

    def test_test_groups_three_segments_ordered(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """test-groups 三段 marker 顺序跑：schema → security → business_policy。"""
        script = _job_run_script(ci_jobs, "test-groups")
        positions = []
        for marker in TEST_GROUPS_BUNDLE_MARKERS:
            assert f"-m {marker}" in script, f"test-groups 缺失 -m {marker}"
            positions.append(script.index(f"-m {marker}"))
        assert positions == sorted(positions), (
            f"test-groups 三段顺序漂移：应为 {TEST_GROUPS_BUNDLE_MARKERS}"
        )


class TestTestGroups:
    @pytest.mark.parametrize("job,marker", sorted(TEST_GROUP_MARKERS.items()))
    def test_group_runs_marker_with_parallelism(
        self, ci_jobs: dict[str, dict[str, Any]], job: str, marker: str
    ) -> None:
        script = _job_run_script(ci_jobs, job)
        assert f"-m {marker}" in script, f"{job} 必须 按 marker 分组"
        assert "-n 4" in script, f"{job} 必须保持 4 worker 并行"
        assert "--no-cov" in script, f"{job} 专项组不重复计覆盖率（主 pytest job 已覆盖）"

    def test_bundle_segments_keep_parallelism_contract(
        self, ci_jobs: dict[str, dict[str, Any]]
    ) -> None:
        """test-groups 三段逐段保持 -n 4 + --no-cov 契约。"""
        script = _job_run_script(ci_jobs, "test-groups")
        assert script.count("-n 4") >= len(TEST_GROUPS_BUNDLE_MARKERS)
        assert script.count("--no-cov") >= len(TEST_GROUPS_BUNDLE_MARKERS)


class TestDomainMypy:
    def test_src_strict(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        assert "mypy --strict src/infra_core" in _job_run_script(ci_jobs, "type-bundle")

    def test_scripts_strict(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        assert "mypy --strict scripts/" in _job_run_script(ci_jobs, "type-bundle")


class TestExistingJobInvariants:
    def test_pytest_main_command_unchanged(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """主 pytest job 铁律：-n 8 --dist loadgroup + 覆盖率地板（ramp-up 见 pyproject）。"""
        script = _job_run_script(ci_jobs, "pytest")
        assert "-n 8" in script and "--dist loadgroup" in script
        assert "--cov-fail-under=45" in script

    def test_guards_run_all_four_guard_scripts(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        script = _job_run_script(ci_jobs, "guards")
        for guard_script in GUARD_SCRIPTS:
            assert f"python {guard_script}" in script, f"guards 缺失 {guard_script}"

    def test_e2e_includes_cli_smoke(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        assert "scripts/cli_smoke_test.sh" in _job_run_script(ci_jobs, "e2e-tests")


class TestActionlintHostFirst:
    """actionlint 步骤宿主优先契约（2026-08-29 [REDACTED-HOST] raw 直连黑洞修复）。

    旧实现每 run 无条件 ``bash <(curl raw.githubusercontent.com/.../download-actionlint.bash)``
    ——curl 是非 git 直连，不走 runner insteadOf 镜像，[REDACTED-HOST] 出口对该域间歇黑洞
    （2026-08-29 03:23 实证 134s timeout，main CI run 33225582434/33231155505
    连续三轮红，仅剩此一条非镜像路径）。[REDACTED-HOST] runner 已预装
    /usr/local/bin/actionlint 1.7.11（Layer 1 就绪），契约：版本 ≥1.7 的宿主
    二进制直接使用，仅缺失/过旧时才允许 fallback 下载（GitHub-hosted 兼容）。

    2026-08-29 bundle 化后 actionlint 步骤位于 lint-bundle 内，契约不变。
    """

    def test_host_binary_probe_present(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """actionlint 步骤必须先探测宿主二进制（PATH 优先）。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "command -v actionlint" in script, "缺少宿主二进制探测（PATH 优先分支）"

    def test_host_version_gate(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """宿主版本必须 ≥1.7 才直接使用（防过旧宿主二进制误用）。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "-ge 7" in script, "缺少宿主版本 ≥1.7 门限判断"

    def test_fallback_download_warns(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """宿主缺失时 fallback 下载必须打 ::warning（异常态要显式暴露）。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "::warning::actionlint not found on host" in script, "fallback 分支缺少 ::warning"

    def test_download_only_after_host_probe(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """curl 下载只允许存在于宿主探测之后（禁止恢复每 run 无条件首下载）。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "download-actionlint.bash" in script, (
            "fallback 下载路径必须保留（GitHub-hosted 兼容）"
        )
        probe_at = script.index("command -v actionlint")
        download_at = script.index("download-actionlint.bash")
        assert probe_at < download_at, "下载路径出现在宿主探测之前（退化为无条件下载）"

    def test_invocation_keeps_color_flag(self, ci_jobs: dict[str, dict[str, Any]]) -> None:
        """lint 调用保持 actionlint -color（宿主与 fallback 两条路径一致）。"""
        script = _job_run_script(ci_jobs, "lint-bundle")
        assert "actionlint -color" in script


# ─── 零红扫描移序契约（2026-09-22 audit-defense-hardening）────────────────
# ci-ok 内两个串行 step 的脚本锚点：droid-review 轮询（最长 ~60min）必须先于
# 零红聚合（GitHub API 扫描本 commit 全部 check-runs）执行——轮询窗口内变红的
# check 才能被快照覆盖。锚点用脚本调用而非 step 名（名字可漂移）。
DROID_REVIEW_STEP_SCRIPT = "scripts/check_droid_review.sh"
ZERO_RED_STEP_SCRIPT = "scripts/check_zero_red.sh"


def _unique_step_index_by_script(steps: list[dict[str, Any]], needle: str) -> int:
    """返回唯一一个 run 脚本含 needle 的 step index；0 个或多个都判失败。"""
    matches = [index for index, step in enumerate(steps) if needle in str(step.get("run") or "")]
    assert len(matches) == 1, (
        f"ci-ok 内 run 脚本含 {needle} 的 step 应恰有 1 个（found {len(matches)}）——"
        "零红移序契约的锚点必须唯一，否则顺序断言失去意义"
    )
    return matches[0]


class TestCiOkStepOrder:
    """零红扫描移序契约（2026-09-22 audit-defense-hardening）。

    背景：零红聚合（check_zero_red.sh：GitHub API 扫描本 commit 全部
    check-runs）原先先于 droid-review 轮询（check_droid_review.sh：最长
    120×30s≈60min）执行，轮询窗口内变红的 check（droid-review 自身、qa-ok、
    late rerun）对先取到的快照不可见，形成「轮询期间变红不感知」的时序窗口。
    移序后零红快照覆盖整个轮询窗口，判定语义不变（任一非
    success/skipped/neutral 即红，ci-ok 自身排除防自引用）。

    本契约锁死新顺序：ci-ok 内 droid-review 轮询 step 必须先于零红聚合 step。
    needs 边、12 job 集合、``if: always()``、notify-ci-complete 契约由各自既有
    契约锁，不在此重复。
    """

    def test_droid_review_polling_precedes_zero_red_scan(
        self, ci_jobs: dict[str, dict[str, Any]]
    ) -> None:
        """零红聚合必须排在 droid-review 轮询之后（反序即恢复时序盲区）。"""
        steps = ci_jobs["ci-ok"].get("steps") or []
        assert steps, "ci-ok 必须包含 steps"
        droid_review_at = _unique_step_index_by_script(steps, DROID_REVIEW_STEP_SCRIPT)
        zero_red_at = _unique_step_index_by_script(steps, ZERO_RED_STEP_SCRIPT)
        assert droid_review_at < zero_red_at, (
            "零红聚合必须在 droid-review 轮询之后执行（移序契约 2026-09-22）："
            f"droid-review step index={droid_review_at}，零红 step index={zero_red_at}"
            "——反序会让轮询窗口内变红的 check 逃出快照"
        )

    def test_moved_zero_red_step_keeps_execution_wiring(
        self, ci_jobs: dict[str, dict[str, Any]]
    ) -> None:
        """换序是纯块搬移：零红 step 的 GH_TOKEN 注入与 repo/sha 实参不得丢失。"""
        steps = ci_jobs["ci-ok"].get("steps") or []
        zero_red_step = steps[_unique_step_index_by_script(steps, ZERO_RED_STEP_SCRIPT)]
        env = zero_red_step.get("env") or {}
        assert env.get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}", (
            "零红 step 必须保留 GH_TOKEN 注入（gh api check-runs 扫描依赖）"
        )
        script = str(zero_red_step.get("run") or "")
        assert "github.event.pull_request.head.sha || github.sha" in script, (
            "零红 step 必须保留 PR head sha 回退 github.sha 的 COMMIT_SHA 解析"
        )
        assert f'{ZERO_RED_STEP_SCRIPT} "${{{{ github.repository }}}}" "$COMMIT_SHA"' in script, (
            "零红 step 必须保留 check_zero_red.sh 的 repo + COMMIT_SHA 实参调用"
        )
