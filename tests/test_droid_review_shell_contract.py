"""B 先行契约：droid-review.yml 瘦 Actions 壳 + 单源分片 + 本地聚合。

锁定：
- 触发面：pull_request_target（原生 head check）+ workflow_run(Quality Gate kick)
  + workflow_dispatch；过渡可双持 pull_request（零 secrets 铸造同名 check）
- shards 禁 pull_request（PR-branch yaml 不可信带 secret）
- workflow_run 仅 kick-after-qg（rerun PRT）；禁 rollup 长等 / 刮日志认 PR
- concurrency 组键强制 PR 号；禁 head_branch / github.ref 回落
- 聚合 job key+name `droid-review` 留壳本地（ruleset 23079535）
- 无 pr-head-check-mirror（禁双写）
- artifact 前缀 `droid-review-debug-`
- 凭证显式传递（SNAKE-CONVERGENCE）
- authorization-gate 四载体内联单源（INFRA-1317）
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

SHARD_BODY_RESIDUE_PATTERNS = (
    "plan_shards.py",
    "run_shard.sh",
    "Install Droid CLI",
    "Setup BYOM",
    "Upload file statuses",
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    return doc.get(True) or doc.get("on") or {}


class TestDroidReviewShellContract:
    def test_shell_triggers_b_form(self) -> None:
        """PRT + QG workflow_run kick + dispatch；过渡可双持 pull_request。"""
        triggers = _triggers(_load(SHELL_YML))
        assert "pull_request_target" in triggers
        assert "workflow_run" in triggers
        assert "workflow_dispatch" in triggers
        # #451：pull_request 用 PR-head YAML 铸造 check；合入后可摘。
        assert "pull_request" in triggers
        wr = triggers["workflow_run"]
        assert wr["workflows"] == ["Quality Gate"]
        jobs = _load(SHELL_YML)["jobs"]
        assert "kick-after-qg" in jobs
        assert "preflight" in jobs
        preflight_if = str(jobs["preflight"].get("if", ""))
        assert "pull_request_target" in preflight_if
        assert "pull_request" in preflight_if
        assert "workflow_run" not in preflight_if
        kick_if = str(jobs["kick-after-qg"].get("if", ""))
        assert "workflow_run" in kick_if
        assert "Quality Gate" in kick_if
        shards_if = str(jobs["shards"].get("if", ""))
        assert "pull_request" in shards_if
        assert "!=" in shards_if or "!= 'pull_request'" in shards_if.replace('"', "'")

    def test_kick_correlates_by_pr_not_log_scrape(self) -> None:
        """kick 用 Actions API + PR 号关联；禁刮 job 日志认 PR。"""
        kick = _load(SHELL_YML)["jobs"]["kick-after-qg"]
        runs = "\n".join(str(s["run"]) for s in (kick.get("steps") or []) if "run" in s)
        assert "event=pull_request_target" in runs
        assert "pull_requests" in runs
        assert "gh run rerun" in runs
        assert "/logs" not in runs
        assert "Cheap-gates preflight for PR #" not in runs
        assert "MAX_ATTEMPTS=200" not in runs

    def test_shell_concurrency_pr_keyed_no_main_fallback(self) -> None:
        """组键强制 PR 号；禁 head_branch / github.ref 回落；cancel 仅同组。"""
        conc = _load(SHELL_YML).get("concurrency", {})
        assert conc.get("cancel-in-progress") is True
        group = conc.get("group", "")
        assert "droid-review-pr-" in group
        assert "pull_request.number" in group or "pull_requests[0].number" in group
        assert "inputs.pr_number" in group
        assert "orphan-" in group or "run_id" in group
        assert "head_branch" not in group
        assert "github.ref" not in group

    def test_shell_workflow_name_unchanged(self) -> None:
        assert _load(SHELL_YML)["name"] == "Droid Auto Review"

    def test_shell_has_no_inline_shard_jobs(self) -> None:
        jobs = _load(SHELL_YML)["jobs"]
        for banned in ("setup", "plan-shards", "review-shard"):
            assert banned not in jobs, f"壳残留内联分片 job：{banned}"
        raw = SHELL_YML.read_text(encoding="utf-8")
        for pattern in SHARD_BODY_RESIDUE_PATTERNS:
            assert pattern not in raw, f"壳残留分片业务体结构：{pattern}"

    def test_shell_calls_shards_exactly_once(self) -> None:
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert raw.count("uses: ./.github/workflows/droid-review-shards.yml") == 1
        assert _load(SHELL_YML)["jobs"]["shards"]["uses"].endswith(
            ".github/workflows/droid-review-shards.yml"
        )

    def test_shell_forwards_budget_vars_and_dispatch_inputs(self) -> None:
        with_block = _load(SHELL_YML)["jobs"]["shards"]["with"]
        assert with_block["shard_max_files"] == "${{ vars.SHARD_MAX_FILES || '25' }}"
        assert with_block["shard_max_count"] == "${{ vars.SHARD_MAX_COUNT || '6' }}"
        assert with_block["shard_timeout_minutes"] == "${{ vars.SHARD_TIMEOUT_MINUTES || '60' }}"
        assert with_block["shard_max_parallel"] == "${{ vars.SHARD_MAX_PARALLEL || '3' }}"
        assert "preflight.outputs.pr_number" in with_block["pr_number"]
        assert "preflight.outputs.head_sha" in with_block["head_sha"]

    def test_shell_passes_all_shard_secrets_snake_form(self) -> None:
        secrets_block = _load(SHELL_YML)["jobs"]["shards"]["secrets"]
        assert set(secrets_block.keys()) == {
            "factory_api_key",
            "go_github_run_token",
            "worker_ci_key",
        }
        assert secrets_block["factory_api_key"] == "${{ secrets.FACTORY_API_KEY }}"

    def test_aggregate_job_key_name_and_always_check(self) -> None:
        """聚合 job key+display name `droid-review`；PRT/dispatch/PR 过渡 always 真跑。"""
        agg = _load(SHELL_YML)["jobs"]["droid-review"]
        assert agg.get("name") == "droid-review"
        agg_if = " ".join(str(agg.get("if", "")).split())
        assert "always()" in agg_if
        assert "pull_request_target" in agg_if
        assert "workflow_dispatch" in agg_if
        assert "pull_request" in agg_if
        assert "needs.preflight.result == 'success'" in agg_if
        needs = agg.get("needs")
        assert needs == ["preflight", "shards"] or set(needs) == {"preflight", "shards"}
        assert agg.get("runs-on") == ["pve-linux"]
        runs = "\n".join(str(s["run"]) for s in (agg.get("steps") or []) if "run" in s)
        assert "NOT_READY" in runs

    def test_shards_job_gated_on_cheap_gates(self) -> None:
        shards_if = " ".join(str(_load(SHELL_YML)["jobs"]["shards"].get("if", "")).split())
        assert "needs.preflight.outputs.ready == 'true'" in shards_if
        assert "pull_request" in shards_if
        assert "processing" not in SHELL_YML.read_text(encoding="utf-8")

    def test_aggregate_permissions_unchanged(self) -> None:
        agg = _load(SHELL_YML)["jobs"]["droid-review"]
        assert agg["permissions"] == {
            "contents": "read",
            "pull-requests": "write",
            "issues": "write",
            "actions": "write",
        }

    def test_no_mirror_job_sole_writer(self) -> None:
        """B：单一写者；禁 pr-head-check-mirror job / REST PATCH|POST check。"""
        jobs = _load(SHELL_YML)["jobs"]
        assert "pr-head-check-mirror" not in jobs
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert "\n  pr-head-check-mirror:" not in raw
        assert "name: pr-head-check-mirror" not in raw
        assert "--method PATCH" not in raw
        assert "--method POST" not in raw
        assert not (REPO_ROOT / ".github/workflows/droid-review-pr-rollup.yml").exists()

    def test_aggregate_restores_base_checkout_and_venv(self) -> None:
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
        assert guard_step_at is not None, "聚合 job 缺 workspace 残留排毒步"
        assert guard_step_at < checkout_at < venv_at, "排毒 → checkout → venv 步序必须与基线一致"

    def test_aggregate_artifact_pattern_unchanged(self) -> None:
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert "droid-review-debug-${{ github.run_id }}-shard-*" in raw
        shards_raw = SHARDS_YML.read_text(encoding="utf-8")
        assert (
            "droid-review-debug-${{ github.run_id }}-shard-${{ matrix.shard.shard_id }}"
            in shards_raw
        )

    def test_auto_resolve_chain_preserved(self) -> None:
        job = _load(SHELL_YML)["jobs"]["auto-resolve-threads"]
        needs = job["needs"]
        assert set(needs) == {"preflight", "droid-review", "shards"}
        resolve_if = " ".join(str(job.get("if", "")).split())
        assert "needs.droid-review.result == 'success'" in resolve_if
        assert "needs.preflight.outputs.ready == 'true'" in resolve_if
        # 过渡 pull_request 铸造无 shards；禁 resolve 空 EXPECTED 红腿。
        assert "github.event_name != 'pull_request'" in resolve_if


class TestAuthorizationGateSingleSource:
    """INFRA-1317 终裁：matches_token 授权判定内联四载体、字节级单源。"""

    def test_carriers_carry_identical_inline_script(self) -> None:
        import textwrap

        normalized = set()
        for rel in GATE_CARRIER_RELS:
            job = _load(REPO_ROOT / rel)["jobs"]["authorization-gate"]
            steps = job.get("steps") or []
            assert len(steps) == 1, f"{rel} 守卫 job 必须单 step（内联判定）"
            assert "uses" not in steps[0], f"{rel} 守卫 step 不得引用 action"
            run = steps[0].get("run")
            assert isinstance(run, str) and "matches_token" in run
            normalized.add(textwrap.dedent(run).strip() + "\n")
        assert len(normalized) == 1, "四载体判定脚本多形态漂移"

    def test_reusable_form_abolished(self) -> None:
        assert not GATE_REUSABLE_YML.exists()

    def test_composite_form_abolished(self) -> None:
        assert not GATE_COMPOSITE_DIR.exists()

    def test_repo_wide_no_fifth_copy(self) -> None:
        offenders = []
        allowed_names = {Path(rel).name for rel in GATE_CARRIER_RELS}
        for path in (REPO_ROOT / ".github").rglob("*.yml"):
            if path.name in allowed_names:
                continue
            if "matches_token" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, f"第五份拷贝回潮：{offenders}"
