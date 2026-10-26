"""Droid Task Executor 契约测试（R1 mission — VAL-EX-001 ~ VAL-EX-007）。

覆盖 unified-executor feature 的全部验证断言：
- VAL-EX-001: droid-task.yml 触发类型/runs-on/timeout
- VAL-EX-002: 二级并发护栏（workflow 级 team 组 + job 级 task 组，cancel-in-progress: false）
- VAL-EX-003: composite setup-byom-go-github 存在且 BYOM-only（factory_credits 零依赖）
- VAL-EX-004: prompt 模板移植完整（error-gateway Issue-first + linear-gateway Fixes REF）
- VAL-EX-005: if:failure() 兜底建 Issue
- VAL-EX-006: dry-run 闭环（由 gh workflow run 验证，本文件锁定结构前提）
- VAL-EX-007: 回归绿（本测试文件本身是回归基线的一部分）
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "droid-task.yml"
COMPOSITE_DIR = REPO_ROOT / ".github" / "actions" / "setup-byom-go-github"
COMPOSITE = COMPOSITE_DIR / "action.yml"


def _load(path: Path) -> dict:
    """Load YAML, handling the on: → True pitfall."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must parse to a mapping"
    return data


def _triggers(data: dict) -> dict:
    """YAML 1.1 parses bare 'on' as boolean True."""
    return data.get("on") or data.get(True) or {}


def _steps_by_name(steps: list[dict]) -> dict[str, dict]:
    """Index steps by name for easy lookup."""
    return {s.get("name", ""): s for s in steps}


# ── VAL-EX-001: 触发类型/runs-on/timeout ──────────────────────────────


class TestTriggerAndRunSurface:
    """VAL-EX-001: droid-task.yml 触发与运行面正确。"""

    def test_trigger_is_repository_dispatch_droid_task(self):
        """on: repository_dispatch: types: [droid-task]"""
        triggers = _triggers(_load(WORKFLOW))
        assert "repository_dispatch" in triggers, (
            "droid-task.yml must trigger on repository_dispatch"
        )
        rd = triggers["repository_dispatch"]
        assert rd is not None
        types = rd.get("types", [])
        assert "droid-task" in types, (
            f"repository_dispatch types must include 'droid-task', got {types}"
        )

    def test_runs_on_self_hosted_pve_linux(self):
        """runs-on: [pve-linux]（2026-09-25 所有者裁定：私有仓一律自建）"""
        data = _load(WORKFLOW)
        job = data["jobs"]["execute"]
        assert job["runs-on"] == ["pve-linux"], (
            f"runs-on must be the private self-hosted pool, got {job['runs-on']}"
        )

    def test_timeout_minutes_60(self):
        """timeout-minutes: 60"""
        data = _load(WORKFLOW)
        job = data["jobs"]["execute"]
        assert job["timeout-minutes"] == 60, (
            f"timeout-minutes must be 60, got {job['timeout-minutes']}"
        )


# ── VAL-EX-002: 并发护栏 ──────────────────────────────────────────────


class TestConcurrencyGuard:
    """VAL-EX-002: 二级并发护栏在位（R1 设计 §3.2：team 组 + task 组）。"""

    def test_workflow_concurrency_group_uses_team_key(self):
        """workflow 级 concurrency.group: droid-team-{team_key}（准入闸）"""
        data = _load(WORKFLOW)
        conc = data.get("concurrency")
        assert conc is not None, "droid-task.yml must have top-level concurrency"
        group = conc.get("group", "")
        assert "droid-team-" in group, (
            f"workflow concurrency group must contain 'droid-team-', got: {group}"
        )
        assert "team_key" in group, (
            f"workflow concurrency group must reference team_key, got: {group}"
        )

    def test_workflow_concurrency_team_group_has_fallback_chain(self):
        """team 组表达式含内联回退链（kind → default）：error-gateway payload 无 team_key。

        concurrency 表达式在步骤运行前求值，不能依赖 Validate 步骤补默认值，
        必须内联 `|| kind || 'default'` 回退链。
        """
        data = _load(WORKFLOW)
        group = data["concurrency"]["group"]
        assert "github.event.client_payload.kind" in group, (
            f"team group must fall back to payload kind for team-less kinds, got: {group}"
        )
        assert "'default'" in group, f"team group must have final 'default' fallback, got: {group}"

    def test_job_concurrency_group_uses_task_id(self):
        """job 级 concurrency.group: droid-task-{task_id}（单飞语义下沉到 execute job）"""
        data = _load(WORKFLOW)
        job = data["jobs"]["execute"]
        conc = job.get("concurrency")
        assert conc is not None, "execute job must have job-level concurrency (task 单飞)"
        group = conc.get("group", "")
        assert "droid-task-" in group, (
            f"job concurrency group must contain 'droid-task-', got: {group}"
        )
        assert "task_id" in group, f"job concurrency group must reference task_id, got: {group}"

    def test_cancel_in_progress_is_false(self):
        """cancel-in-progress: false（workflow 级与 job 级均不取消正在运行的同组任务）"""
        data = _load(WORKFLOW)
        conc = data["concurrency"]
        assert conc.get("cancel-in-progress") is False, (
            f"workflow cancel-in-progress must be false, got: {conc.get('cancel-in-progress')}"
        )
        job_conc = data["jobs"]["execute"]["concurrency"]
        assert job_conc.get("cancel-in-progress") is False, (
            f"job cancel-in-progress must be false, got: {job_conc.get('cancel-in-progress')}"
        )


