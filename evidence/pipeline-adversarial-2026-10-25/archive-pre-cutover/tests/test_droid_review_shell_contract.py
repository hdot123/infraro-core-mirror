"""B-1 收敛契约：droid-review.yml 触发壳 + 单源分片 + 本地聚合。

锁定（对照 tests/test_auto_merge_workflow_contract.py::TestAutoMergeShellContract 同款模式）：
- 触发面：workflow_run(Quality Gate) + workflow_dispatch（无 pull_request：
  secret-bearing 腿不信任 PR-branch workflow 定义；PR head 原生 rollup 由
  droid-review-pr-rollup.yml 承担）
- 分片业务体单源：壳零 setup/plan-shards/review-shard 内联 job（三 job 单源
  droid-review-shards.yml，跨仓 thin caller 同源）
- 聚合 job key `droid-review` 留壳本地（ruleset 23079535 required check 产出
  点，reusable 内部 check 名嵌套禁入，VAL-GATE-103）
- artifact 前缀 `droid-review-debug-`（聚合下载 pattern 与 reusable 上传名一致）
- 凭证显式传递（SNAKE-CONVERGENCE）：shards 调用 job secrets 四键全透传
- authorization-gate 形态（INFRA-1317 终裁）：matches_token 判定脚本内联在
  四载体 job（零 checkout 载体上 composite 无法解析，run 37305718689 实证；
  reusable→reusable 嵌套调用 startup_failure 已废止）——四载体逐字节一致，
  单源由契约测试字节锁定（改一处必须四处同改），零第四份拷贝
"""

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL_YML = REPO_ROOT / ".github/workflows/droid-review.yml"
SHARDS_YML = REPO_ROOT / ".github/workflows/droid-review-shards.yml"
GATE_REUSABLE_YML = REPO_ROOT / ".github/workflows/engine-authorization-gate.yml"
GATE_COMPOSITE_DIR = REPO_ROOT / ".github/actions/engine-authorization-gate"
GATE_CARRIER_RELS = (
    ".github/workflows/auto-merge-pipeline.yml",
    ".github/workflows/droid-review-shards.yml",
    ".github/workflows/branch-cleanup.yml",
    "docs/onboarding/templates/branch-cleanup.thin-caller.yml",
)

