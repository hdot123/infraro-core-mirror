"""auto-merge workflow 防毒契约测试（sparse-checkout 工作区污染回归锁定）

背景（2026-08-28 三 runner 污染事故，#48 回归）：auto-merge job 曾以
actions/checkout + sparse-checkout（cone-mode false）只取 triage 单文件。
GitHub-hosted runner 上工作区每 run 重建、无害；但 self-hosted 共享持久
工作区上，checkout 会把该工作区 .git/config 写成 core.sparseCheckout=true
并把工作树裁剪到极小集合——同一 runner 后续所有复用该工作区的 CI job 以
"Can't find action.yml under .github/actions/setup-venv" 失败，且每次
auto-merge 触发都重新污染（排毒三次后仍复发实证）。

本文件锁定六条不变量：
1. auto-merge.yml 任何 job 都不得使用 actions/checkout（该 job 零工作区足迹；
   防毒上优于"去 sparse-checkout 保留全量 checkout"）；
2. 解析后的 workflow 结构中不存在 sparse-checkout / sparse-checkout-cone-mode
   输入（注释中的禁令引用不出现在解析结构里，不影响本断言）；
3. 内联 triage 脚本与 src/infra_core/shell/auto_merge_triage.sh 逐字节一致
   （heredoc 副本禁止静默漂移——workflow 内联版是行为的唯一运行时来源，
   源文件是唯一编辑来源，两者必须同步演进）；
4. triage 调用指向 RUNNER_TEMP（每 run 独立、run 结束即清理），不引用
   工作区相对路径；
5. 门禁 workflow（ci/qa/droid-review）每个 actions/checkout 之前必须有
   Workspace guard 运行时排毒步骤（checkout 自带的 sparse-checkout disable
   实证无法恢复被裁剪的工作树，残留 .git 必须删除重建）；
6. auto-merge job 首步即 Workspace guard——该 job 每 sweep 在三 runner 上
   轮转，是残留毒区的常驻自愈向量。
"""

import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTO_MERGE_YML = REPO_ROOT / ".github/workflows/auto-merge.yml"
# M4 gate-watchdog-automerge：reusable 版 resolve+triage+merge 流水线（memory-core
# thin caller 载体）。防毒/字节一致不变量与自仓 caller 完全同构，逐条锁定。
AUTO_MERGE_PIPELINE_YML = REPO_ROOT / ".github/workflows/auto-merge-pipeline.yml"
# B-2 收敛后的载体面：auto-merge.yml 收敛为触发壳（uses-only 调用 job，零
# steps），业务体（triage heredoc / workspace guard / merge 步）单源于
# auto-merge-pipeline.yml。零工作区足迹类断言（禁 checkout / 禁 sparse 输入）
# 仍覆盖两文件；业务体类断言只覆盖 BODY 载体。
AUTO_MERGE_CARRIERS = (AUTO_MERGE_YML, AUTO_MERGE_PIPELINE_YML)
AUTO_MERGE_BODY_CARRIERS = (AUTO_MERGE_PIPELINE_YML,)
# M6 harden-consolidate-shared-workflows（VAL-HARD-104）：shared-workflows 仓退役
# （README 重定向 + 归档），merge action 行为等价移植为本仓 actions/auto-merge。
# F3 SHA 锁定：自仓引用改为 40 位 SHA + # vTag 格式
# 引用走 owner/repo 全路径 + 不可变 SHA pin（INFRA-678：@main → @v0.7.2 → SHA锁定，
# 防 CI 漂移；升级 = 显式 bump 该常量，回滚 = revert 本 PR，main 上的引用
# 一并还原，归档仓的旧 pin 仍可解析，无第二处 pin 需要同步）。
# 注意：YAML 解析会剥离 # vTag 注释，所以这里只存 SHA 部分
# 2026-09-29 pin 前进：3695445（v0.18.4 基线）→ d5f8196（PR #169 读写 token
# 分离合并提交，已验证与 origin/main actions/auto-merge 内容等价）。
# 2026-10-12 pin 前进：d5f8196 → d894541（PR #323 do-not-merge 逃生舱兜底层
# 落地后内容不再等价，#325 bump 同步；freshness 契约要求 pin 内容 == origin/main）。
# 2026-10-13 pin 前进：d894541 → 731f247（PR #331 首个 gh 调用 fail-loud 化
# （VAL-L-001）落地后内容不再等价，本 bump 同步；同 PR 先例两段式）。
# 2026-10-24 pin 前进：731f247 → 6d20aba（#414 REST 三门绿判与 composite 对齐）。
AUTO_MERGE_ACTION_REF = (
    "hdot123/infraro-core/actions/auto-merge@6d20aba85f56b487fb6a13f9b1eccaf1815d055e"
)
TRIAGE_SH = REPO_ROOT / "src/infra_core/shell/auto_merge_triage.sh"
GUARDED_CHECKOUT_WORKFLOWS = ("ci.yml", "qa.yml", "droid-review.yml")
GUARD_MARKER = 'rm -rf "$GITHUB_WORKSPACE/.git"'

SPARSE_CHECKOUT_KEYS = frozenset({"sparse-checkout", "sparse-checkout-cone-mode"})


def _load_doc(path: Path = AUTO_MERGE_YML) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{path.name} 必须是 YAML mapping"
    return doc


def _iter_keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_keys(item)