# ── VAL-EX-003: composite BYOM-only ──────────────────────────────────


class TestCompositeByomOnly:
    """VAL-EX-003: composite setup-byom-go-github 存在且 BYOM-only（factory_credits 零依赖）。

    2026-10-04 迁移：BAILIAN 已退役，droid-task 执行腿换 setup-byom-go-github
    （与 droid-review 同款 CF AI Gateway go-github 链路）。droid CLI 安装不再由
    composite 承担，上移至 workflow「Install Droid CLI」步骤（droid-review 同款
    宿主候选择优 D1-c 逻辑）。
    """

    def test_composite_action_file_exists(self):
        """.github/actions/setup-byom-go-github/action.yml 存在"""
        assert COMPOSITE.exists(), f"Composite action must exist at {COMPOSITE}"

    def test_composite_uses_inputs_not_secrets(self):
        """composite 内不得直接引用 secrets.*，须经 inputs"""
        raw = COMPOSITE.read_text(encoding="utf-8")
        # 排除注释行后检查 secrets. 引用
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 跳过行内注释部分
            code_part = line.split("#")[0] if "#" in line else line
            assert "secrets." not in code_part, (
                f"composite must not reference secrets.* directly (use inputs instead): {line.strip()}"
            )

    def test_composite_declares_byom_inputs(self):
        """inputs.go-github-run-token 声明（CF 路由模式：opencode key 退出注入面）"""
        data = _load(COMPOSITE)
        inputs = data.get("inputs", {})
        assert "opencode-go-key" not in inputs, (
            "CF 路由模式（2026-10-18）：CI 不携带 opencode key（Worker 剥离客户端 Authorization）"
        )
        assert "go-github-run-token" in inputs, "composite must declare go-github-run-token input"
        assert inputs["go-github-run-token"].get("required") is True

    def test_composite_is_composite_type(self):
        """runs.using: composite"""
        data = _load(COMPOSITE)
        assert data["runs"]["using"] == "composite"

    def test_composite_no_factory_credits_dependency(self):
        """VAL-EX-003: composite 无 factory_credits / FACTORY_API_KEY 依赖。

        settings.json 写入逻辑（python 段）不得含 factoryCredits 键，
        整个文件不得引用 FACTORY_API_KEY 环境变量。
        """
        raw = COMPOSITE.read_text(encoding="utf-8")
        assert "FACTORY_API_KEY" not in raw, (
            "composite must not reference FACTORY_API_KEY env var (禁 factory_credits 铁律)"
        )
        assert "factoryCredits" not in raw, "composite must never write factoryCredits (BYOM-only)"

    def test_composite_writes_settings_json_with_custom_models(self):
        """settings.json 写入含 customModels，指向 go-github 网关入口域名"""
        raw = COMPOSITE.read_text(encoding="utf-8")
        assert "customModels" in raw, "composite must write customModels to settings.json"
        assert "gh.lumivane.dpdns.org" in raw, "composite must point to the go-gateway entry domain"

    def test_composite_model_is_dynamic_droid_review(self):
        """模型供给 = dynamic/droid-review（peak/off-peak 动态路由）"""
        raw = COMPOSITE.read_text(encoding="utf-8")
        assert "dynamic/droid-review" in raw, "composite must use dynamic/droid-review model supply"

    def test_composite_no_bailian_residue(self):
        """退役裁定（fz-r5-drift-addendum §1）：composite 不得再引用 Bailian 端点"""
        raw = COMPOSITE.read_text(encoding="utf-8")
        assert "dashscope" not in raw and "ailian" not in raw, (
            "composite must not reference Bailian/dashscope endpoints (BAILIAN retired)"
        )

    def test_workflow_uses_composite_action(self):
        """droid-task.yml 使用 ./.github/actions/setup-byom-go-github"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        setup_steps = [
            s
            for s in steps
            if s.get("uses", "").startswith("./.github/actions/setup-byom-go-github")
        ]
        assert len(setup_steps) == 1, (
            f"workflow must use setup-byom-go-github composite exactly once, found {len(setup_steps)}"
        )
        # CF 路由模式（2026-10-18）：只传网关 run token，opencode key 不再注入
        setup_step = setup_steps[0]
        with_block = setup_step.get("with", {})
        assert "opencode-go-key" not in with_block, (
            "CF 路由模式：CI 不携带 opencode key（上游认证由 CF 端 provider 存储 credential 承担）"
        )
        assert "go-github-run-token" in with_block, (
            "setup-byom-go-github must receive go-github-run-token input"
        )

    def test_workflow_has_droid_cli_install_step(self):
        """droid CLI 安装在 workflow 步骤（迁移后不再由 composite 承担）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        install = steps.get("Install Droid CLI", {})
        run = install.get("run", "")
        assert "app.factory.ai/cli" in run, "install step must use official download source"
        assert "droid --version" not in run or "BEST_PATH" in run, (
            "install step must resolve and verify the effective droid binary"
        )

    def test_workflow_install_before_setup_byom(self):
        """安装先于 BYOM settings 写入（Execute 前二进制+凭证须均就绪）"""
        data = _load(WORKFLOW)
        names = [s.get("name", "") for s in data["jobs"]["execute"]["steps"]]
        assert names.index("Install Droid CLI") < names.index(
            "Setup BYOM (go-github / DeepSeek V4.1 Flash)"
        )