# 旧内联分片 job 关键结构在壳零残留（YAML 结构 + raw 双查）。
# 注意：'Workspace guard (sparse residue detox)' 不在此列——基线聚合 job 本步
# 就有（合法自有序步），raw 文本在场≠分片业务体残留；残留守卫由上方禁入
# job key 断言承担。
SHARD_BODY_RESIDUE_PATTERNS = (
    "plan_shards.py",
    "run_shard.sh",
    "Install Droid CLI",
    "Setup BYOM",
    "Upload file statuses",
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestDroidReviewShellContract:
    def test_shell_triggers_updated(self) -> None:
        """触发面：workflow_run(Quality Gate) + dispatch；禁 pull_request（#420 P1）。"""
        triggers = _load(SHELL_YML).get(True, {}) or _load(SHELL_YML).get("on", {})
        wr = triggers["workflow_run"]
        assert wr["workflows"] == ["Quality Gate"]
        assert "pull_request" not in triggers
        assert "pull_request_target" not in triggers
        jobs = _load(SHELL_YML)["jobs"]
        assert "preflight" in jobs
        preflight_if = str(jobs["preflight"].get("if", ""))
        assert "github.event_name == 'pull_request'" not in preflight_if
        assert "workflow_run" in preflight_if
        assert "check_cheap_gates_green.sh" in SHELL_YML.read_text(encoding="utf-8")
        shards_needs = jobs["shards"].get("needs")
        assert shards_needs == ["preflight"] or shards_needs == "preflight"
        inputs = triggers["workflow_dispatch"]["inputs"]
        for name in ("pr_number", "head_sha"):
            assert inputs[name]["required"] is False
            assert inputs[name]["type"] == "string"

    def test_shell_concurrency_dispatch_vs_auto(self) -> None:
        """workflow 级并发：dispatch 与 auto 分桶；无 pull_request 腿后可 cancel-in-progress。"""
        conc = _load(SHELL_YML).get("concurrency", {})
        cancel = conc.get("cancel-in-progress")
        assert cancel is True or cancel is False or isinstance(cancel, str)
        group = conc.get("group", "")
        assert "github.workflow" in group
        assert "workflow_dispatch" in group and "dispatch" in group and "auto" in group

    def test_shell_workflow_name_unchanged(self) -> None:
        """三契约之一：workflow 显示名 'Droid Auto Review' 字节级不变。"""
        assert _load(SHELL_YML)["name"] == "Droid Auto Review"

    def test_shell_has_no_inline_shard_jobs(self) -> None:
        """VAL-B1-001：setup/plan-shards/review-shard 三 job 不在壳（单源 reusable）。"""
        jobs = _load(SHELL_YML)["jobs"]
        for banned in ("setup", "plan-shards", "review-shard"):
            assert banned not in jobs, f"壳残留内联分片 job：{banned}（单源破坏）"
        raw = SHELL_YML.read_text(encoding="utf-8")
        for pattern in SHARD_BODY_RESIDUE_PATTERNS:
            assert pattern not in raw, f"壳残留分片业务体结构：{pattern}"

    def test_shell_calls_shards_exactly_once(self) -> None:
        """VAL-B1-001：`uses: ./.github/workflows/droid-review-shards.yml` 恰 1 处。"""
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert raw.count("uses: ./.github/workflows/droid-review-shards.yml") == 1
        assert _load(SHELL_YML)["jobs"]["shards"]["uses"].endswith(
            ".github/workflows/droid-review-shards.yml"
        )

    def test_shell_forwards_budget_vars_and_dispatch_inputs(self) -> None:
        """预算参数读 vars 后显式转发；dispatch 输入透传（workflow_call inputs）。"""
        with_block = _load(SHELL_YML)["jobs"]["shards"]["with"]
        assert with_block["shard_max_files"] == "${{ vars.SHARD_MAX_FILES || '25' }}"
        assert with_block["shard_max_count"] == "${{ vars.SHARD_MAX_COUNT || '6' }}"
        assert with_block["shard_timeout_minutes"] == "${{ vars.SHARD_TIMEOUT_MINUTES || '60' }}"
        assert with_block["shard_max_parallel"] == "${{ vars.SHARD_MAX_PARALLEL || '3' }}"
        assert "preflight.outputs.pr_number" in with_block["pr_number"]
        assert "preflight.outputs.head_sha" in with_block["head_sha"]

    def test_shell_passes_all_shard_secrets_snake_form(self) -> None:
        """凭证显式传递三键（SNAKE-CONVERGENCE；漏传 = reusable 内凭证空）。

        CF 路由模式（2026-10-18）：opencode_go_key 退出注入面——Worker
        一律剥离客户端 Authorization，上游认证由 AI Gateway provider
        存储 credential 承担，CI 不携带 opencode key。
        """
        secrets_block = _load(SHELL_YML)["jobs"]["shards"]["secrets"]
        assert set(secrets_block.keys()) == {
            "factory_api_key",
            "go_github_run_token",
            "worker_ci_key",
        }
        assert secrets_block["factory_api_key"] == "${{ secrets.FACTORY_API_KEY }}"

    def test_aggregate_job_key_and_always_check(self) -> None:
        """三契约之二：聚合 job key `droid-review` 留壳；廉价门齐后 always() 真跑。"""
        agg = _load(SHELL_YML)["jobs"]["droid-review"]
        agg_if = " ".join(str(agg.get("if", "")).split())
        assert agg_if == "always() && needs.preflight.outputs.ready == 'true'"
        needs = agg.get("needs")
        assert needs == ["preflight", "shards"] or set(needs) == {"preflight", "shards"}
        assert agg.get("runs-on") == ["pve-linux"]

    def test_shards_job_gated_on_cheap_gates(self) -> None:
        """shards 仅在 preflight（ci-ok + qa-ok 双绿）后启动，禁止标签门。"""
        shards_if = " ".join(str(_load(SHELL_YML)["jobs"]["shards"].get("if", "")).split())
        assert shards_if == "needs.preflight.outputs.ready == 'true'"
        assert "processing" not in SHELL_YML.read_text(encoding="utf-8")

    def test_aggregate_permissions_unchanged(self) -> None:
        """聚合 job permissions 四面与基线一致（H1 零新增权限面）。"""
        agg = _load(SHELL_YML)["jobs"]["droid-review"]
        assert agg["permissions"] == {
            "contents": "read",
            "pull-requests": "write",
            "issues": "write",
            "actions": "write",
        }

    def test_pr_head_check_mirror_job(self) -> None:
        """Option A (#420)：native rollup 持有 head check；mirror job 观察-only，
        禁止 REST PATCH/POST（避免与 rollup 抢同名 check / 假绿进不了 rollup）。"""
        mirror = _load(SHELL_YML)["jobs"]["pr-head-check-mirror"]
        assert mirror.get("permissions") == {"checks": "write"}
        mirror_if = str(mirror.get("if", ""))
        assert "github.event_name == 'workflow_run'" in mirror_if
        runs = "\n".join(str(s.get("run", "")) for s in (mirror.get("steps") or []) if "run" in s)
        assert "skip REST mirror" in runs
        assert "--method PATCH" not in runs
        assert "--method POST" not in runs

    def test_aggregate_restores_base_checkout_and_venv(self) -> None:
        """聚合 job 基线步序：guard 前必有 workspace 残留排毒 + BASE checkout +
        setup venv（Publish findings 跑 `python3 scripts/droid_review/
        publish_findings.py`，脚本与 infra_core 包都来自 BASE checkout +
        pip install -e —— 缺任何一步，发布步在干净 workspace 必 ModuleNotFound
        或文件不存在）。"""
        agg = _load(SHELL_YML)["jobs"]["droid-review"]
        steps = agg.get("steps") or []
        names = [str(s.get("name", "")) for s in steps]
        guard_at = next(i for i, n in enumerate(names) if "Guard upstream job failures" in n)

        checkout_at = next(
            (
                i
                for i, s in enumerate(steps[:guard_at])
                if str(s.get("name", "")) == "Checkout repository (BASE)"
            ),
            None,
        )
        assert checkout_at is not None, "聚合 job guard 前缺 'Checkout repository (BASE)' 步"
        checkout = steps[checkout_at]
        assert str(checkout["with"]["ref"]) == "${{ needs.shards.outputs.base_sha }}"
        assert checkout["uses"].startswith("actions/checkout@")

        venv_at = next(
            (
                i
                for i, s in enumerate(steps[:guard_at])
                if str(s.get("name", "")) == "Setup venv (per-run isolation)"
            ),
            None,
        )
        assert venv_at is not None, "聚合 job guard 前缺 'Setup venv (per-run isolation)' 步"
        assert str(steps[venv_at]["uses"]) == "./.github/actions/setup-venv"

        guard_step_at = next(
            (
                i
                for i, s in enumerate(steps[:guard_at])
                if "Workspace guard" in str(s.get("name", ""))
            ),
            None,
        )
        assert guard_step_at is not None, "聚合 job 缺 workspace 残留排毒步（基线 ：549）"
        assert guard_step_at < checkout_at < venv_at, "排毒 → checkout → venv 步序必须与基线一致"

    def test_aggregate_artifact_pattern_unchanged(self) -> None:
        """三契约之三：artifact 前缀 `droid-review-debug-`（聚合下载 pattern 与
        reusable 上传名同一 run_id 前缀，跨 workflow artifact 命名兼容）。"""
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert "droid-review-debug-${{ github.run_id }}-shard-*" in raw
        shards_raw = SHARDS_YML.read_text(encoding="utf-8")
        assert (
            "droid-review-debug-${{ github.run_id }}-shard-${{ matrix.shard.shard_id }}"
            in shards_raw
        )

    def test_auto_resolve_chain_preserved(self) -> None:
        """auto-resolve job 仍挂聚合之后（needs droid-review + shards 数据面）；
        拆门后条件收敛为 always() + 聚合 success，零标签门。"""
        job = _load(SHELL_YML)["jobs"]["auto-resolve-threads"]
        needs = job["needs"]
        assert set(needs) == {"droid-review", "shards"}
        resolve_if = " ".join(str(job.get("if", "")).split())
        assert resolve_if == "always() && needs.droid-review.result == 'success'"


class TestAuthorizationGateSingleSource:
    """INFRA-1317 终裁：matches_token 授权判定内联四载体、字节级单源。

    形态红线（2026-10-05 两轮 P0 回归实证）：reusable workflow 形态被
    reusable 载体嵌套调用 → 全链 startup_failure（GitHub 拒绝嵌套 reusable
    调用链）；composite action 形态在零 checkout 载体上连 action.yml 都无法
    解析（run 37305718689：工作区无 .github/ 副本）。内联 run 是零 checkout
    载体唯一可解析形态——四载体脚本去缩进后逐字节一致即单源（与 triage
    heredoc 同款防漂移模式），另废止 reusable 版与 composite 目录防歧义。
    """

    def test_carriers_carry_identical_inline_script(self) -> None:
        import textwrap

        normalized = set()
        for rel in GATE_CARRIER_RELS:
            job = _load(REPO_ROOT / rel)["jobs"]["authorization-gate"]
            steps = job.get("steps") or []
            assert len(steps) == 1, f"{rel} 守卫 job 必须单 step（内联判定）"
            assert "uses" not in steps[0], f"{rel} 守卫 step 不得引用 action（零 checkout 可解析）"
            run = steps[0].get("run")
            assert isinstance(run, str) and "matches_token" in run, (
                f"{rel} 守卫 step 必须内联判定脚本"
            )
            normalized.add(textwrap.dedent(run).strip() + "\n")
        assert len(normalized) == 1, "四载体判定脚本多形态漂移（改一处必须四处同改）"

    def test_reusable_form_abolished(self) -> None:
        """P0 反例锚：reusable 版 gate workflow 不得复活（嵌套调用 startup_failure）。"""
        assert not GATE_REUSABLE_YML.exists(), (
            f"{GATE_REUSABLE_YML} 已废止（嵌套 reusable 调用致全链 startup_failure），不得复活"
        )

    def test_composite_form_abolished(self) -> None:
        """INFRA-1317 反例锚：composite 版 gate 目录不得复活（零 checkout 载体
        上 action.yml 自身无法解析，run 37305718689 实证）。"""
        assert not GATE_COMPOSITE_DIR.exists(), (
            f"{GATE_COMPOSITE_DIR} 已废止（零 checkout 载体无法解析 composite），不得复活"
        )

    def test_repo_wide_no_fifth_copy(self) -> None:
        """全仓扫描：matches_token 实现只允许在四载体与其契约测试。"""
        offenders = []
        allowed_names = {Path(rel).name for rel in GATE_CARRIER_RELS}
        for path in (REPO_ROOT / ".github").rglob("*.yml"):
            if path.name in allowed_names:
                continue
            if "matches_token" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, f"第五份拷贝回潮：{offenders}"
