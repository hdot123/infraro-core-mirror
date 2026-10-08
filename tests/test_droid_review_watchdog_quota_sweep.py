"""droid-review-watchdog quota-sweep 自愈结构测试（2026-08-19 PR #852；D-1 形态更新 2026-10-06）。

回归保护：BYOK 429 配额耗尽自愈（quota-sweep job）的关键属性。
背景：PR #850 实证 Bailian 配额打穿时 review exit 1，job log 只有
无差别 "exited with code 1"，真实 429 签名只在 debug artifact 的
session transcript 里；且配额未恢复时立即 rerun 无意义。quota-sweep
以 schedule 扫描 + artifact 检测 + 恢复窗口 + attempt 限界实现自愈。

这些断言防止自愈逻辑被静默移除或弱化（如去掉恢复窗口导致 rerun
风暴、去掉 attempt 限界导致无限重试）。

D-1 形态更新（2026-10-06，宪法第 1.2(c) 款「断言更新为新形态预期」）：
M2 HOTFIX 的 dispatch-only 桩（附录 A.3 登记的既有违宪态）已消除，触发器
恢复为 workflow_run + schedule + workflow_dispatch。本文件原「锁定不可达
状态」断言按其自带的 M4 恢复提醒设计同步改写为锁定可达新形态；不得以
删除断言的方式消除断言。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).parent.parent
WATCHDOG_PATH = REPO_ROOT / ".github/workflows/droid-review-watchdog.yml"


def _load() -> dict:
    return yaml.safe_load(WATCHDOG_PATH.read_text())


def _on_block() -> dict:
    # yaml 把 `on:` 解析为布尔 True 键
    return _load()[True]


def _quota_sweep_run() -> str:
    data = _load()
    steps = data["jobs"]["quota-sweep"]["steps"]
    return steps[0]["run"]


class TestQuotaSweepJobStructure:
    """quota-sweep job 的存在性与触发配置（D-1 新形态：触发器全开且可达）。"""

    def test_quota_sweep_job_exists(self):
        """quota-sweep job 必须存在（429 自愈入口）。"""
        assert "quota-sweep" in _load()["jobs"]

    def test_triggers_restored(self):
        """D-1：workflow_run + schedule + workflow_dispatch 三触发器全开。

        上游名单 = 两个事件门控 job 消费面的精确并集（CI / Droid Auto
        Review）， schedule 节奏对齐 quota-sweep 恢复窗口扫描设计
        （QUOTA_RECOVERY_WINDOW_SECONDS 默认 1800s = 30 分钟）。
        """
        on = _on_block()
        assert "workflow_run" in on
        assert on["workflow_run"]["workflows"] == ["CI", "Droid Auto Review"]
        assert on["workflow_run"]["types"] == ["completed"]
        assert "schedule" in on
        assert on["schedule"] == [{"cron": "11,41 * * * *"}]
        assert "workflow_dispatch" in on

    def test_quota_sweep_gated_on_schedule_or_dispatch(self):
        """quota-sweep 的 if：schedule 到点可达 + dispatch 手动 sweep 可达。

        workflow_run 事件仍不入（quota 耗尽时立即 rerun 无意义，必须等
        配额恢复窗口）。
        """
        expected = (
            "vars.WATCHDOG_KILL_SWITCH != 'enabled' && "
            "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch')"
        )
        assert _load()["jobs"]["quota-sweep"]["if"] == expected

    def test_quota_sweep_reachable_no_dead_configuration(self):
        """D-1 验收：quota-sweep 不再是永假分支（死配置回归即红）。

        M2 HOTFIX 期本 job 的 if（schedule）与唯一触发器（dispatch）结构性
        互斥，任何触发下恒 skip。新形态下 schedule 到点与 dispatch 手动
        sweep 两条腿都过门；kill-switch（WATCHDOG_KILL_SWITCH=enabled）是
        唯一合法的 skip 路径。
        """
        job = _load()["jobs"]["quota-sweep"]
        job_if = job["if"]
        assert "'schedule'" in job_if, "quota-sweep 与 schedule 触发面互斥（死配置回归）"
        assert "'workflow_dispatch'" in job_if, (
            "quota-sweep 与 dispatch 触发面互斥（dispatch 全 skip 回归）"
        )
        assert "WATCHDOG_KILL_SWITCH" in job_if, "quota-sweep 失去 kill-switch 门（G1 护栏）"

    def test_stale_hotfix_narrative_removed(self):
        """M2 HOTFIX / M4 待恢复叙事已随形态更新清除（防陈旧注释误导）。

        防误导叙事标记以字符串拼接构造：标记字节串不出现在测试源内，
        全仓 grep 防误导叙事标记零命中（含本文件）。
        """
        text = WATCHDOG_PATH.read_text()
        banned = []
        banned.append("[M4 " + "待恢复]")
        banned.append("HOTFIX " + "(M2)")
        banned.append("missing " + "scripts/")
        for marker in banned:
            assert marker not in text

    def test_contract_name_preserved(self):
        """workflow name 契约字符串保留。"""
        assert _load()["name"] == "Droid Review Watchdog"


class TestQuotaSweepDetectionLogic:
    """429 检测与防风暴关键属性。"""

    def test_artifact_signature_grep(self):
        """检测必须 grep transcript 的 quota exceeded 签名（job log 无此特征）。"""
        run = _quota_sweep_run()
        assert "quota exceeded" in run
        assert ".factory/sessions/" in run

    def test_recovery_window_uses_vars(self):
        """恢复窗口通过 QUOTA_RECOVERY_WINDOW_SECONDS 变量配置（默认 1800s）。"""
        run = _quota_sweep_run()
        # 变量引用
        assert "QUOTA_RECOVERY_WINDOW_SECONDS" in run
        # 默认值回退
        assert "1800" in run
        # 使用变量而非硬编码
        assert (
            '-lt "$QUOTA_RECOVERY_WINDOW_SECONDS"' in run
            or "-lt ${QUOTA_RECOVERY_WINDOW_SECONDS}" in run
        )

    def test_attempt_limit_uses_vars(self):
        """run_attempt 限界通过 WATCHDOG_MAX_ATTEMPT 变量配置（默认 3）。"""
        run = _quota_sweep_run()
        # 变量引用
        assert "WATCHDOG_MAX_ATTEMPT" in run
        # 默认值回退
        assert "3" in run

    def test_rerun_uses_failed_jobs_api(self):
        """rerun 必须走 rerun-failed-jobs API（终态 run 才可用）。"""
        run = _quota_sweep_run()
        assert "rerun-failed-jobs" in run

    def test_scan_window_uses_vars(self):
        """扫描窗口通过 QUOTA_SCAN_WINDOW_HOURS 变量配置（默认 6 小时）。"""
        run = _quota_sweep_run()
        # 变量引用
        assert "QUOTA_SCAN_WINDOW_HOURS" in run
        # 默认值回退
        assert "6 hours ago" in run or "QUOTA_SCAN_WINDOW_HOURS" in run
        # 使用变量构造时间范围
        assert "hours ago" in run

    def test_pagination_bounded(self):
        """扫描分页有上界（G2）：显式 page 循环 ≤ MAX_PAGES，不用 --paginate 无界跟随。

        瞬时错误容忍（2026-10-06 返修）：单页 gh api 失败/jq 解析失败记日志
        跳页，不因 set -e 中止 sweep（连续失败达阈才终止）——sweep 自身
        产红正是 D-1 要消除的噪音形态。
        """
        run = _quota_sweep_run()
        assert "MAX_PAGES" in run
        assert 'while [ "$PAGE" -le "$MAX_PAGES" ]' in run
        assert "per_page=50&page=$PAGE" in run
        assert "--paginate" not in run
        assert "if ! RESP=$(gh api" in run
        assert "CONSECUTIVE_API_FAILURES" in run
        assert "page skipped" in run

    def test_dry_run_first_runs_observe_only(self):
        """dry-run 首跑（G6）：auto 模式前 2 次 sweep observe，检出只记日志不真正 rerun。

        observe 不虚增 rerun 计数（2026-10-06 返修）：检出数（DETECTED）与
        rerun 数（RERUNNED）分离，观测数据与 G7 记录不失真。
        """
        run = _quota_sweep_run()
        assert "DRY_RUN" in run
        assert 'if [ "$SWEEP_COUNT" -le 2 ]' in run
        # observe 分支拦截真实 rerun 调用
        assert 'if [ "$DRY_RUN_MODE" = "true" ]' in run
        assert "DETECTED=" in run
        assert run.index("DETECTED=$((DETECTED + 1))") < run.index(
            'if [ "$DRY_RUN_MODE" = "true" ]'
        )

    def test_kill_switch_double_guard(self):
        """kill-switch（G1）：job 级 if 之外步内仍有 enabled 双保险。"""
        run = _quota_sweep_run()
        assert "KILL_SWITCH" in run
        assert '[ "$KILL_SWITCH" = "enabled" ]' in run

    def test_linear_observability_degradable(self):
        """Linear 可观测（G7）：sweep 状态写入 Linear，凭证缺席/失败降级不阻断。

        幂等护栏（2026-10-06 返修）：lookup 失败（不可解析响应）与未找到
        （空结果）区分，仅未找到才 issueCreate；lookup 失败跳过本轮写入
        （防 30 分钟 tick 重复建 issue）。
        """
        run = _quota_sweep_run()
        assert "api.linear.app/graphql" in run
        assert "issueCommentCreate" in run
        # 降级语义：warning 而非 exit 非 0
        assert "::warning::" in run
        # lookup 结果校验先于 issueCreate（重复建 issue 回归即红）
        assert run.index(".data.project.issues.nodes | type") < run.index("issueCreate(input:")
        assert "skipping this round's write" in run
        # 网络层兜底（三轮返修）：lookup/create curl 失败落显式标记值，
        # 不在 set -euo pipefail 下中止 sweep（Linear 不可达不红 job）
        assert "|| LOOKUP_RESP='G7_LOOKUP_FAILED'" in run, (
            "G7 lookup curl 缺网络层兜底（Linear 不可达即红整个 sweep）"
        )
        assert "|| CREATE_RESP='G7_CREATE_FAILED'" in run, (
            "G7 create curl 缺网络层兜底（Linear 不可达即红整个 sweep）"
        )

    def test_fail_closed_no_conclusion_change(self):
        """门禁语义：脚本只请求 rerun，不写 check 结论、不绕过门禁。"""
        run = _quota_sweep_run()
        # 不存在任何 check 结论改写 API
        for forbidden in ("check-runs", "annotations", "--admin"):
            assert forbidden not in run


class TestExistingJobsPreserved:
    """原有 watchdog 职责不受影响。"""

    def test_self_heal_rerun_preserved(self):
        """self-heal-rerun（503 自愈）保留且限界通过 WATCHDOG_MAX_ATTEMPT 变量配置。"""
        data = _load()
        assert "self-heal-rerun" in data["jobs"]
        # run_attempt 限界已从 if: 移到 shell run block（通过 WATCHDOG_MAX_ATTEMPT 变量）
        job = data["jobs"]["self-heal-rerun"]
        run_block = job["steps"][-1]["run"]
        assert "WATCHDOG_MAX_ATTEMPT" in run_block
        assert "MAX_ATTEMPT" in run_block

    def test_cancel_on_ci_fail_preserved(self):
        """cancel-on-ci-fail（CI 红取消烧钱 review）保留。"""
        assert "cancel-on-ci-fail" in _load()["jobs"]

    def test_timeout_minutes_bounded(self):
        """quota-sweep 有 timeout 上界（防 sweep 自身挂死）。"""
        assert _load()["jobs"]["quota-sweep"]["timeout-minutes"] <= 15