# ── VAL-EX-004: prompt 模板移植完整 ───────────────────────────────────


class TestPromptTemplates:
    """VAL-EX-004: prompt 模板移植完整。"""

    def test_error_gateway_prompt_step_exists(self):
        """error-gateway prompt 步骤存在"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert "Build prompt (error-gateway)" in steps

    def test_error_gateway_prompt_has_issue_first_hard_gate(self):
        """error-gateway prompt 含 Issue-first 硬门禁关键词"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Build prompt (error-gateway)"]
        run = step["run"]
        # Issue-first 硬门禁关键句
        assert "Issue-first" in run or "Issue-first" in run, (
            "error-gateway prompt must contain Issue-first hard gate"
        )
        assert "posthog-error-sync" in run, (
            "error-gateway prompt must reference posthog-error-sync label"
        )
        assert "--state all" in run, (
            "error-gateway prompt must use --state all for idempotent check"
        )
        assert "Closes #" in run or "Fixes #" in run, (
            "error-gateway prompt must require Closes/Fixes #<issue-number>"
        )

    def test_error_gateway_prompt_condition(self):
        """error-gateway prompt 步骤条件正确"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Build prompt (error-gateway)"]
        condition = step.get("if", "")
        assert "error-gateway" in condition, (
            f"error-gateway prompt condition must reference error-gateway, got: {condition}"
        )

    def test_linear_gateway_prompt_step_exists(self):
        """linear-gateway prompt 步骤存在"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert "Build prompt (linear-gateway)" in steps

    def test_linear_gateway_prompt_has_fixes_ref(self):
        """linear-gateway prompt 含 Fixes REF 回写契约"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Build prompt (linear-gateway)"]
        run = step["run"]
        assert "Fixes" in run, "linear-gateway prompt must contain Fixes REF contract"
        assert "P_IDENTIFIER" in run, "linear-gateway prompt must reference issue identifier"

    def test_linear_gateway_prompt_has_gate_a_idempotency(self):
        """linear-gateway prompt 含 Gate A 幂等性检查"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Build prompt (linear-gateway)"]
        run = step["run"]
        assert "Gate A" in run or "幂等" in run or "completed" in run, (
            "linear-gateway prompt must include Gate A idempotency check"
        )

    def test_linear_gateway_prompt_condition(self):
        """linear-gateway prompt 步骤条件正确"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Build prompt (linear-gateway)"]
        condition = step.get("if", "")
        assert "linear-gateway" in condition, (
            f"linear-gateway prompt condition must reference linear-gateway, got: {condition}"
        )

    def test_prompt_uses_env_vars_not_inline_expressions(self):
        """prompt 步骤通过 env: 映射传值（不直接在 run 内用 ${{ }} 展开 payload）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        for name in ("Build prompt (error-gateway)", "Build prompt (linear-gateway)"):
            step = steps[name]
            env = step.get("env", {})
            assert len(env) > 0, f"{name} must use env: mapping to pass payload values"


# ── VAL-EX-005: 失败兜底 ──────────────────────────────────────────────