def _steps(doc: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for job_name, job in (doc.get("jobs") or {}).items():
        assert isinstance(job, dict), f"job {job_name} 必须是 mapping"
        for step in job.get("steps") or []:
            assert isinstance(step, dict), f"job {job_name} 存在非 mapping 步骤"
            yield str(job_name), step


def _triage_run_script(doc: dict[str, Any]) -> str:
    triage_steps = [
        step
        for _, step in _steps(doc)
        if step.get("id") == "triage" and isinstance(step.get("run"), str)
    ]
    assert len(triage_steps) == 1, "必须恰好包含一个 id=triage 的 run 步骤"
    return triage_steps[0]["run"]


class TestNoWorkspaceFootprint:
    @pytest.mark.parametrize("carrier", AUTO_MERGE_CARRIERS)
    def test_no_job_uses_actions_checkout(self, carrier: Path) -> None:
        """防毒铁律：auto-merge 两载体（自仓 caller + reusable pipeline）全部 job
        禁止 actions/checkout。

        checkout 是唯一会把 core.sparseCheckout=true 写进共享工作区
        .git/config 的入口；该 job 所需的 triage 脚本经 heredoc 内联，
        无需仓库工作区， checkout 必须整步移除而非仅去 sparse 参数。
        """
        offenders = [
            f"{job_name}: {step['uses']}"
            for job_name, step in _steps(_load_doc(carrier))
            if str(step.get("uses", "")).startswith("actions/checkout")
        ]
        assert not offenders, (
            f"{carrier.name} 禁止 actions/checkout（self-hosted 共享工作区防毒），"
            f"违规步骤: {offenders}"
        )

    @pytest.mark.parametrize("carrier", AUTO_MERGE_CARRIERS)
    def test_no_sparse_checkout_inputs_in_structure(self, carrier: Path) -> None:
        """解析后的 workflow 结构不得出现任何 sparse-checkout 输入。"""
        keys = set(_iter_keys(_load_doc(carrier)))
        offenders = sorted(keys & SPARSE_CHECKOUT_KEYS)
        assert not offenders, f"{carrier.name} 结构含 sparse-checkout 输入: {offenders}"


class TestInlineTriageParity:
    @pytest.mark.parametrize("carrier", AUTO_MERGE_BODY_CARRIERS)
    def test_triage_heredoc_byte_identical_to_source(self, carrier: Path) -> None:
        """heredoc 内联脚本必须与 src/ 源文件逐字节一致（防静默漂移）。

        B-2 收敛后业务体单源：锁定载体为 auto-merge-pipeline.yml（memory-core
        thin caller 与自仓触发壳共用执行体）。
        """
        run_script = _triage_run_script(_load_doc(carrier))
        match = re.search(r"<<'TRIAGE_EOF'\n(.*?)\nTRIAGE_EOF\n", run_script, re.DOTALL)
        assert match, f"{carrier.name} triage 步骤必须以 <<'TRIAGE_EOF' heredoc 内联脚本"
        inlined = match.group(1) + "\n"
        source = TRIAGE_SH.read_text(encoding="utf-8")
        assert inlined == source, (
            f"{carrier.name} 内联 triage 脚本与 "
            "src/infra_core/shell/auto_merge_triage.sh 漂移——三份（src + 两载体）必须同步修改"
        )


class TestRestThreeGatesVerdict:
    """v3.2.2 §3.2 三门 REST 绿判（VAL-M1-003）：绿判唯一入口 = REST check-runs(head_sha)
    三门各取 started_at 最新且 completed、conclusion 全 success；
    statusCheckRollup 仅辅助 pending；mergeable 判定禁止走 checks_green(rollup)。

    同步五载体：auto_merge_triage.sh 源文件（本类断言）+ pipeline heredoc
    （TestInlineTriageParity 字节锁保证同源）+ actions/auto-merge/action.yml
    （REST 门判富集）+ ci-ok/零红引用 + 测试。
    """

    THREE_GATES = ("substrate-gate-suite", "quality-gate", "droid-review")

    def test_triage_source_verdict_is_rest_three_gates(self) -> None:
        """triage 源文件：mergeable 门判必须是 REST 三门（required_gate_success）。"""
        script = TRIAGE_SH.read_text(encoding="utf-8")
        assert "required_gate_success" in script, "triage 缺 REST 三门绿判函数"
        for gate in self.THREE_GATES:
            assert f'"{gate}"' in script, f"triage REST 三门名单缺 {gate}"
        # round-4 P1（shard-16）修订：实例排序键 = check-run id（round-3 ①）。
        # 旧断言 "started_at"/"sort_by" 只查注释文本即可满足（started_at 现仅
        # 存在于「为何弃用 started_at 排序」的注释里），回摆成
        # sort_by(.started_at) 也能过——正是该排序引发的 #7/#168 缺陷。
        # 行为级语义由 TestRestThreeGatesInstanceSelection 锁定；此处锁结构：
        # 排序必须挂在 .id 上，禁止 .started_at 出现在排序表达式中。
        gate_fn = re.search(
            r"def gate_latest_completed\(pr; gate\):(.*?)# 绿判收敛点", script, re.DOTALL
        )
        assert gate_fn, "triage 缺 gate_latest_completed 函数"
        assert "sort_by(.id" in gate_fn.group(1), (
            "triage 实例选择必须按 check-run id 排序（round-3 ①；.started_at 排序"
            "是 #7 fail-open / #168 回摆根因，禁止回归）"
        )
        assert ".started_at" not in gate_fn.group(1), (
            "gate_latest_completed 禁止以 started_at 为排序键（round-4 P1 shard-16："
            "旧断言被注释文本满足，回摆不可检）"
        )
        # checkRuns 富集字段必须被消费（REST 取数由调用方注入）
        assert "checkRuns" in script, "triage 必须消费 checkRuns 富集字段（REST 取数）"

    def test_triage_rollup_not_in_mergeable_path(self) -> None:
        """statusCheckRollup 禁止参与 mergeable 判定（仅辅助 pending）。"""
        script = TRIAGE_SH.read_text(encoding="utf-8")
        assert "checks_green" not in script, (
            "checks_green(rollup) 必须从 triage 删除——mergeable 禁走 rollup（v3.2.2 §3.2）"
        )
        # rollup 仅保留 pending 辅助（未报齐 → pending）
        assert "checks_reported" in script, "rollup 未报齐 → pending 的辅助判定必须保留"

    def test_pipeline_triage_fetch_enriches_rest_check_runs(self) -> None:
        """pipeline 载体：triage 取数后必须富集 REST check-runs(head_sha)。"""
        run_script = _triage_run_script(_load_doc(AUTO_MERGE_PIPELINE_YML))
        # 行为级：REST 富集命令本体必须在 triage 取数步内真实执行——
        # `gh api .../check-runs` 管道 + checkRuns 注入赋值行。
        # token 级检查（check-runs 字样出现于注释）不构成行为证据。
        enrichment_lines = [
            ln for ln in run_script.splitlines() if "gh api" in ln and "check-runs" in ln
        ]
        assert enrichment_lines, (
            "triage 取数步必须存在 REST 富集命令本体（gh api .../check-runs），"
            "注释出现不算（#9：token 级契约删富集块仍绿）"
        )
        enrich_cmd = enrichment_lines[0]
        assert "commits/$HEAD_SHA/check-runs" in enrich_cmd, (
            "REST 富集必须绑定当前 head SHA（commits/$HEAD_SHA/check-runs）"
        )
        # 注入：PR_DATA 重赋值行必须携带 checkRuns 字段注入（--argjson cr）
        inject_lines = [
            ln for ln in run_script.splitlines() if "--argjson cr" in ln and "checkRuns" in ln
        ]
        assert inject_lines, (
            "取数结果必须以 --argjson cr 注入 checkRuns 字段供 triage 消费（删富集块即本断言红）"
        )
        assert any("PR_DATA=" in ln for ln in inject_lines), (
            "checkRuns 注入必须重赋值回 PR_DATA（仅构造不消费 = 无效富集）"
        )
        assert "headRefOid" in run_script, "REST 取数必须绑定当前 head SHA（headRefOid）"
        assert "checkRuns" in run_script, "取数结果必须注入 checkRuns 字段供 triage 消费"
        # 取数 --json 行必须包含 headRefOid（head SHA 绑定）
        fetch_line = next(
            (ln for ln in run_script.splitlines() if "--json" in ln and "gh pr view" in ln),
            None,
        )
        assert fetch_line is not None, "triage 步骤未找到 gh pr view --json 取数行"
        assert "headRefOid" in fetch_line, "triage 取数缺 headRefOid（REST 门判 SHA 绑定失效）"

    def test_action_yml_verdict_is_rest_three_gates(self) -> None:
        """action.yml：合并前必须断言 REST 三门 conclusion 全 success（第一道门）。"""
        action_yml = REPO_ROOT / "actions/auto-merge/action.yml"
        script = action_yml.read_text(encoding="utf-8")
        assert "REQUIRED_GATES" in script, "action 缺 REST 三门名单（required_gate_success 前置门）"
        for gate in self.THREE_GATES:
            assert f'"{gate}"' in script, f"action REST 三门名单缺 {gate}"
        gate_fn = re.search(
            r"def gate_latest_completed\(gate\):(.*?)\| last",
            script,
            re.DOTALL,
        )
        assert gate_fn, "action 缺 gate_latest_completed 函数"
        assert "sort_by(.id" in gate_fn.group(1), (
            "action 实例选择必须按 check-run id 排序（round-3 ①；禁止 started_at 排序回归）"
        )
        assert ".started_at" not in gate_fn.group(1), (
            "gate_latest_completed 禁止以 started_at 为排序键（round-4 P1：注释可满足旧断言）"
        )

    def test_shell_iron_rules_comment_updated(self) -> None:
        """壳文件零红铁律注释同步：rollup 判定表述改为 REST 三门 + 旁路层。"""
        raw = AUTO_MERGE_YML.read_text(encoding="utf-8")
        assert "REST 三门" in raw, "壳注释必须更新为 REST 三门绿判语义"
        assert "statusCheckRollup 全 SUCCESS/SKIPPED/NEUTRAL" not in raw, (
            "壳注释残留旧 rollup 绿判表述（checks_green 已删除）"
        )

    @pytest.mark.parametrize("carrier", AUTO_MERGE_BODY_CARRIERS)
    def test_triage_invocation_targets_runner_temp(self, carrier: Path) -> None:
        """triage 调用必须指向 RUNNER_TEMP，禁止工作区相对路径残留。"""
        run_script = _triage_run_script(_load_doc(carrier))
        assert 'bash "$RUNNER_TEMP/auto_merge_triage.sh"' in run_script, (
            f"{carrier.name}: triage 必须经 $RUNNER_TEMP 调用内联脚本"
        )
        assert "bash src/infra_core/shell/auto_merge_triage.sh" not in run_script, (
            f"{carrier.name}: triage 不得经工作区相对路径执行脚本（本 job 无工作区；注释中的路径引用不受限）"
        )


def _job_steps(path: Path) -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
    """遍历 (workflow 名, job 名, steps 列表)。"""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job_name, job in (doc.get("jobs") or {}).items():
        steps = (job or {}).get("steps") or []
        yield path.name, str(job_name), steps


class TestWorkspaceGuard:
    """checkout 前置运行时排毒守卫（2026-08-28 残留毒区自愈机制）。

    actions/checkout 自带的 `git sparse-checkout disable` 实证无法恢复被
    裁剪的工作树（disable 后仍缺 .github/actions/setup-venv，CI 全线
    ~13s 失败）。守卫必须在 checkout 前以纯 shell 内联 run 检测残留并
    删除 .git——工作树残缺时本地 composite action 同样无法解析，不可
    封装为 action。
    """

    @pytest.mark.parametrize("workflow", GUARDED_CHECKOUT_WORKFLOWS)
    def test_every_checkout_preceded_by_guard(self, workflow: str) -> None:
        path = REPO_ROOT / ".github/workflows" / workflow
        for wf_name, job_name, steps in _job_steps(path):
            for idx, step in enumerate(steps):
                uses = str(step.get("uses", ""))
                if not uses.startswith("actions/checkout"):
                    continue
                assert idx > 0, f"{wf_name}/{job_name}: checkout 是首步，缺 Workspace guard"
                prev = steps[idx - 1]
                prev_run = str(prev.get("run", ""))
                assert GUARD_MARKER in prev_run and "sparse" in prev.get("name", ""), (
                    f"{wf_name}/{job_name}: checkout 前一步不是 Workspace guard"
                )

    def test_no_unaccompanied_checkout_count(self) -> None:
        """守卫数量与 checkout 数量一致（防未来新增 job 漏配守卫）。"""
        for workflow in GUARDED_CHECKOUT_WORKFLOWS:
            path = REPO_ROOT / ".github/workflows" / workflow
            checkouts = 0
            guards = 0
            for _, _, steps in _job_steps(path):
                for step in steps:
                    if str(step.get("uses", "")).startswith("actions/checkout"):
                        checkouts += 1
                    if "sparse" in str(step.get("name", "")):
                        guards += 1
            assert checkouts == guards, (
                f"{workflow}: checkout({checkouts}) 与守卫({guards})数量不一致"
            )

    @pytest.mark.parametrize("carrier", AUTO_MERGE_BODY_CARRIERS)
    def test_automerge_job_first_step_is_guard(self, carrier: Path) -> None:
        """auto-merge job 首步 = Workspace guard：每 sweep runner 池常驻自愈。"""
        steps = _load_doc(carrier)["jobs"]["auto-merge"]["steps"]
        first = steps[0]
        assert "sparse" in str(first.get("name", "")), (
            f"{carrier.name}: auto-merge job 首步必须是 Workspace guard"
        )
        assert GUARD_MARKER in str(first.get("run", "")), (
            f"{carrier.name}: guard 步骤必须包含排毒逻辑"
        )


class TestReusablePipelineTemplateContract:
    """auto-merge-pipeline.yml reusable 模板契约（M4 gate-watchdog-automerge）。

    锁定 memory-core thin caller（VAL-GATE-106）所依赖的不变量：
    触发面归 caller（workflow_run 四名单 / pull_request_target / schedule /
    workflow_dispatch + 事件门控 + concurrency 全部不进本文件）；本文件只承载
    resolve+triage+merge 执行体；merge 步引用本仓 actions/auto-merge
    （M6 VAL-HARD-104 自 shared-workflows@5a0fc1b 行为等价收编，该仓退役归档）。
    """

    def test_workflow_name(self) -> None:
        assert _load_doc(AUTO_MERGE_PIPELINE_YML)["name"] == "Auto Merge Pipeline"

    def test_only_workflow_call_trigger(self) -> None:
        """触发面归 caller：本文件只允许 workflow_call，禁止自带任何触发器。"""
        pipeline_data = _load_doc(AUTO_MERGE_PIPELINE_YML)
        triggers = pipeline_data.get(True, {}) or pipeline_data.get("on", {})
        assert set(triggers.keys()) == {"workflow_call"}, (
            f"reusable 不得自带触发器（workflow_run 名单留在 caller），实际: {list(triggers.keys())}"
        )

    def test_no_event_guard_inside_reusable(self) -> None:
        """事件门控（workflow_run 仅 success 尝试合并）归 caller：本文件零守卫引用。"""
        raw = AUTO_MERGE_PIPELINE_YML.read_text(encoding="utf-8")
        assert "conclusion == 'success'" not in raw
        assert "event_name == 'workflow_run' &&" not in raw

    def test_dispatch_token_snake_single_form_declared(self) -> None:
        """snake 单形态键声明（SNAKE-CONVERGENCE v0.18.5 立法）。

        GitHub 对 caller secrets: 传键做声明面严格校验——caller 传了未声明键
        → run 级 startup_failure（零 job）。历史上 callee 曾双形态声明
        （dispatch_token + dispatch-token 并存，CONSUMER-GATE-DEADLOCK 修复
        2026-08-30）；v0.18.5 蛇形收敛立法后 hyphen 过渡变体全部删除，
        dispatch_token 是唯一声明键且 required: true——缺凭证的 fail-closed
        语义由运行时合并步凭证失败保持，未声明键在 plan-time 即 fail-fast。
        """
        pipeline_data = _load_doc(AUTO_MERGE_PIPELINE_YML)
        secrets_block = pipeline_data[True]["workflow_call"].get("secrets", {})
        assert "dispatch_token" in secrets_block, (
            "必须声明 dispatch_token secret 输入（snake 单形态）"
        )
        assert secrets_block["dispatch_token"]["required"] is True
        for key in secrets_block:
            assert "-" not in key and key == key.lower(), (
                f"workflow_call secrets 键必须 snake 小写单形态，违例: {key}"
            )

    def test_job_topology(self) -> None:
        """job 拓扑：authorization-gate → resolve → auto-merge。

        2026-09-19 m6-engine-authorization-gate：入口新增授权门 job（消费仓
        未授权 fail-loud），resolve 受门；auto-merge 仍只依赖 resolve。
        守卫语义/防漂移由 tests/test_engine_authorization_gate_contract.py
        锁定（改拓扑必须两处同改）。
        """
        jobs = _load_doc(AUTO_MERGE_PIPELINE_YML)["jobs"]
        assert set(jobs.keys()) == {"authorization-gate", "resolve", "auto-merge"}
        assert jobs["resolve"].get("needs") == "authorization-gate"
        assert jobs["auto-merge"].get("needs") == "resolve"
        matrix = jobs["auto-merge"]["strategy"]["matrix"]
        assert matrix["pr_number"] == "${{ fromJSON(needs.resolve.outputs.pr_numbers) }}"

    def test_merge_step_uses_consolidated_infra_core_action(self) -> None:
        """VAL-HARD-104：merge 步引用本仓 actions/auto-merge（shared-workflows 已退役）。"""
        steps = _load_doc(AUTO_MERGE_PIPELINE_YML)["jobs"]["auto-merge"]["steps"]
        # F3: SHA-locked format - match 40-char hex SHA
        merge_step = next(
            (
                s
                for s in steps
                if re.search(r"/actions/auto-merge@[0-9a-f]{40}", str(s.get("uses", "")))
            ),
            None,
        )
        assert merge_step is not None, "actions/auto-merge merge 步缺失"
        assert merge_step["uses"] == AUTO_MERGE_ACTION_REF, (
            f"merge 步必须引用本仓 actions/auto-merge SHA-locked，实际: {merge_step['uses']}"
        )

    def test_no_shared_workflows_residual(self) -> None:
        """VAL-HARD-104：shared-workflows 退役后两载体零 live 引用（uses: 面，防回潮）。

        断言面是 step 的 uses: 引用（真正会被 GitHub 解析执行的部分）；
        注释中的移植溯源文字（含仓名+SHA）不算引用，允许保留。
        """
        for carrier in AUTO_MERGE_CARRIERS:
            offenders = [
                f"{job}: {step.get('uses')}"
                for job, step in _steps(_load_doc(carrier))
                if "shared-workflows" in str(step.get("uses", ""))
            ]
            assert not offenders, (
                f"{carrier.name} 残留 shared-workflows uses: 引用——该仓已退役归档，"
                f"merge action 在本仓 actions/auto-merge：{offenders}"
            )

    def test_merge_step_uses_dispatch_token_not_github_token(self) -> None:
        """合并凭证必须来自 dispatch_token secret 输入（PAT 防递归抑制），绝不回退 GITHUB_TOKEN。"""
        steps = _load_doc(AUTO_MERGE_PIPELINE_YML)["jobs"]["auto-merge"]["steps"]
        # F3: SHA-locked format - match 40-char hex SHA
        merge_step = next(
            s
            for s in steps
            if re.search(r"/actions/auto-merge@[0-9a-f]{40}", str(s.get("uses", "")))
        )
        env = merge_step.get("env", {})
        assert env.get("GITHUB_TOKEN") == "${{ secrets.dispatch_token }}", (
            "merge 步 GITHUB_TOKEN env 必须经 dispatch_token snake 单读传入，"
            f"实际: {env.get('GITHUB_TOKEN')}"
        )

    def test_no_secret_fusion_expressions(self) -> None:
        """SNAKE-CONVERGENCE 立法：secrets 消费点一律 snake 单读，熔合表达式归零。

        旧双形态过渡期曾要求 `secrets.dispatch_token || secrets['dispatch-token']`
        熔合；v0.18.5 收敛后 hyphen 变体已删，任何 `|| secrets.` 回退或
        `secrets['...']` 括号取键形态都算回潮违例。
        """
        raw = AUTO_MERGE_PIPELINE_YML.read_text(encoding="utf-8")
        assert "|| secrets." not in raw, "secrets 消费点不得保留熔合表达式（|| secrets.）"
        assert "secrets['" not in raw, "secrets 消费点不得使用括号取键形态（secrets['…']）"

    def test_permissions_block(self) -> None:
        """2026-09-19 governance（VAL-CORE-004）：write 下放 job 级。

        顶层只留 read 基线（四 scope 全 read；INFRA-688 兜底语义保持）；
        resolve job 只读；auto-merge job 保留原顶层 write 面（contents/
        pull-requests write——合并凭证实际经 dispatch_token PAT，job 级
        声明为最坏情况兜底，行为与下放前等价）。
        """
        doc = _load_doc(AUTO_MERGE_PIPELINE_YML)
        read_baseline = {
            "contents": "read",
            "pull-requests": "read",
            "checks": "read",
            "actions": "read",
        }
        assert doc.get("permissions", {}) == read_baseline, (
            f"reusable 顶层权限必须全 read 基线，实际: {doc.get('permissions')}"
        )
        jobs = doc["jobs"]
        assert jobs["resolve"].get("permissions") == read_baseline, (
            f"resolve 只读，实际: {jobs['resolve'].get('permissions')}"
        )
        assert jobs["auto-merge"].get("permissions") == {
            "contents": "write",
            "pull-requests": "write",
            "checks": "read",
            "actions": "read",
        }, f"auto-merge job 保留原 write 面，实际: {jobs['auto-merge'].get('permissions')}"

    def test_all_jobs_self_hosted(self) -> None:
        for job_name, job in _load_doc(AUTO_MERGE_PIPELINE_YML)["jobs"].items():
            # INFRA-1317 形态终裁：authorization-gate 是本地内联 job（run step
            # 内联判定脚本，零 checkout 可解析）——reusable 内 uses-only 调用
            # job 已废止（reusable→reusable 嵌套调用实证 startup_failure）。
            assert job.get("uses") is None, (
                f"{job_name} 不得是 uses job（reusable→reusable 嵌套调用禁令，P0 形态红线）"
            )
            assert job.get("runs-on") == ["pve-linux"], (
                f"{job_name} 必须跑在私有自建池上（2026-09-25 所有者裁定：私有仓一律自建）"
            )

    def test_workspace_guard_probe_uses_consumer_agnostic_file(self) -> None:
        """reusable 版 guard 探针必须用 .github/workflows（语言中立目录，
        非 Python 消费仓无 pyproject.toml 时会每 run 全量重克隆——v0.15.0 改）。"""
        steps = _load_doc(AUTO_MERGE_PIPELINE_YML)["jobs"]["auto-merge"]["steps"]
        guard_run = str(steps[0].get("run", ""))
        assert ".github/workflows" in guard_run
        assert ".github/actions/setup-venv" not in guard_run


class TestAutoMergeCompositeActionContract:
    """VAL-HARD-104：actions/auto-merge composite action 移植契约。

    自 shared-workflows 仓 auto-merge action @5a0fc1b 行为等价移植
    （该仓已退役归档）。本类锁定移植保真面：composite 形态与
    元数据、零红扫描语义（全 check-runs 直查 + 四类非绿结论拦截）、
    squash + 删分支合并命令、凭证经 env GITHUB_TOKEN 注入（caller 侧铁律
    绑定 DISPATCH_TOKEN PAT，禁默认 GITHUB_TOKEN）。
    """

    ACTION_YML = REPO_ROOT / "actions/auto-merge/action.yml"

    def _load_action(self) -> dict[str, Any]:
        assert self.ACTION_YML.exists(), "actions/auto-merge/action.yml 必须存在"
        doc = yaml.safe_load(self.ACTION_YML.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), "action.yml 必须是 YAML mapping"
        return doc

    @staticmethod
    def _action_script(doc: dict[str, Any]) -> str:
        return " ".join(str(step.get("run", "")) for step in doc["runs"]["steps"])

    def test_composite_with_pr_number_input(self) -> None:
        doc = self._load_action()
        assert doc["name"] == "Auto Merge PR"
        assert doc["runs"]["using"] == "composite"
        inputs = doc.get("inputs") or {}
        assert inputs.get("pr-number", {}).get("required") is True
        assert inputs.get("pr-number", {}).get("type") == "string"

    def test_zero_red_scan_semantics_preserved(self) -> None:
        """零红铁律载体：merge 前全 check-runs 扫描（四类非绿结论全部拦截）。"""
        script = self._action_script(self._load_action())
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            assert conclusion in script, f"零红扫描缺少 {conclusion} 判定"
        assert "check-runs" in script, "必须直查 check-runs（含 advisory 层全量）"

    def test_required_gates_precondition_before_merge(self) -> None:
        """v3.2.2 §3.2：REST 三门 conclusion 全 success 必须是合并前置门。

        Round-2 P2 #10（#381 终局分级）随修对齐：GATE_FAIL 断言从 token 级
        升为行为级——门判执行体必须真实聚合（jq 程序提取 + 行为级红绿用例
        见 test_auto_merge_read_write_token_contract.py
        TestGateInstanceSelectionFailClosed：更新 in_progress/null started_at
        取消实例均拒合、全 success 放行）。此处仅锁聚合在合并前发生。
        """
        doc = self._load_action()
        script = self._action_script(doc)
        assert "REQUIRED_GATES" in script, "action 缺 REST 三门名单"
        assert "GATE_FAIL" in script, (
            "action 缺三门门判执行体（全部实例取 started_at 最新者、completed 且全 success）"
        )
        # 行为级补充：GATE_FAIL 必须来自真实 jq 聚合赋值（非注释/字符串）
        assert re.search(r"^GATE_FAIL=\$\(jq -s ", script, re.MULTILINE), (
            "GATE_FAIL 必须由 jq -s 聚合赋值行产生（token 级存在 ≠ 行为执行）"
        )
        # 三门门判必须在合并命令之前（第一道门，先于旁路零红扫描与 merge）
        assert script.index("REQUIRED_GATES") < script.index("gh pr merge"), (
            "REST 三门门判必须在合并命令之前"
        )

    def test_squash_delete_branch_merge_command(self) -> None:
        script = self._action_script(self._load_action())
        assert "--squash" in script and "--delete-branch" in script, (
            "合并命令必须保持 gh pr merge --squash --delete-branch"
        )

    def test_credential_via_env_github_token(self) -> None:
        """composite 步凭证取自 env GITHUB_TOKEN——由 caller 注入 DISPATCH_TOKEN PAT。"""
        first = self._load_action()["runs"]["steps"][0]
        assert first.get("env", {}).get("GH_TOKEN") == "${{ env.GITHUB_TOKEN }}"

    def test_no_shared_workflows_reference(self) -> None:
        """action 内部 step 不得引用已退役仓（uses: 面；溯源注释不算）。"""
        doc = self._load_action()
        offenders = [
            step.get("uses")
            for step in doc["runs"]["steps"]
            if "shared-workflows" in str(step.get("uses", ""))
        ]
        assert not offenders, f"actions/auto-merge 内残留 shared-workflows 引用：{offenders}"

    def test_merge_failure_explicit_exit_1(self) -> None:
        """VAL-M2-202：gh pr merge 失败路径必须显式退出（禁止静默绿）。

        回归锁定：2026-08-31 F5 加固前，action.yml 第 87-91 行 else 分支仅 echo
        未退出，导致 merge 失败后脚本继续执行、最终 exit 0 假绿。修复后必须：
        1) 失败时 re-check PR state（gh pr view --json state）
        2) 若 state=MERGED（竞态：已被并行腿合并）→ exit 0
        3) 若 state≠MERGED（真失败）→ exit 1
        4) 禁止无守卫的 echo-only 路径（防止静默吞错误）
        """
        script = self._action_script(self._load_action())

        # 必须包含 gh pr merge 失败后的 state re-check
        assert "gh pr merge" in script, "必须调用 gh pr merge"
        assert "gh pr view" in script and "state" in script, (
            "gh pr merge 失败后必须 re-check PR state（竞态守卫）"
        )

        # 必须有显式 exit 1（非竞态失败路径）
        assert "exit 1" in script, "真失败路径必须显式 exit 1"

        # 必须有 MERGED 状态判定（竞态成功降级）
        assert "MERGED" in script, "必须识别 MERGED 状态（竞态成功降级）"

        # 验证结构：失败分支不能仅 echo 而不退出（禁止静默绿）
        # 提取 else 分支片段，检查必须包含 exit 或 exit 0（race）
        lines = script.splitlines()
        in_merge_else = False
        else_block_lines = []
        indent_level = None
        for line in lines:
            if "gh pr merge" in line and "then" in line:
                # 进入 if gh pr merge ... then 结构
                continue
            if (
                not in_merge_else
                and "else" in line
                and any("gh pr merge" in prev for prev in lines[: lines.index(line)])
            ):
                # 找到 gh pr merge 的 else 分支
                in_merge_else = True
                indent_level = len(line) - len(line.lstrip())
                continue
            if in_merge_else:
                current_indent = len(line) - len(line.lstrip()) if line.strip() else indent_level
                # else 块结束条件：遇到同级或更浅的非空行（fi 或后续代码）
                if line.strip() and current_indent <= indent_level and "fi" not in line:
                    break
                else_block_lines.append(line)

        else_block = "\n".join(else_block_lines)
        assert "exit" in else_block or "exit 0" in else_block, (
            "gh pr merge 失败分支必须有显式 exit（禁止静默 echo-only 路径）"
        )


class TestAutoMergeShellContract:
    """B-2 收敛契约：auto-merge.yml 收敛为触发壳，业务体单源 pipeline。

    壳只承载触发面（4 类触发器）+ workflow 级 concurrency + 事件门控 +
    secrets 显式传递；resolve/triage/merge 业务体仅存于
    auto-merge-pipeline.yml（其入口 authorization-gate job 对引擎仓自身
    豁免，调用即等价旧内联形态）。基线 = B-2 收敛前 origin/main
    （5fa9d987..B-2 前最后 main 提交）的 auto-merge.yml 触发块。
    """

    SHELL_YML = AUTO_MERGE_YML
    PIPELINE_YML = AUTO_MERGE_PIPELINE_YML
    # VAL-B2-001：旧 job 业务体关键结构在壳文件零残留（YAML 结构 + raw 双查）。
    # 逐字节体（TRIAGE_EOF heredoc）已由 TestInlineTriageParity 锁定于 pipeline
    # 载体；本表锁定业务体入口结构不得在壳复活。
    BODY_RESIDUE_PATTERNS = (
        "TRIAGE_EOF",
        "gh pr update-branch",
        "auto-merge-conflict-",
        "auto-merge-stalled-",
        "/actions/auto-merge@",
        "Handle BEHIND PR",
        "Handle CONFLICTING PR",
        "Triage PR mergeable state",
    )

    def _shell(self) -> dict[str, Any]:
        return _load_doc(self.SHELL_YML)

    def _shell_triggers(self) -> dict[str, Any]:
        data = self._shell()
        return data.get(True, {}) or data.get("on", {})

    def test_shell_has_exactly_four_triggers(self) -> None:
        """4 类触发器逐一保持，且壳不引入额外触发器。"""
        triggers = self._shell_triggers()
        assert set(triggers.keys()) == {
            "workflow_run",
            "pull_request_target",
            "schedule",
            "workflow_dispatch",
        }

    def test_shell_workflow_run_upstream_list(self) -> None:
        """workflow_run 五上游名单与类型逐项对照基线。"""
        wr = self._shell_triggers()["workflow_run"]
        assert sorted(wr["workflows"]) == sorted(
            ["CI", "QA", "Droid Auto Review", "Evolution Governance", "Quality Gate"]
        ), f"workflow_run names drifted: {wr['workflows']}"
        assert wr["types"] == ["completed"]

    def test_shell_pull_request_target_types(self) -> None:
        assert self._shell_triggers()["pull_request_target"]["types"] == [
            "opened",
            "synchronize",
            "reopened",
        ]

    def test_shell_schedule_cron_30min(self) -> None:
        # 名义 */30；平台实测投递约每天 4.8 次（2026-10-18 观测），按小时级兜底评估
        assert self._shell_triggers()["schedule"] == [{"cron": "*/30 * * * *"}]

    def test_shell_dispatch_input_optional_string(self) -> None:
        inputs = self._shell_triggers()["workflow_dispatch"]["inputs"]
        assert inputs["pr_number"]["required"] is False
        assert inputs["pr_number"]["type"] == "string"

    def test_shell_concurrency_group_unchanged(self) -> None:
        """顶层 concurrency group 'auto-merge-pipeline' + 排队不取消。"""
        concurrency = self._shell()["concurrency"]
        assert concurrency["group"] == "auto-merge-pipeline"
        assert concurrency["cancel-in-progress"] is False

    def test_shell_is_pure_trigger_surface(self) -> None:
        """壳零业务体：调用 job 零 steps（uses-only 转发），事件门控在场。"""
        doc = self._shell()
        jobs = doc["jobs"]
        assert set(jobs.keys()) == {"auto-merge"}, "壳只保留单一调用 job"
        caller = jobs["auto-merge"]
        assert caller.get("steps") is None, "调用 job 不得携带 steps（业务体必须单源 pipeline）"
        assert caller["uses"] == "./.github/workflows/auto-merge-pipeline.yml"
        raw = self.SHELL_YML.read_text(encoding="utf-8")
        assert "github.event.workflow_run.conclusion == 'success'" in raw, (
            "事件门控必须留在 caller（github 事件上下文只在 caller 存在）"
        )

    def test_shell_passes_dispatch_token_snake_form(self) -> None:
        """secrets 显式传递：snake 单形态键对接 pipeline 声明，缺传即 startup_failure。"""
        caller = self._shell()["jobs"]["auto-merge"]
        secrets_block = caller.get("secrets") or {}
        assert secrets_block == {"dispatch_token": "${{ secrets.DISPATCH_TOKEN }}"}
        declared = _load_doc(self.PIPELINE_YML)[True]["workflow_call"]["secrets"]
        assert "dispatch_token" in declared, "pipeline 必须声明 dispatch_token 输入"

    def test_shell_zero_business_body_residue(self) -> None:
        """VAL-B2-001：旧 job 业务体关键词在壳文件零残留（结构与 run 体双查）。"""
        raw = self.SHELL_YML.read_text(encoding="utf-8")
        offenders = [p for p in self.BODY_RESIDUE_PATTERNS if p in raw]
        assert not offenders, f"壳文件残留业务体关键词: {offenders}"

    def test_shell_permissions_baseline(self) -> None:
        """壳顶层 read 基线 + 调用 job write 兜底（VAL-CORE-004 语义保持）。"""
        doc = self._shell()
        read_baseline = {
            "contents": "read",
            "pull-requests": "read",
            "checks": "read",
            "actions": "read",
        }
        assert doc.get("permissions", {}) == read_baseline
        assert doc["jobs"]["auto-merge"].get("permissions") == {
            "contents": "write",
            "pull-requests": "write",
            "checks": "read",
            "actions": "read",
        }

    def test_shell_workflow_name_unchanged(self) -> None:
        raw = self.SHELL_YML.read_text(encoding="utf-8")
        assert re.search(r"^name:\s*Auto Merge\s*$", raw, re.MULTILINE)

    def test_shell_iron_rules_comments_migrated(self) -> None:
        """头部铁律注释随壳迁移保真（禁 checkout / 零红铁律双层判定）。

        v3.2.2 §3.2：零红铁律的双层判定表述同步为「REST 三门绿判 + 旁路层
        零红扫描」——statusCheckRollup 不再参与绿判（仅辅助 pending）。
        """
        raw = self.SHELL_YML.read_text(encoding="utf-8")
        assert "严禁 actions/checkout" in raw
        assert "sparse-checkout" in raw
        assert "零红铁律" in raw
        assert "REST 三门" in raw
        assert "statusCheckRollup" in raw  # 辅助 pending 语义仍在注释中
        assert "不得只查 required contexts" in raw
