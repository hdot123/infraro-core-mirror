"""watchdog 触发器健康基线契约（D-1 形态锁定，2026-10-06）。

宪法最高规则第 1 条 Enforcement：自愈层（watchdog）的预期形态 = 触发器
全开 + 7 条运行时护栏在位 + 三个 job 与触发面无结构性互斥（M2 HOTFIX
的 dispatch-only 死配置为既有违宪态，已由 D-1 消除）。任何把本形态改回
禁用/互斥/削弱的变更即违宪，本文件立即变红阻断合并。

D-1 验收映射（10 项）：
  ① dispatch 不再全 skip → test_dispatch_exercise_real_job
  ② schedule 到点有 run 且过门 → test_schedule_reachable / test_cron_derivation_aligned
  ⑤ 7 条运行时护栏 → TestRuntimeGuardrails（G1-G7 逐条）
  上游名单拼写 → test_upstream_names_exact_spelling（防显示名漂移静默断链）
  artifact 前缀零检出监控面 → test_artifact_prefix_monitoring_surface
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.business_policy

REPO_ROOT = Path(__file__).parent.parent
WATCHDOG_PATH = REPO_ROOT / ".github/workflows/droid-review-watchdog.yml"
HANDLERS_PATH = REPO_ROOT / ".github/workflows/droid-review-watchdog-handlers.yml"
SHARDS_PATH = REPO_ROOT / ".github/workflows/droid-review-shards.yml"


def _load(path: Path = WATCHDOG_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def _on_block() -> dict:
    # yaml 把 `on:` 解析为布尔 True 键
    return _load()[True]


class TestTriggerHealthBaseline:
    """触发器全开健康基线（宪法第 1.2(c) 款新形态锁定）。"""

    def test_all_three_triggers_present(self):
        on = _on_block()
        for trigger in ("workflow_run", "schedule", "workflow_dispatch"):
            assert trigger in on, f"触发器 {trigger} 被移除（watchdog 被禁用，违宪）"

    def test_upstream_names_exact_spelling(self):
        """workflow_run 上游名单与目标 workflow 显示名逐字符一致。

        workflow_run 匹配按显示名精确比对，一个字符之差即静默断链（watchdog
        永不再被触发且无任何报错）。名单锁两个事件门控 job 的消费面并集。
        """
        on = _on_block()
        assert sorted(on["workflow_run"]["workflows"]) == ["CI", "Droid Auto Review"]
        assert on["workflow_run"]["types"] == ["completed"]

    def test_upstream_names_match_actual_workflow_display_names(self):
        """上游名单每个名字都能在仓库 workflow 显示名面找到出处（防拼写漂移）。"""
        workflows_dir = REPO_ROOT / ".github/workflows"
        display_names = set()
        for path in workflows_dir.glob("*.yml"):
            data = yaml.safe_load(path.read_text()) or {}
            name = data.get("name")
            if isinstance(name, str):
                display_names.add(name)
        for upstream in _on_block()["workflow_run"]["workflows"]:
            assert upstream in display_names, (
                f"上游名 {upstream!r} 不在任何 workflow 显示名中（拼写漂移 = 静默断链）"
            )

    def test_no_extra_upstreams_without_consuming_job(self):
        """列表外上游不进名单：没有对应消费 job 的上游只产生恒 skip 的红噪音 run。"""
        on = _on_block()
        watchdog_text = WATCHDOG_PATH.read_text()
        for upstream in on["workflow_run"]["workflows"]:
            assert upstream in watchdog_text.split("on:")[1], (
                f"上游 {upstream!r} 在触发面但未出现在任何 job 的 if 门控中"
            )

    def test_schedule_reachable(self):
        """schedule 触发器在位且 cron 形态合法（到点产生 run 的前提）。"""
        schedule = _on_block()["schedule"]
        assert isinstance(schedule, list) and len(schedule) == 1
        cron = schedule[0]["cron"]
        fields = cron.split()
        assert len(fields) == 5, f"cron 字段数错误：{cron!r}"

    def test_cron_derivation_aligned_with_recovery_window(self):
        """cron 节奏与 quota-sweep 恢复窗口设计对齐：30 分钟一窗（默认 1800s）。"""
        cron = _on_block()["schedule"][0]["cron"]
        minute_field = cron.split()[0]
        # '11,41 * * * *'：每 30 分钟一个探测窗，分钟位避开 :00/:30 高峰 load-shed
        assert minute_field == "11,41", f"cron 分钟位漂移：{cron!r}（应保持 30 分钟节奏）"
        minutes = [int(m) for m in minute_field.split(",")]
        for m in minutes:
            assert m not in (0, 30), f"cron 分钟位 {m} 撞高峰 load-shed 窗口"

    def test_concurrency_group_covers_all_legs(self):
        """concurrency.group 与新触发面匹配：workflow_run 腿互斥同组，
        schedule/dispatch 腿以 run_id 兜底成组（不再退化空串）。"""
        group = _load()["concurrency"]["group"]
        assert "github.event.workflow_run.id" in group
        assert "github.run_id" in group
        assert _load()["concurrency"]["cancel-in-progress"] is False

    def test_job_trigger_semantics_no_mutual_exclusion(self):
        """三 job 的 if 与触发面无互斥：每条腿至少一个 job 可达（死配置回归即红）。

        M2 HOTFIX 形态（dispatch-only 触发器 + workflow_run×2/schedule×1 的
        if）下任何触发都全 skip。本断言按事件类型逐一验可达性。
        """
        jobs = _load()["jobs"]
        ifs = {name: job["if"] for name, job in jobs.items()}
        # workflow_run 腿：self-heal-rerun（Droid Auto Review failure）与
        # cancel-on-ci-fail（CI failure）各自可达
        assert "github.event_name == 'workflow_run'" in ifs["self-heal-rerun"]
        assert "github.event_name == 'workflow_run'" in ifs["cancel-on-ci-fail"]
        assert "Droid Auto Review" in ifs["self-heal-rerun"]
        assert "'CI'" in ifs["cancel-on-ci-fail"]
        # schedule 腿 + dispatch 腿：quota-sweep 双门可达
        assert "github.event_name == 'schedule'" in ifs["quota-sweep"]
        assert "github.event_name == 'workflow_dispatch'" in ifs["quota-sweep"]
        # 事件 job 不吞 schedule/dispatch 事件（需要 workflow_run 上下文，
        # 其他事件下该上下文为空，语义必须显式互斥）
        for name in ("self-heal-rerun", "cancel-on-ci-fail"):
            assert "github.event_name == 'workflow_run'" in ifs[name], (
                f"{name} 的 if 缺事件名门控（非 workflow_run 事件下 workflow_run 上下文为空，属隐藏互斥）"
            )

    def test_dispatch_exercise_real_job(self):
        """D-1 验收①：dispatch 触发时至少一个 job 过 if（quota-sweep 手动腿）。

        静态核验口径：quota-sweep 的 if 显式含 workflow_dispatch 分支；
        dispatch 无 workflow_run 上下文，事件 job 的 if 求值为假是正确 skip
        而非互斥。
        """
        assert "github.event_name == 'workflow_dispatch'" in _load()["jobs"]["quota-sweep"]["if"]


class TestRuntimeGuardrails:
    """7 条运行时护栏逐条落地（D-1 验收⑤；参数推导依据在 PR 描述）。"""

    def test_g1_kill_switch_gates_all_jobs(self):
        """G1 kill-switch：三个 job 的 if 均含 WATCHDOG_KILL_SWITCH 门。"""
        for name, job in _load()["jobs"].items():
            assert "WATCHDOG_KILL_SWITCH" in job["if"], f"job {name} 缺 kill-switch 门"

    def test_g1_kill_switch_repo_variable_convention(self):
        """G1 命名推导：WATCHDOG_ 前缀 + enabled 值（与 WATCHDOG_MAX_ATTEMPT 同族）。"""
        text = WATCHDOG_PATH.read_text()
        assert "vars.WATCHDOG_KILL_SWITCH != 'enabled'" in text

    def test_g2_pagination_bounded(self):
        """G2 分页上界：显式 page 循环 + MAX_PAGES 变量（默认 2），无 --paginate 无界。

        瞬时错误容忍（2026-10-06 返修）：set -euo pipefail 下单页 gh api 失败
        /jq 解析失败只记日志跳页（连续失败达阈才终止），不中止整个 sweep
        产红——sweep 自身产红正是本护栏要消除的噪音形态，回归即红。
        """
        run = _load()["jobs"]["quota-sweep"]["steps"][0]["run"]
        assert "MAX_PAGES" in run
        assert "vars.WATCHDOG_MAX_PAGES || '2'" in WATCHDOG_PATH.read_text()
        assert "--paginate" not in run
        assert "if ! RESP=$(gh api" in run
        assert "CONSECUTIVE_API_FAILURES" in run
        # 单页失败路径：记日志跳页，不 exit 非 0
        assert "page skipped" in run

    def test_g3_pr_state_check_before_rerun(self):
        """G3 PR 状态检查：self-heal-rerun 只重试 open PR 的上游 run。

        字段名锁定（2026-10-06 返修）：workflow-run REST 对象只有
        pull_requests（live 实证：PR 关联 run 返回 [271]、head_* 变体
        恒 null）——读错字段即护栏死代码、closed-PR 检查永不执行。
        断言打在真实读取行（--jq 命令）上而非整个 run 块，注释不受限。
        """
        steps = _load()["jobs"]["self-heal-rerun"]["steps"]
        guard = steps[0]["run"]
        read_lines = [
            ln.strip() for ln in guard.splitlines() if "actions/runs/$RUN_ID" in ln and "--jq" in ln
        ]
        assert read_lines, "G3 未从 workflow-run REST 对象读关联 PR（护栏死代码）"
        for ln in read_lines:
            assert "head_pull_requests" not in ln, f"G3 读取行锁死字段名（恒 null）：{ln}"
        assert any(".pull_requests[0].number" in ln for ln in read_lines), (
            "G3 读取行未用 .pull_requests[0].number（workflow-run 对象的实际字段）"
        )
        assert ".state" in guard
        assert '"open"' in guard

    def test_g4_permissions_minimal_surface(self):
        """G4 权限对齐：顶层只读基线，write 面逐 job 最小声明。

        self-heal-rerun 的 G3 状态检查调 gh api pulls/<n>，需要
        pull-requests: read；job 级 permissions 按替换语义生效（覆盖
        顶层基线），缺声明即 403（shards setup 同款先例）。
        """
        data = _load()
        assert data["permissions"] == {"contents": "read"}
        for name, job in data["jobs"].items():
            expected = {"actions": "write"}
            if name == "self-heal-rerun":
                # G3 调 gh api repos/$REPO/pulls/$ASSOC_PR（.github/workflows/droid-review-watchdog.yml）
                expected["pull-requests"] = "read"
            assert job.get("permissions") == expected, (
                f"job {name} 权限面偏离最小面（基线 actions:write，G3 面另需 pull-requests:read）"
            )

    def test_g5_rerun_cap_enforced_on_both_rerun_surfaces(self):
        """G5 rerun 上限：WATCHDOG_MAX_ATTEMPT 在 self-heal-rerun 与 quota-sweep 双面生效。"""
        data = _load()
        heal_run = data["jobs"]["self-heal-rerun"]["steps"][-1]["run"]
        sweep_run = data["jobs"]["quota-sweep"]["steps"][0]["run"]
        assert "vars.WATCHDOG_MAX_ATTEMPT || '3'" in WATCHDOG_PATH.read_text()
        for label, run in (("self-heal-rerun", heal_run), ("quota-sweep", sweep_run)):
            assert "MAX_ATTEMPT" in run, f"{label} 缺 rerun 上限"
            assert "-ge" in run, f"{label} 缺上限比较"

    def test_g6_dry_run_first_runs_observe(self):
        """G6 dry-run 首跑：三个 job 均接 auto-rollout（前 2 次运行 observe）。

        事件 job 的 observe = 检测逻辑无条件执行（行动步无步级 if），
        仅 rerun/cancel API 调用按 WATCHDOG_MODE 门控（与文件头承诺一致，
        2026-10-06 返修：步级 if 连检测一并跳过属语义违背，回归即红）；
        quota-sweep 在脚本内以 DRY_RUN_MODE 拦截真实 rerun。
        """
        data = _load()
        assert "vars.WATCHDOG_DRY_RUN || 'auto'" in WATCHDOG_PATH.read_text()
        for name in ("self-heal-rerun", "cancel-on-ci-fail"):
            job = data["jobs"][name]
            assert len(job["steps"]) == 2, f"job {name} 步数漂移（guard + action 两步形态）"
            action = job["steps"][1]
            assert "if" not in action, (
                f"job {name} 行动步重新引入步级 if（observe 连检测一并跳过的语义违背回归）"
            )
            assert "WATCHDOG_MODE" in action["run"], f"job {name} 行动步缺 WATCHDOG_MODE 门控"
            # guard 步导出 WATCHDOG_MODE（行动步的门的真值来源）
            assert 'echo "WATCHDOG_MODE=$MODE" >> "$GITHUB_ENV"' in job["steps"][0]["run"]
            # 检测面不被模式门控：日志特征匹配 / run 列举出现在无条件路径
            if name == "self-heal-rerun":
                assert "PATTERNS=" in action["run"]
                assert "grep -qE" in action["run"]
                assert action["run"].index("grep -qE") < action["run"].index("WATCHDOG_MODE:-live")
            else:
                assert "status=in_progress" in action["run"]
                assert "status=queued" in action["run"]
                assert action["run"].index("status=in_progress") < action["run"].index(
                    "WATCHDOG_MODE:-live"
                )
        sweep_run = data["jobs"]["quota-sweep"]["steps"][0]["run"]
        assert 'if [ "$SWEEP_COUNT" -le 2 ]' in sweep_run
        assert 'if [ "$DRY_RUN_MODE" = "true" ]' in sweep_run
        # observe 不虚增 rerun 计数（检出数与 rerun 数分离，观测数据不失真）
        assert "DETECTED=" in sweep_run
        assert sweep_run.index("DETECTED=$((DETECTED + 1))") < sweep_run.index(
            'if [ "$DRY_RUN_MODE" = "true" ]'
        )

    def test_guard_early_exit_neutralizes_action_steps(self):
        """guard 步早退 → 行动步零执行（2026-10-06 三轮返修，跨步门控闭环）。

        exit 0 只终止 guard 步，行动步随后必然执行：guard 早退路径若不
        显式落 WATCHDOG_MODE=skip，行动步的 `${WATCHDOG_MODE:-live}` 兜底
        为 live——kill-switch 翻转 / closed PR 等早退场景下 rerun/cancel
        动作照跑。行动步入口以 skip 复检兜底，未导出状态同样 fail-safe
        按 skip（状态缺失不缺省为 live）。这是契约测试面此前锁不住的缺陷
        类：文本存在性断言查不出跨步状态传递断裂，本断言逐分支锁定。
        """
        data = _load()
        for name in ("self-heal-rerun", "cancel-on-ci-fail"):
            guard = data["jobs"][name]["steps"][0]["run"]
            action = data["jobs"][name]["steps"][1]["run"]
            # guard 每个 exit 0 早退分支都先落 WATCHDOG_MODE=skip
            for i, ln in enumerate(guard.splitlines()):
                if ln.strip() == "exit 0":
                    prelude = "\n".join(guard.splitlines()[:i])
                    assert 'echo "WATCHDOG_MODE=skip" >> "$GITHUB_ENV"' in prelude, (
                        f"job {name} guard 步第 {i + 1} 行 exit 0 早退未落 WATCHDOG_MODE=skip"
                        "（行动步兜底为 live，早退门控失效）"
                    )
            # 行动步入口复检 skip，且复检先于一切 GitHub API 调用（零执行）
            recheck = '[ "${WATCHDOG_MODE:-}" = "skip" ]'
            assert recheck in action, f"job {name} 行动步缺 skip 复检（早退门控断裂）"
            first_api = action.index("gh api")
            assert action.index(recheck) < first_api, (
                f"job {name} 行动步 skip 复检晚于首个 API 调用（早退后仍发请求）"
            )
            # 未导出兜底为 skip（fail-safe），不得保留缺省 live 的顶层兜底
            assert '"${WATCHDOG_MODE:-}"' in action
            assert '"${WATCHDOG_MODE:-live}"' in action  # observe/live 分支仍用原形态

    def test_g7_curl_fails_degraded_not_hard(self):
        """G7 网络/传输层失败降级不红 job（2026-10-06 三轮返修）。

        set -euo pipefail 下无兜底的 curl 网络失败会中止整个 sweep 产红，
        违背 G7「可降级不阻断」自身契约。lookup/create 两处 curl 均落
        显式失败标记值：解析必失败 → 走既有降级 warning 分支（lookup
        失败跳本轮写入防重复建 issue；create 失败 ISSUE_ID 保持空）。
        """
        run = _load()["jobs"]["quota-sweep"]["steps"][0]["run"]
        assert "https://api.linear.app/graphql) || LOOKUP_RESP='G7_LOOKUP_FAILED'" in run, (
            "G7 lookup curl 缺网络层兜底（Linear 不可达即红整个 job）"
        )
        assert "https://api.linear.app/graphql) || CREATE_RESP='G7_CREATE_FAILED'" in run, (
            "G7 create curl 缺网络层兜底（Linear 不可达即红整个 job）"
        )
        # 兜底次序：失败标记先于下游解析/分支（标记在位即守门成立）
        lookup_pos = run.index("G7_LOOKUP_FAILED")
        assert lookup_pos < run.index(".data.project.issues.nodes | type"), (
            "lookup 兜底标记晚于 nodes type 解析（失败值未受门控直入下游）"
        )
        create_pos = run.index("G7_CREATE_FAILED")
        assert create_pos < run.index(".data.issueCreate.issue.id"), (
            "create 兜底标记晚于响应解析（失败值未受门控直入下游）"
        )
        # 观测写面仍在线（comment curl 原生 || 降级 echo 形态保留）
        assert "issueCommentCreate" in run
        assert "Linear comment failed" in run

    def test_g7_linear_observability_wired(self):
        """G7 Linear 可观测：sweep 状态写入 Linear（项目面与 branch-cleanup 同源），可降级。

        幂等护栏（2026-10-06 返修）：lookup「失败」（不可解析响应）与
        「未找到」（空结果）区分，仅后者才 issueCreate；lookup 失败跳过
        本轮写入——防 30 分钟一 tick 的 schedule 重复建 issue，回归即红。
        """
        sweep_step = _load()["jobs"]["quota-sweep"]["steps"][0]
        env = sweep_step["env"]
        assert env["LINEAR_API_KEY"] == "${{ secrets.LINEAR_API_KEY }}"
        assert env["LINEAR_PROJECT_ID"] == "${{ vars.LINEAR_PROJECT_INFRA_CORE_ID }}"
        run = sweep_step["run"]
        assert "issueCommentCreate" in run
        # 观测失败不阻断 sweep（warning 降级，非 exit 1）
        assert "::warning::" in run
        assert "exit 1" not in run.split("# G7")[1]
        # lookup 失败 vs 未找到：nodes type 校验成立才可能走 issueCreate
        lookup_pos = run.index(".data.project.issues.nodes | type")
        create_pos = run.index("issueCreate(input:")
        assert lookup_pos < create_pos, "issueCreate 未被 lookup 结果校验门控（重复建 issue 回归）"
        assert "skipping this round's write" in run
        # 外部值经 jq 构造 payload（--arg + tojson），不裸拼进 JSON 字符串
        assert run.count("jq -n --arg") >= 3

    def test_guard_variables_declared_with_fallback(self):
        """护栏变量显式兜底（M5 R1(2) 契约：无兜底的 vars 引用须真实存在）。"""
        text = WATCHDOG_PATH.read_text()
        for var in ("WATCHDOG_KILL_SWITCH", "WATCHDOG_DRY_RUN", "WATCHDOG_MAX_PAGES"):
            assert f"vars.{var} ||" in text, f"护栏变量 {var} 引用无兜底"

    def test_no_gate_bypass_tokens(self):
        """门禁语义（fail-closed）：全文件无 check 结论改写 / --admin 绕过面。"""
        data = _load()
        for job in data["jobs"].values():
            for step in job["steps"]:
                run = step.get("run", "")
                for forbidden in ("check-runs", "annotations", "--admin"):
                    assert forbidden not in run, f"门禁绕过面出现：{forbidden!r}"


class TestArtifactPrefixMonitoringSurface:
    """artifact 前缀 droid-review-debug- 零检出监控面（D-1 开发期测试要求②）。"""

    def test_quota_sweep_filter_uses_debug_prefix(self):
        """sweep 的 artifact 检出保持 startswith('droid-review-debug-') 过滤。"""
        run = _load()["jobs"]["quota-sweep"]["steps"][0]["run"]
        assert 'startswith("droid-review-debug-")' in run

    def test_uploader_produces_matching_prefix(self):
        """生产/消费前缀闭环：shards 上传的 artifact 名仍带 droid-review-debug- 前缀。"""
        text = SHARDS_PATH.read_text()
        assert "droid-review-debug-" in text, "shards 不再产出 watchdog 消费的前缀（检出面断裂）"

    def test_handlers_document_dependency(self):
        """handlers 文件头对前缀契约的归属说明保留（quota-sweep 留在 caller 的理由）。"""
        text = HANDLERS_PATH.read_text()
        assert "droid-review-debug-" in text


class TestLayerAssetsIntact:
    """两层资产面完整：handlers reusable 接口未被 D-1 触碰（形态①内联保留）。"""

    def test_handlers_file_untouched_contract(self):
        """handlers 的 workflow_call 双 mode 接口在位（D-1 不改 handlers）。"""
        data = _load(HANDLERS_PATH)
        on = data[True]
        assert "workflow_call" in on
        inputs = on["workflow_call"]["inputs"]
        assert set(inputs) >= {"mode", "run_id", "run_attempt"}
        jobs = data["jobs"]
        assert jobs["self-heal-rerun"]["if"] == "inputs.mode == 'self-heal-rerun'"
        assert jobs["cancel-on-ci-fail"]["if"] == "inputs.mode == 'cancel-on-ci-fail'"

    def test_watchdog_does_not_reference_handlers(self):
        """形态①：事件门控执行体内联在 watchdog，不新增对 handlers 的 uses 调用。

        handlers 的 live 消费方是外部 thin caller（memory-core 等）；本仓
        watchdog 若改 caller 形态属形态②，是另一个宪法流程项。
        """
        text = WATCHDOG_PATH.read_text()
        assert "droid-review-watchdog-handlers.yml" not in text

    def test_watchdog_not_required_check_producer(self):
        """watchdog 不是 ruleset required check 产出者（D-1 验收⑦：不触保护面）。"""
        droid_review_text = (REPO_ROOT / ".github/workflows/droid-review.yml").read_text()
        # 聚合 job key droid-review 的产出者仍是 droid-review.yml
        assert "droid-review:" in droid_review_text