class TestFailureFallback:
    """VAL-EX-005: if:failure() 建 Issue 兜底在位。"""

    def test_failure_fallback_step_exists(self):
        """Failure fallback 步骤存在"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        # 找到包含 failure fallback 的步骤
        fallback_steps = {
            k: v for k, v in steps.items() if "failure" in k.lower() or "fallback" in k.lower()
        }
        assert len(fallback_steps) >= 1, (
            f"workflow must have a failure fallback step, found steps: {list(steps.keys())}"
        )

    def test_failure_fallback_has_if_failure_condition(self):
        """failure fallback 步骤有 if: failure() 条件"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        assert len(fallback) >= 1
        step = fallback[0]
        condition = step.get("if", "")
        assert "failure()" in condition, (
            f"failure fallback must have if: failure() condition, got: {condition}"
        )

    def test_failure_fallback_creates_issue(self):
        """failure fallback 步骤使用 gh issue create"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        step = fallback[0]
        run = step["run"]
        assert "gh issue create" in run, "failure fallback must create GitHub Issue via gh CLI"

    def test_failure_fallback_uses_infra_failed_label(self):
        """failure fallback 创建的 Issue 带 infra-failed label（不污染 needs-triage 人工分诊队列）"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        step = fallback[0]
        run = step["run"]
        assert "infra-failed" in run, "failure fallback Issue must have infra-failed label"
        create_section = run[run.index("gh issue create") :]
        assert "--label" in create_section and "infra-failed" in create_section, (
            "gh issue create must pass --label infra-failed"
        )
        assert '--label "needs-triage"' not in run and "--label needs-triage" not in run, (
            "failure fallback must NOT label issues needs-triage (reserved for human triage queue)"
        )

    def test_failure_fallback_idempotent_search_matches_title_phrase(self):
        """幂等搜索串与创建标题的中文指纹短语一致（历史英文搜索串从未命中）。

        事故证据：INFRA-913 兜底 Issue 被重复创建 3 次（#1150/#1155/#1169），
        因搜索串 "droid-task failed for ..." 与标题「droid-task 执行失败: ...」不匹配。
        """
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        step = fallback[0]
        run = step["run"]
        assert "gh issue list" in run, (
            "failure fallback must check for existing Issues before creating (idempotent)"
        )
        assert "--state all" in run, "failure fallback idempotent check must use --state all"
        code_lines = [
            ln for ln in run.splitlines() if ln.strip() and not ln.strip().startswith("#")
        ]
        search_lines = [ln for ln in code_lines if "--search" in ln]
        title_lines = [ln for ln in code_lines if "--title" in ln]
        assert len(search_lines) == 1 and len(title_lines) == 1, (
            "fallback must have exactly one idempotent search and one create title"
        )
        # 搜索串与标题共用同一中文指纹短语 + task_id（保证能命中）
        for ln in (search_lines[0], title_lines[0]):
            assert "droid-task 执行失败" in ln, (
                f"search/title must share the Chinese phrase, got: {ln}"
            )
            assert "$P_TASK_ID" in ln, f"search/title must include task_id, got: {ln}"
        # 旧英文搜索串不得残留在可执行代码中（注释里的历史说明除外）
        assert all("droid-task failed for" not in ln for ln in code_lines), (
            "stale English search phrase must be removed from executable lines"
        )

    def test_failure_fallback_has_storm_frequency_cap(self):
        """频控：近 1h 同类失败 Issue ≥2 条时只评论不新建（防风暴繁殖）"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        step = fallback[0]
        run = step["run"]
        assert "RECENT_COUNT" in run and "-ge 2" in run, (
            "frequency cap must count recent infra-failed issues and trigger at >= 2"
        )
        assert "gh issue comment" in run, (
            "frequency cap branch must comment on the latest issue instead of creating"
        )
        # 频控分支必须短路退出（不落到 gh issue create）
        assert "exit 0" in run

    def test_failure_fallback_has_idempotent_check(self):
        """failure fallback 有幂等检查（避免重复创建 Issue）"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        step = fallback[0]
        run = step["run"]
        assert "gh issue list" in run, (
            "failure fallback must check for existing Issues before creating (idempotent)"
        )
        assert "--state all" in run, "failure fallback idempotent check must use --state all"


# ── INFRA-1034: Gate A 工作流级硬闸 + 兜底第二闸 ──────────────────────


