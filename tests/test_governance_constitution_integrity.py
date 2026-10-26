"""两层完整性断言（项目最高宪法 Enforcement 落地，2026-10-06 立法）。

最高规则第 1 条【自进化层与自愈层不可侵犯】要求 CI 常驻两层完整性断言：
两层资产被破坏、禁用、绕过或实质性削弱的，本测试立即变红，阻断合并。

断言形态基线（宪法附录 A.3 登记）：watchdog 断言已按宪法第 1.2(c) 款从
「文件与接口存在性」升级为「触发器健康基线」（D-1，2026-10-06 大哥批准
消除既有违宪态：M2 HOTFIX 的 dispatch-only 桩）。触发器全开 + 三 job 与
触发面无结构性互斥自此是被锁定的预期形态；任何再禁用即违宪。细则契约另
见 tests/test_droid_review_watchdog_contract.py（护栏逐条）与
tests/test_droid_review_watchdog_quota_sweep.py（quota-sweep 自愈属性）。
不得以删除断言的方式消除断言。

宪法全文见仓库根 GOVERNANCE.md（最高治理文件，效力高于任务规则）。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.business_policy

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"
GOVERNANCE = REPO_ROOT / "GOVERNANCE.md"


def _load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _on_block(data: dict) -> dict:
    # yaml 把 `on:` 解析为布尔 True 键
    return data[True]


class TestSelfHealingLayerIntegrity:
    """自愈层文件与接口存在性（宪法附录 A.1）。"""

    def test_watchdog_file_exists(self):
        path = WORKFLOWS / "droid-review-watchdog.yml"
        assert path.exists(), "自愈层资产 droid-review-watchdog.yml 被删除（违宪）"
        data = _load_workflow(path)
        assert data["name"] == "Droid Review Watchdog", "watchdog 工作流名被改名（契约中断）"

    def test_handlers_file_exists_with_workflow_call(self):
        path = WORKFLOWS / "droid-review-watchdog-handlers.yml"
        assert path.exists(), "自愈层资产 droid-review-watchdog-handlers.yml 被删除（违宪）"
        data = _load_workflow(path)
        on = _on_block(data)
        assert "workflow_call" in on, "handlers 失去 workflow_call reusable 接口（接口中断）"
        assert "mode" in on["workflow_call"]["inputs"], "handlers 失去 mode 选择输入（接口中断）"

    def test_handlers_dual_mode_interface_present(self):
        """self-heal-rerun / cancel-on-ci-fail 双 mode 接口在位（mode 门控两个 handler job）。"""
        data = _load_workflow(WORKFLOWS / "droid-review-watchdog-handlers.yml")
        jobs = data["jobs"]
        assert "self-heal-rerun" in jobs, "self-heal-rerun handler job 被移除（自愈面削弱）"
        assert "cancel-on-ci-fail" in jobs, "cancel-on-ci-fail handler job 被移除（自愈面削弱）"
        assert jobs["self-heal-rerun"]["if"] == "inputs.mode == 'self-heal-rerun'"
        assert jobs["cancel-on-ci-fail"]["if"] == "inputs.mode == 'cancel-on-ci-fail'"

    def test_watchdog_triple_mode_surface_present(self):
        """watchdog 主文件三模式 job 面（quota-sweep / self-heal-rerun / cancel-on-ci-fail）在位。"""
        jobs = _load_workflow(WORKFLOWS / "droid-review-watchdog.yml")["jobs"]
        assert "quota-sweep" in jobs, "quota-sweep job 被移除（BYOK 429 自愈面削弱）"
        assert "self-heal-rerun" in jobs, "self-heal-rerun job 被移除（503 自愈面削弱）"
        assert "cancel-on-ci-fail" in jobs, "cancel-on-ci-fail job 被移除（CI 失败止损面削弱）"

    def test_watchdog_triggers_health_baseline(self):
        """D-1 触发器健康基线（宪法第 1.2(c) 款新形态锁定，2026-10-06）。

        自愈层的预期形态 = 触发器全开（workflow_run + schedule +
        workflow_dispatch）且三 job 与触发面无结构性互斥（每条触发腿至少
        一个 job 可达）。M2 HOTFIX 的 dispatch-only 桩使任何触发下全部 job
        恒 skip，属宪法附录 A.3 登记的既有违宪态，已由 D-1 消除；任何把
        watchdog 改回禁用/死配置形态的变更即违宪。
        """
        data = _load_workflow(WORKFLOWS / "droid-review-watchdog.yml")
        on = _on_block(data)
        for trigger in ("workflow_run", "schedule", "workflow_dispatch"):
            assert trigger in on, f"watchdog 触发器 {trigger} 被禁用（自愈层被禁用，违宪）"
        assert on["workflow_run"]["workflows"], "workflow_run 上游名单为空（触发面失效）"
        assert on["schedule"], "schedule 定时面为空（自扫心跳失效）"
        jobs = data["jobs"]
        assert "github.event_name == 'workflow_run'" in jobs["self-heal-rerun"]["if"], (
            "self-heal-rerun 与 workflow_run 触发面互斥（死配置回归）"
        )
        assert "github.event_name == 'workflow_run'" in jobs["cancel-on-ci-fail"]["if"], (
            "cancel-on-ci-fail 与 workflow_run 触发面互斥（死配置回归）"
        )
        sweep_if = jobs["quota-sweep"]["if"]
        assert (
            "github.event_name == 'schedule'" in sweep_if
            and "github.event_name == 'workflow_dispatch'" in sweep_if
        ), "quota-sweep 与 schedule/dispatch 触发面互斥（死配置回归）"

    def test_watchdog_runtime_guard_variables_referenced(self):
        """运行时变量族被真实引用（变量族被架空 = 自愈面被实质性削弱）。"""
        text = (WORKFLOWS / "droid-review-watchdog.yml").read_text()
        for var in (
            "WATCHDOG_MAX_ATTEMPT",
            "QUOTA_SCAN_WINDOW_HOURS",
            "QUOTA_RECOVERY_WINDOW_SECONDS",
        ):
            assert var in text, f"运行时变量 {var} 不再被 watchdog 引用（自愈面削弱）"

    def test_auto_merge_self_heal_surface_present(self):
        """auto-merge 的 BEHIND update-branch self-heal 面在位。"""
        text = (WORKFLOWS / "auto-merge-pipeline.yml").read_text()
        assert "update-branch self-heal" in text, "auto-merge 的 self-heal 面被移除（自愈面削弱）"


class TestSelfEvolutionLayerIntegrity:
    """自进化层文件与接口存在性（宪法附录 A.2）。"""

    def test_evolution_scan_exists_with_schedule_and_call(self):
        path = WORKFLOWS / "evolution-scan.yml"
        assert path.exists(), "自进化层资产 evolution-scan.yml 被删除（违宪）"
        data = _load_workflow(path)
        on = _on_block(data)
        assert "schedule" in on, "evolution-scan 失去自扫定时面（自进化层被禁用）"
        assert on["schedule"] == [{"cron": "17,47 * * * *"}], "evolution-scan 自扫 cron 被篡改"
        assert "workflow_call" in on, "evolution-scan 失去 workflow_call reusable 接口"

    def test_evolution_heartbeat_exists_with_schedule_and_call(self):
        path = WORKFLOWS / "evolution-heartbeat.yml"
        assert path.exists(), "自进化层资产 evolution-heartbeat.yml 被删除（违宪）"
        data = _load_workflow(path)
        on = _on_block(data)
        assert "schedule" in on, "evolution-heartbeat 失去自扫定时面（自进化层被禁用）"
        assert on["schedule"] == [{"cron": "53 */2 * * *"}], "evolution-heartbeat 自扫 cron 被篡改"
        assert "workflow_call" in on, "evolution-heartbeat 失去 workflow_call reusable 接口"

    def test_evolution_engine_modules_present(self):
        """引擎三模块在位（scanner / heartbeat / self_audit）。"""
        for module in ("evolution_scanner.py", "evolution_heartbeat.py", "evolution_self_audit.py"):
            path = REPO_ROOT / "src/infra_core/engine" / module
            assert path.exists(), f"引擎模块 {module} 被删除（自进化层削弱）"


class TestConstitutionDocumentIntegrity:
    """宪法文件本身在位且条款完整（Enforcement 的记录载体）。"""

    def test_governance_file_exists_at_repo_root(self):
        assert GOVERNANCE.exists(), "宪法文件被从仓库根删除（最高治理文件不可删除）"

    def test_constitution_contains_supreme_rule_with_all_three_clauses(self):
        text = GOVERNANCE.read_text()
        assert "自进化层与自愈层不可侵犯" in text, "最高规则第 1 条标题缺失"
        assert "层级宣言" in text, "第 1.1 层级宣言缺失"
        assert "三条件合规路径" in text, "第 1.2 三条件合规路径缺失"
        assert "Enforcement" in text, "第 1.3 Enforcement 缺失"
        assert "2026-10-06" in text, "立法裁定日期缺失（第 1.2(a) 款带日期决策记录要求）"

    def test_constitution_appendix_lists_both_layers(self):
        text = GOVERNANCE.read_text()
        assert "两层资产清单" in text, "两层资产清单附录缺失"
        assert "droid-review-watchdog.yml" in text, "附录缺自愈层 watchdog 资产"
        assert "evolution-scan.yml" in text, "附录缺自进化层扫描资产"
        assert "17,47 * * * *" in text, "附录缺自进化层自扫定时面 cron"