class TestLinearGateAHardGate:
    """INFRA-1034 复盘：Gate A 从 prompt 指令上浮为工作流级硬闸。

    事故：INFRA-1033（Release 0.18.3）已完成（tag 先于 run 23 秒），droid 仍被
    拉起跑 8 分钟后模型端崩溃（num_turns=0），if:failure() 兜底盲目产出噪音
    镜像 Issue INFRA-1034。硬闸在拉起 droid 前查 Linear 终态短路。
    """

    GATE_NAME = "Pre-flight (Linear Gate A)"

    def test_preflight_step_exists(self):
        """Pre-flight (Linear Gate A) 步骤存在"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert self.GATE_NAME in steps, (
            f"workflow must have '{self.GATE_NAME}' step, found: {list(steps.keys())}"
        )

    def test_preflight_only_runs_for_linear_gateway(self):
        """硬闸仅对 linear-gateway 生效（error-gateway 无 Linear issue）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps[self.GATE_NAME]
        condition = step.get("if", "")
        assert "linear-gateway" in condition, (
            f"pre-flight gate must be scoped to linear-gateway, got: {condition}"
        )

    def test_preflight_queries_linear_state_type(self):
        """硬闸查询 Linear issue state.type（GraphQL，非 prompt 指令）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps[self.GATE_NAME]["run"]
        assert "api.linear.app/graphql" in run, "gate must call Linear GraphQL API directly"
        assert "state" in run and "type" in run, "gate must query state.type"
        assert "completed" in run, "gate must branch on completed state"

    def test_preflight_uses_linear_api_key_secret(self):
        """硬闸从 secrets.LINEAR_API_KEY 取凭证（缺失 warn 放行不阻塞）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        env = steps[self.GATE_NAME].get("env", {})
        assert env.get("LINEAR_API_KEY") == "${{ secrets.LINEAR_API_KEY }}", (
            f"gate must use LINEAR_API_KEY secret, got: {env.get('LINEAR_API_KEY')!r}"
        )
        run = steps[self.GATE_NAME]["run"]
        assert "::warning::" in run and "exit 0" in run, (
            "gate must warn-and-continue when secret missing (best-effort hardening)"
        )

    def test_preflight_fail_closed_on_unresolvable_state(self):
        """API 可达但解析不出 state.type → fail-closed（exit 1），不静默放行"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps[self.GATE_NAME]["run"]
        assert "::error::" in run, "gate must emit ::error:: when state unresolvable"

    def test_preflight_emits_skip_output(self):
        """completed → 输出 skip=true（供后续步骤条件短路）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps[self.GATE_NAME]["run"]
        assert "skip=true" in run and "GITHUB_OUTPUT" in run, (
            "gate must write skip=true to GITHUB_OUTPUT on completed state"
        )

    def test_execute_and_prompt_steps_gated_by_skip(self):
        """Execute droid 与 prompt 构建步骤都被 skip != 'true' 条件门控"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        for name in (
            "Checkout target repo",
            "Configure git identity",
            "Build prompt (error-gateway)",
            "Build prompt (linear-gateway)",
            "Execute droid",
        ):
            condition = steps[name].get("if", "")
            assert "skip != 'true'" in condition, (
                f"'{name}' must be gated on linear_gate skip output, got: {condition}"
            )

    def test_preflight_gate_id_has_step_id(self):
        """硬闸步骤声明 id: linear_gate（供 outputs 引用）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert steps[self.GATE_NAME].get("id") == "linear_gate", (
            "pre-flight gate must have id: linear_gate for step output references"
        )


class TestFailureFallbackSecondGate:
    """INFRA-1034 复盘：兜底第二闸——droid 失败后复查 Linear 终态。

    completed → 只评论既有 Issue 不新建（失败是完成态任务上的噪音，如模型端
    崩溃 num_turns=0）；复查失败 fail-open 降级为原兜底行为。
    """

    def test_fallback_reevaluates_linear_state_before_create(self):
        """兜底在建 Issue 前复查 Linear state.type"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        run = fallback[0]["run"]
        assert "api.linear.app/graphql" in run, (
            "fallback second gate must re-query Linear state before creating mirror issue"
        )
        assert "completed" in run, "second gate must branch on completed state"

    def test_fallback_second_gate_skips_create_and_comments(self):
        """completed 分支：不建 Issue，直接 exit 0 短路（无重复动作）"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        run = fallback[0]["run"]
        # 第二闸的 completed 分支必须在 gh issue create 之前短路
        create_pos = run.index("gh issue create")
        gate_branch = run[:create_pos]
        assert "exit 0" in gate_branch, "second-gate completed branch must short-circuit"

    def test_fallback_second_gate_scoped_to_linear_gateway(self):
        """第二闸仅对 linear-gateway kind 生效（error-gateway 无 Linear 状态可查）"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        run = fallback[0]["run"]
        assert '"$P_KIND" = "linear-gateway"' in run or 'P_KIND" = "linear-gateway' in run, (
            "second gate must be scoped to linear-gateway kind"
        )
        env = fallback[0].get("env", {})
        assert env.get("P_IDENTIFIER") == (
            "${{ github.event.client_payload.context.identifier }}"
        ), "fallback must receive P_IDENTIFIER for Linear recheck"
        assert env.get("LINEAR_API_KEY") == "${{ secrets.LINEAR_API_KEY }}", (
            "fallback must receive LINEAR_API_KEY for Linear recheck"
        )

    def test_fallback_second_gate_fail_open(self):
        """复查失败（网络/API）不拦截兜底：curl 失败 → 空响应 → 空状态 → 落回原行为"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        fallback = [
            s
            for s in steps
            if "failure" in s.get("name", "").lower() or "fallback" in s.get("name", "").lower()
        ]
        run = fallback[0]["run"]
        # 复查的 curl 必须容错（|| echo "" 而非 set -e 硬红）
        assert '2>/dev/null || echo ""' in run, (
            "second-gate recheck curl must tolerate failure (fail-open to original fallback)"
        )


# ── VAL-EX-006: dry-run 结构前提 ──────────────────────────────────────


class TestDryRunPrerequisites:
    """VAL-EX-006: dry-run 闭环的结构前提（实际 dry-run 由 gh workflow run 验证）。"""

    def test_workflow_has_execute_step(self):
        """workflow 有 droid exec 执行步骤"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert "Execute droid" in steps, "workflow must have 'Execute droid' step"

    def test_execute_step_uses_auto_high(self):
        """droid exec 使用 --auto high 模式"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Execute droid"]
        run = step["run"]
        assert "--auto high" in run, "droid exec must use --auto high mode"

    def test_execute_step_uses_tag_metadata(self):
        """droid exec 使用 --tag 传递元数据"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Execute droid"]
        run = step["run"]
        assert "--tag" in run, "droid exec must pass --tag metadata"

    def test_execute_step_injects_factory_api_key(self):
        """Execute droid 步骤 env 注入 FACTORY_API_KEY（CLI 身份认证层）。

        参照 droid-review.yml「Run shard review」同款形态：workflow step env
        直注 secrets.FACTORY_API_KEY。BYOM settings.json 只覆盖模型路由，
        不覆盖 CLI 登录门禁（2026-10-04 18:40Z run 实证：缺该 env 时
        droid exec 启动即 Authentication failed）。注：VAL-EX-003 的
        FACTORY_API_KEY 禁令仅约束 composite action 文件，workflow 不受限。
        """
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Execute droid"]
        env = step.get("env", {})
        assert env.get("FACTORY_API_KEY") == "${{ secrets.FACTORY_API_KEY }}", (
            "Execute droid step must inject FACTORY_API_KEY from secrets "
            f"(got: {env.get('FACTORY_API_KEY')!r})"
        )

    def test_workflow_permissions_include_contents_write(self):
        """2026-09-19 governance（VAL-CORE-004）：write 下放 job 级。

        顶层只留 contents:read 基线（INFRA-688 兜底）；contents:write 在
        execute job 级（checkout target repo / 推送修复分支兜底语义）。
        """
        data = _load(WORKFLOW)
        top_perms = data.get("permissions", {})
        assert top_perms.get("contents") == "read", (
            f"workflow top-level must be read baseline, got {top_perms}"
        )
        job_perms = data["jobs"]["execute"].get("permissions", {})
        assert job_perms.get("contents") == "write", (
            f"execute job must have contents: write, got {job_perms}"
        )

    def test_workflow_permissions_include_issues_write(self):
        """issues: write 在 execute job 级（失败兜底建 Issue 需要）"""
        data = _load(WORKFLOW)
        job_perms = data["jobs"]["execute"].get("permissions", {})
        assert job_perms.get("issues") == "write", (
            f"execute job must have issues: write (for failure fallback Issue), got {job_perms}"
        )

    def test_validate_payload_step_exists(self):
        """workflow 有 payload 验证步骤"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        assert "Validate payload" in steps, (
            "workflow must validate client_payload before processing"
        )

    def test_validate_payload_checks_required_fields(self):
        """payload 验证检查 kind/task_id/repo 三个必需字段"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Validate payload"]
        run = step["run"]
        assert "kind" in run
        assert "task_id" in run
        assert "repo" in run

    def test_validate_payload_rejects_invalid_kind(self):
        """payload 验证拒绝非法 kind 值"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Validate payload"]
        run = step["run"]
        assert "error-gateway" in run
        assert "linear-gateway" in run


# ── VAL-EX-007 基础：结构回归 ──────────────────────────────────────────


class TestWorkflowStructureRegression:
    """VAL-EX-007 基础：workflow 结构回归断言（全量 pytest + actionlint 由 CI 执行）。"""

    def test_workflow_name_is_descriptive(self):
        """workflow 名清晰表达用途"""
        data = _load(WORKFLOW)
        name = data.get("name", "")
        assert "Droid Task" in name or "droid" in name.lower(), (
            f"workflow name should be descriptive, got: {name}"
        )

    def test_workflow_file_has_header_comment(self):
        """workflow 文件有头注释说明设计依据与模型供给链路"""
        raw = WORKFLOW.read_text(encoding="utf-8")
        assert "BYOM" in raw or "byom" in raw, "workflow file should mention BYOM in header or body"
        assert "BYOK" not in raw.replace("BYOK→BYOM", ""), (
            "workflow must not retain standalone BYOK wording (BAILIAN retired)"
        )

    def test_self_checkout_step_exists_and_uses_dispatch_token(self):
        """Checkout executor host repo (self) 步骤存在且使用 DISPATCH_TOKEN"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        checkout = steps.get("Checkout executor host repo (self)", {})
        with_block = checkout.get("with", {})
        token = with_block.get("token", "")
        assert "DISPATCH_TOKEN" in token, f"self-checkout must use DISPATCH_TOKEN, got: {token}"

    def test_checkout_step_uses_dispatch_token(self):
        """checkout target repo 步骤使用 DISPATCH_TOKEN 凭证"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        checkout = steps.get("Checkout target repo", {})
        with_block = checkout.get("with", {})
        token = with_block.get("token", "")
        assert "DISPATCH_TOKEN" in token, f"checkout must use DISPATCH_TOKEN, got: {token}"

    def test_step_order_self_checkout_before_setup_before_target_checkout(self):
        """步骤顺序：Validate → Self checkout → Install CLI → Setup BYOM → Target checkout

        VAL-RTR-002 新语义（2026-09-10 用户裁定 U6）：执行面集中 infra-core，
        droid-task.yml 必须先 checkout 本仓（composite 可用）再 checkout 目标仓。
        """
        data = _load(WORKFLOW)
        names = [s.get("name", "") for s in data["jobs"]["execute"]["steps"]]
        # 核心断言：self-checkout → install → setup → target checkout 顺序
        assert names.index("Validate payload") < names.index("Checkout executor host repo (self)")
        assert names.index("Checkout executor host repo (self)") < names.index("Install Droid CLI")
        assert names.index("Install Droid CLI") < names.index(
            "Setup BYOM (go-github / DeepSeek V4.1 Flash)"
        )
        assert names.index("Setup BYOM (go-github / DeepSeek V4.1 Flash)") < names.index(
            "Checkout target repo"
        )
        assert names.index("Checkout target repo") < names.index("Execute droid")

    def test_execute_droid_runs_in_target_repo_subdirectory(self):
        """Execute droid 步骤含 working-directory: target-repo（VAL-RTR-002）。

        目标仓检出至子目录 target-repo 后，droid 必须在该子目录内执行，
        否则 payload.repo 语义被违背（droid 会在 infra-core 工作区而非目标仓运行）。
        """
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Execute droid"]
        assert step.get("working-directory") == "target-repo", (
            f"Execute droid must run in target-repo subdirectory, "
            f"got working-directory={step.get('working-directory')!r}"
        )

    def test_no_hardcoded_local_paths(self):
        """公开仓库不含本地绝对路径（/Users/... 等）"""
        raw = WORKFLOW.read_text(encoding="utf-8")
        assert "/Users/" not in raw, "workflow must not contain hardcoded local paths (/Users/...)"
        raw_composite = COMPOSITE.read_text(encoding="utf-8")
        assert "/Users/" not in raw_composite, (
            "composite must not contain hardcoded local paths (/Users/...)"
        )


# ── git 身份预配（2026-10-04 INFRA-1283 首执行实证：runner 无 identity 致 commit 被拒）──


class TestGitIdentityConfiguration:
    """Configure git identity 步骤契约：droid 的 commit 必须有 repo 级身份。"""

    def test_git_identity_step_exists(self):
        """Configure git identity 步骤存在，位于 target checkout 之后、Build prompt 之前"""
        data = _load(WORKFLOW)
        names = [s.get("name", "") for s in data["jobs"]["execute"]["steps"]]
        assert "Configure git identity" in names, (
            "droid-task.yml must have a 'Configure git identity' step"
        )
        assert names.index("Checkout target repo") < names.index("Configure git identity")
        assert names.index("Configure git identity") < names.index("Build prompt (error-gateway)")

    def test_git_identity_user_name_exact_match(self):
        """user.name 必须精确为 droid-task[bot]"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps["Configure git identity"].get("run", "")
        assert 'git config user.name "droid-task[bot]"' in run, (
            "identity step must set user.name to droid-task[bot]"
        )

    def test_git_identity_user_email_exact_match(self):
        """user.email 必须精确为 droid-task[bot]@users.noreply.github.com"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps["Configure git identity"].get("run", "")
        assert 'git config user.email "droid-task[bot]@users.noreply.github.com"' in run, (
            "identity step must set user.email to droid-task[bot]@users.noreply.github.com"
        )

    def test_git_identity_configured_in_target_repo_subdir(self):
        """target-repo 子目录也必须配置（Execute droid 的 working-directory 在此，commit 发生于此）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps["Configure git identity"].get("run", "")
        assert "git -C target-repo config user.name" in run, (
            "identity step must also configure target-repo subdirectory (droid commits there)"
        )


# ── VAL-PILOT-008: run 脚本注入面加固 ──────────────────────────────────

# run 脚本内的内联 ${{ }} 表达式是脚本注入面（self-hosted runner 上 payload 值
# 未经验证直接进入 bash 词法层）。契约：droid-task.yml 所有 run 脚本不得内联
# github.event.client_payload 表达式，payload 值一律经 env: 传入脚本。
_INLINE_CLIENT_PAYLOAD_RE = re.compile(r"\$\{\{\s*github\.event\.client_payload")


class TestPayloadInjectionHardening:
    """VAL-PILOT-008: run 脚本零内联 client_payload 表达式（env 传参）。"""

    def _run_scripts(self) -> list[tuple[str, str]]:
        """(step name, run script) 列表。"""
        data = _load(WORKFLOW)
        steps = data["jobs"]["execute"]["steps"]
        return [(s.get("name", ""), s.get("run", "") or "") for s in steps]

    def test_no_run_script_inlines_client_payload(self):
        """全部 run 脚本零内联 github.event.client_payload 表达式"""
        violations = [
            (name, _INLINE_CLIENT_PAYLOAD_RE.findall(run))
            for name, run in self._run_scripts()
            if _INLINE_CLIENT_PAYLOAD_RE.search(run)
        ]
        assert not violations, (
            "run scripts must not inline github.event.client_payload expressions "
            f"(script injection surface on self-hosted runners); pass via env: instead. "
            f"Violations: {[(name, exprs) for name, exprs in violations]}"
        )

    def test_validate_payload_step_uses_env_mapping(self):
        """Validate payload 步骤经 env: 传参（行为不变的搬运）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        step = steps["Validate payload"]
        env = step.get("env", {})
        expected_vars = {
            "KIND": "kind",
            "TASK_ID": "task_id",
            "REPO": "repo",
            "P_ERROR_TYPE": "context.error_type",
            "P_METHOD": "context.method",
            "P_FAILED_EVENT": "context.failed_event",
            "P_IDENTIFIER": "context.identifier",
            "P_TEAM_KEY": "context.team_key",
        }
        for var, payload_path in expected_vars.items():
            assert var in env, f"Validate payload env must map {var} (payload {payload_path})"
            assert payload_path in env[var], (
                f"Validate payload env[{var}] must reference payload "
                f"{payload_path}, got: {env[var]}"
            )

    def test_validate_payload_run_references_env_vars_only(self):
        """Validate payload 脚本只引用 env 变量（零 client_payload 字样）"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps["Validate payload"]["run"]
        assert "client_payload" not in run, (
            "Validate payload run script must reference env vars only, not payload paths"
        )
        # 验证脚本消费的变量都在 env 块中声明（防 env 名/脚本引用漂移）
        env_keys = set(steps["Validate payload"].get("env", {}).keys())
        script_vars = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", run))
        undeclared = script_vars - env_keys - {"GITHUB_OUTPUT"}
        assert not undeclared, (
            f"Validate payload script references undeclared env vars: {sorted(undeclared)}"
        )

    def test_validation_behavior_preserved(self):
        """行为不变：必需字段检查与 kind 白名单仍在"""
        data = _load(WORKFLOW)
        steps = _steps_by_name(data["jobs"]["execute"]["steps"])
        run = steps["Validate payload"]["run"]
        assert '-z "$KIND" || -z "$TASK_ID" || -z "$REPO"' in run, (
            "empty-field check for kind/task_id/repo must be preserved"
        )
        assert '"$KIND" != "error-gateway"' in run, (
            "kind whitelist check (error-gateway) must be preserved"
        )
        assert '"$KIND" != "linear-gateway"' in run, (
            "kind whitelist check (linear-gateway) must be preserved"
        )
        assert '-z "$P_ERROR_TYPE" || -z "$P_METHOD" || -z "$P_FAILED_EVENT"' in run, (
            "error-gateway context field check must be preserved"
        )
        assert '-z "$P_IDENTIFIER" || -z "$P_TEAM_KEY"' in run, (
            "linear-gateway context field check must be preserved"
        )
