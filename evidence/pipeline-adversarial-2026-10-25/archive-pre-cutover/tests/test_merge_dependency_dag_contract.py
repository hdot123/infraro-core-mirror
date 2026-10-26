"""防回归契约：三门承载 job + needs 链无上下文门 + 依赖图（v3.2.2 §3.5/§3.6，
F-06 / 八门⑥⑦⑧ / VAL-M1-009、VAL-M1-010）。

假绿机制回顾：job 级 if（labels/actor/draft 等 GitHub 上下文条件）产生的
SKIPPED 在 Ruleset required check 判定中等价通过——无标签 PR 因此零检查可合入
（#358 起 9 合 7 假绿，#372 实证假绿合入 main）。

test_ci_structure_contract.py 已锁 ci.yml 单文件面；本文件把同一契约推广到
三门完整承载链与依赖图：

  - ci.yml        → ci-ok、substrate-gate-suite（gate-tests job）
  - quality-gate.yml → quality-gate
  - droid-review.yml → droid-review（聚合 job，壳）+ shards 可复用链
  - droid-review-shards.yml → authorization-gate / setup / plan-shards /
    review-shard（聚合 job 的 needs 上游——任一上游被误加上下文门都会
    让聚合 check 恒 SKIPPED 或断链，属同一假绿面）

依赖图（§3.6）：ci-ok 不等 quality-gate（QG 经 API 轮询 ci-ok，非 needs）；
rerun 按 run/head 定位（watchdog rerun/cancel 面）；webhook/dispatch/merge/
triage/哨兵全链 head SHA 绑定（VAL-M1-010）。

白名单（显式豁免，设计性跳过 ≠ 标签假绿）：
  - droid-review-shards.yml 的 draft / dependabot / docs-only 门：文档类 PR
    与 dependabot 版本 PR 无需模型审查，跳过的是 review 内部分片（聚合 job
    仍无条件落 check，droid-review 的 check 名永不因豁免而消失）；
  - setup-labels.yml 不在三门承载链，天然不在本契约面。

聚合豁免不得产假 success：聚合 job（droid-review）与聚合门（ci-ok）
禁止 continue-on-error 与 needs 白名单吸收失败——上游红必须落成显式红 check。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.business_policy

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github/workflows"

# 三门承载链文件 → 承载 required check 名的 job key。
GATE_CARRIER_JOBS: dict[str, tuple[str, ...]] = {
    "ci.yml": ("ci-ok", "gate-tests"),
    "quality-gate.yml": ("quality-gate",),
    "droid-review.yml": ("droid-review",),
    # #420 Option A: native PR-head rollup (no secrets); same-source exempt
    "droid-review-pr-rollup.yml": ("droid-review",),
}

# droid-review 聚合 job 的 needs 上游（shards reusable 链）。上游被误加
# 上下文门 = 聚合 check 断链/恒 SKIPPED，属同一假绿面，一并锁定。
SHARDS_CHAIN_JOBS: tuple[str, ...] = (
    "authorization-gate",
    "setup",
    "plan-shards",
    "review-shard",
)

# 承载 job 的 if 只允许两种形态：无 if（真跑）或恒真聚合语义 always()
# （保证任何上游失败都落成显式红 check，而非隐式 SKIPPED）。
ALLOWED_CARRIER_IFS = frozenset({"", "always()"})
# droid-review 聚合：廉价门 preflight 通过后真跑（非标签 SKIPPED 假绿面）。
DROID_AGGREGATE_CHEAP_GATES_IF = "always() && needs.preflight.outputs.ready == 'true'"

# 承载 job 的 same-source 门豁免（round-1 P1-3）：gate-tests（substrate-gate-suite）
# 是执行 job 而非聚合门，按 P1-3 裁定加 same-source 信任门（fork PR 代码不
# 上共享持久 runner）。假绿面论证：fork PR 上 gate-tests skip → ci-ok 的
# needs.gate-tests.result=skipped → 显式红（success-only 判定）→ 不可合，
# skip 面不产 required-check 缺席假绿（substrate-gate-suite 的 required 实例
# 由 ci-ok 同 run 侧聚合承载判定，skip ≠ 通过）。ci-ok 自身仍恒 always()。
#
# round-2 #3（#381 终局分级）：quality-gate 同面豁免——fork PR 上 quality-gate
# skip。假绿面论证：quality-gate 是 required check 但其职责是聚合 ci/qa/substrate
# 结论；fork PR 的同源 CI 腿（ci-ok/substrate/qa）本身已 skip，无结论可聚合，
# skip 不产新增假绿（合并门禁由守卫链 REST 三门判 fail-closed 承接：无
# quality-gate 实例 → 非 success → 不可合）。精确门形态由
# TestQualityGateForkSameSourceGate 锁定。
CARRIER_SAME_SOURCE_EXEMPT = {
    ("ci.yml", "gate-tests"),
    ("quality-gate.yml", "quality-gate"),
    # PR-only thin rollup: fork → skipped (required ≠ success); BASE checkout only
    ("droid-review-pr-rollup.yml", "droid-review"),
}
# 上下文门特征串：命中即假绿面（SKIPPED ≡ 通过）。
ZERO_TOLERANCE_NEEDLES = (
    "contains(github.event.pull_request.labels",
    "contains(github.event.pull_request.head.labels",
    "github.actor",
    "github.event.pull_request.draft",
)

# shards 设计豁免白名单：唯一允许携带 draft/dependabot 门的 job 与其精确
# if 形态（F-06 显式豁免——跳过的是 review 内部分片，聚合 check 仍恒真跑）。
WHITELIST_EXEMPT_JOB = "plan-shards"
WHITELIST_EXEMPT_IF = (
    "needs.setup.outputs.draft == 'false' && needs.setup.outputs.user_login != 'dependabot[bot]'"
)

# step 级扫描（round-9 sh19 P1）：shards reusable 内 step 级 if 携带
# draft/dependabot 豁免 needle 零命中——设计豁免唯一落位 = plan-shards 的
# job 级 if；step 级同形条件 = 未披露的第二豁免落位（静默跳过 review 工作）。
# step 级 if 形态全集由 TestShardsReusableStepIfWhitelist 精确白名单锁定。
SHARDS_STEP_EXEMPT_ALLOWLIST: frozenset[str] = frozenset()

# shards reusable step 级 if 全集白名单（与 TestCarrierStepIfWhitelist 同法）：
# 键 = job#idx（_all_condition_strings 同款定位符），值 = 归一化表达式。
# 当前实际面：plan-shards#5 = Plan shards 步、plan-shards#6 = 状态上传步
# （两步均以 docs_only skip 门绕开空计划路径）、review-shard#10 = findings
# 上传步（always() = 失败诊断面也随 artifact 落盘）。
SHARDS_STEP_IF_WHITELIST: dict[str, str] = {
    "plan-shards#5": "steps.docs_only.outputs.skip != 'true'",
    "plan-shards#6": "always() && steps.docs_only.outputs.skip != 'true'",
    "review-shard#10": "always()",
}


def _load_workflow(name: str) -> dict[str, Any]:
    path = WORKFLOWS / name
    assert path.exists(), f"{name} 必须存在"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{name} 必须是 YAML mapping"
    jobs = doc.get("jobs")
    assert isinstance(jobs, dict), f"{name} 必须定义 jobs mapping"
    return jobs


def _norm_if(spec: dict[str, Any]) -> str:
    return " ".join(str(spec.get("if", "")).split())


def _job_condition_strings(jobs: dict[str, Any]) -> dict[str, str]:
    """全部 job 级 if 表达式（含 reusable uses job），空白归一。"""
    return {name: _norm_if(spec) for name, spec in jobs.items()}


def _all_condition_strings(jobs: dict[str, Any]) -> dict[str, str]:
    """round-4 P1（shard-29）：job 级 + step 级全部 if 表达式，空白归一。

    零容忍扫描面（labels/actor/draft）此前只覆盖 job 级 if——聚合门禁 job
    的「实质门禁」常在 step 级（ci-ok 的零红扫描步、quality-gate 的聚合步），
    在 always() job 里给守卫 step 加 labels 门可让 job 照常落绿 check 而门禁
    被静默跳过（#358/#372 假绿同构）。返回 {定位符: 表达式}，job 级键 =
    job 名，step 级键 = job 名#步序号（枚举索引兜底——round-8 sh19 P1：
    按「步名#?」键会让同 job 多个未命名 step 互相覆盖，靠前 step 上的
    上下文门逃逸零容忍扫描）。
    """
    out: dict[str, str] = {}
    for name, spec in jobs.items():
        out[name] = _norm_if(spec)
        for idx, step in enumerate(spec.get("steps") or []):
            if isinstance(step, dict) and "if" in step:
                out[f"{name}#{idx}"] = _norm_if(step)
    return out


class TestConditionStringKeyUniqueness:
    """扫描面键唯一性（round-8 sh19 P1）：未命名 step 不得互相覆盖。

    旧实现键 = f"{job}#{step.get('name', '?')}"——同 job 两个未命名 step 都落
    'job#?'，后者覆盖前者；labels/actor/draft 门加在靠前的未命名 step 上即
    逃逸全部零容忍扫描（#358/#372 假绿同构，round-4 P1 修复面重开）。
    键改 f"{job}#{idx}"（枚举索引）后每个带 if 的 step 都有唯一定位符。
    """

    def test_unnamed_step_ifs_are_not_overwritten(self) -> None:
        """两个未命名带 if 的 step → 两条条件都必须出现在扫描面。"""
        jobs = {
            "probe": {
                "steps": [
                    {"run": "echo one", "if": "github.event.pull_request.draft == true"},
                    {"run": "echo two", "if": "github.actor == 'evil'"},
                ]
            }
        }
        conds = _all_condition_strings(jobs)
        exprs = list(conds.values())
        assert "github.event.pull_request.draft == true" in exprs, (
            f"靠前未命名 step 的 if 被覆盖（键碰撞），扫描面: {conds}"
        )
        assert "github.actor == 'evil'" in exprs, f"靠后未命名 step 的 if 缺失，扫描面: {conds}"

    def test_mixed_named_unnamed_step_keys_are_unique(self) -> None:
        """命名与未命名 step 混排 → 键互不碰撞（索引兜底与名字空间隔离）。"""
        jobs = {
            "probe": {
                "steps": [
                    {"name": "labeled-gate", "run": "echo x", "if": "github.actor == 'a'"},
                    {"run": "echo y", "if": "github.actor == 'b'"},
                    {"name": "draft-gate", "run": "echo z", "if": "github.actor == 'c'"},
                ]
            }
        }
        conds = _all_condition_strings(jobs)
        step_keys = [k for k in conds if "#" in k]
        assert len(step_keys) == 3, f"三个带 if 的 step 必须产出三个键，实际: {step_keys}"
        assert len(set(step_keys)) == 3, f"step 级键必须互不相同，实际: {step_keys}"

    def test_real_workflows_scan_face_count_matches_if_steps(self) -> None:
        """真实 carrier workflows：扫描面条数 = job 数 + 带 if 的 step 数。"""
        for workflow in (*GATE_CARRIER_JOBS, "droid-review-shards.yml"):
            jobs = _load_workflow(workflow)
            expected = len(jobs) + sum(
                1
                for spec in jobs.values()
                for step in spec.get("steps") or []
                if isinstance(step, dict) and "if" in step
            )
            conds = _all_condition_strings(jobs)
            assert len(conds) == expected, (
                f"{workflow} 扫描面键数 {len(conds)} ≠ 期望 {expected}"
                "（存在键覆盖 = 有 step 的 if 逃逸扫描）"
            )


class TestGateCarrierJobsUnconditioned:
    """三门承载 job 的 if 零上下文门：无 if 或恒真 always()。"""

    @pytest.mark.parametrize(
        "workflow,job",
        [(wf, job) for wf, jobs in GATE_CARRIER_JOBS.items() for job in jobs],
    )
    def test_carrier_job_has_no_context_gate(self, workflow: str, job: str) -> None:
        jobs = _load_workflow(workflow)
        assert job in jobs, f"{workflow} 缺承载 job {job}"
        if (workflow, job) in CARRIER_SAME_SOURCE_EXEMPT:
            # round-1 P1-3 豁免：gate-tests 持 same-source 信任门（fork 代码
            # 不上共享 runner）；skip 经 ci-ok needs 判定显红，不产假绿面。
            # round-2 #3 扩充：quality-gate 持 same-source fork 门（fork PR 的
            # required 实例由本 workflow push 面与守卫链其他门承载；skip 必须
            # 显式登记，TestQualityGateForkSameSourceGate 锁定精确门形态）。
            job_if = _norm_if(jobs[job])
            assert "github.event.pull_request.head.repo.full_name == github.repository" in job_if
        else:
            job_if = _norm_if(jobs[job])
            if workflow == "droid-review.yml" and job == "droid-review":
                assert job_if == DROID_AGGREGATE_CHEAP_GATES_IF, (
                    f"droid-review 聚合 if 必须锁定廉价门 preflight，实际：{job_if!r}"
                )
            else:
                assert job_if in ALLOWED_CARRIER_IFS, (
                    f"{workflow}#{job} 的 if 必须为空或恒真 always()（SKIPPED≡通过假绿面），"
                    f"实际：{job_if!r}"
                )


class TestNeedsChainNoContextGates:
    """needs 链（shards reusable 全链）job 级 if 禁上下文门特征串。

    设计性豁免（draft/dependabot/docs-only）只允许以 needs.setup.outputs.*
    输出形式存在于 shards reusable 的白名单 job（plan-shards）内部，且聚合 job
    对豁免的结算必须是「显式跳过语义」而非假 success（见 TestExemptionAggregation）。
    labels / actor / 事件 draft 门零豁免——任何位置命中即红。
    """

    def test_shards_chain_jobs_no_context_gate(self) -> None:
        jobs = _load_workflow("droid-review-shards.yml")
        for job in SHARDS_CHAIN_JOBS:
            assert job in jobs, f"droid-review-shards.yml 缺链上 job {job}"
            job_if = _norm_if(jobs[job])
            if job == WHITELIST_EXEMPT_JOB:
                assert job_if == WHITELIST_EXEMPT_IF, (
                    f"白名单豁免 job {job} 的 if 漂移（只准精确持有设计门，禁止夹带其他条件）：{job_if!r}"
                )
                continue
            for needle in (*ZERO_TOLERANCE_NEEDLES, "needs.setup.outputs.draft"):
                assert needle not in job_if, (
                    f"droid-review-shards.yml#{job} 的 if 命中上下文门特征 {needle!r}：{job_if!r}"
                )

    def test_labels_gate_zero_hits_across_gate_chain(self) -> None:
        """labels 标签门全链零命中（含注释以外的一切 if 位置）。

        round-3 ⑦：ZERO_TOLERANCE_NEEDLES 的两种 needle 变体都必须应用到
        三门承载载体——旧实现只扫 `contains(...labels`，`head.labels` 形态
        （如 contains(github.event.pull_request.head.labels.*.name, 'x')）漏检，
        #358/#372 假绿向量经第二变体重开。
        round-4 P1（shard-29）：扫描面升级为 job 级 + step 级（_all_condition_
        strings）——在 always() 聚合 job 的守卫 step 上加 labels 门可让 job 照
        常落绿 check 而门禁被静默跳过，是 #358/#372 假绿同构。
        """
        for workflow in (*GATE_CARRIER_JOBS, "droid-review-shards.yml"):
            jobs = _load_workflow(workflow)
            for name, expr in _all_condition_strings(jobs).items():
                for needle in (
                    "contains(github.event.pull_request.labels",
                    "contains(github.event.pull_request.head.labels",
                ):
                    assert needle not in expr, (
                        f"{workflow}#{name} 的 if 携带标签门 {needle!r}"
                        "（#358/#372 假绿根因，零豁免）"
                    )

    def test_head_labels_gate_zero_hits_across_gate_chain(self) -> None:
        """round-3 ⑦：head.labels 变体独立扫描（显式红失败面）。

        head.labels 是 labels needle 的第二变体（ZERO_TOLERANCE_NEEDLES 已
        定义但从未应用到 carrier）——head-labels 门加到 ci-ok / quality-gate /
        droid-review 必须在本测试显红，防止第二假绿向量（#358/#372 同构）。
        round-4 P1（shard-29）：扫描面含 step 级 if。
        """
        for workflow in (*GATE_CARRIER_JOBS, "droid-review-shards.yml"):
            jobs = _load_workflow(workflow)
            for name, expr in _all_condition_strings(jobs).items():
                assert "contains(github.event.pull_request.head.labels" not in expr, (
                    f"{workflow}#{name} 的 if 携带 head.labels 变体标签门"
                    "（#358/#372 假绿向量第二形态，零豁免）"
                )
                # 直读形态同样封堵（非 contains 的 .head.labels 访问）
                assert "github.event.pull_request.head.labels" not in expr, (
                    f"{workflow}#{name} 的 if 直读 head.labels（标签门变体，零豁免）"
                )

    def test_actor_gate_zero_hits_across_gate_chain(self) -> None:
        for workflow in (*GATE_CARRIER_JOBS, "droid-review-shards.yml"):
            jobs = _load_workflow(workflow)
            for name, expr in _all_condition_strings(jobs).items():
                assert "github.actor" not in expr, (
                    f"{workflow}#{name} 的 if 携带 actor 门（作者过滤 = 定向假绿）"
                )

    def test_event_draft_gate_zero_hits_across_gate_chain(self) -> None:
        """事件上下文 draft 门（github.event.pull_request.draft）全链零命中。

        设计豁免必须经 setup 解析输出（needs.setup.outputs.draft）承载——
        直接读事件 draft 字段的门绕过了解析层，属契约外的隐蔽豁免形态。
        round-9 sh19 P1：shards 分支同步升 _all_condition_strings（job+step
        双级）——step 级事件 draft 门藏在 always() job 里可让 job 照常落
        check 而门禁被静默跳过（round-4 P1 同构逃逸向量）。
        """
        for workflow in (*GATE_CARRIER_JOBS, "droid-review-shards.yml"):
            jobs = _load_workflow(workflow)
            for name, expr in _all_condition_strings(jobs).items():
                assert "github.event.pull_request.draft" not in expr, (
                    f"{workflow}#{name} 的 if 直读事件 draft（豁免必须单源 setup 输出）"
                )

    def test_design_exemptions_confined_to_shards_reusable(self) -> None:
        """draft/dependabot 豁免只准出现在 shards reusable（设计门白名单唯一落位），
        三门承载 job 与 caller 壳禁止出现同形条件。

        round-9 sh19 P1：shards 内部豁免圈定同步升 step 级（与
        TestCarrierStepIfWhitelist 的 step 级白名单同法）——job 级圈定对
        step 级 draft/dependabot 门（藏在 setup/review-shard 任一 step 的
        if 上）全盲，未披露的第二豁免落位会静默跳过 review 工作而链条照绿。
        扫描面 = _all_condition_strings（job 级键 = job 名，step 级键 =
        job#idx）；job 级合法落位集 = [WHITELIST_EXEMPT_JOB]，step 级豁免
        needle 零命中（SHARDS_STEP_EXEMPT_ALLOWLIST = ∅，当前设计无 step 级
        豁免位；登记新落位必须连 同步 step 级 if 全集白名单）。
        """
        for workflow in GATE_CARRIER_JOBS:
            jobs = _load_workflow(workflow)
            for name, expr in _job_condition_strings(jobs).items():
                assert "needs.setup.outputs.draft" not in expr, (
                    f"{workflow}#{name} 出现 draft 豁免形态（只准在 shards reusable 内部）"
                )
                assert "dependabot" not in expr, (
                    f"{workflow}#{name} 出现 dependabot 豁免形态（只准在 shards reusable 内部）"
                )
        jobs = _load_workflow("droid-review-shards.yml")
        job_exempt = [
            name
            for name, expr in _job_condition_strings(jobs).items()
            if "needs.setup.outputs.draft" in expr or "dependabot" in expr
        ]
        assert job_exempt == [WHITELIST_EXEMPT_JOB], (
            f"shards 白名单豁免漂移（job 级）：{job_exempt}（唯一豁免位 = {WHITELIST_EXEMPT_JOB}）"
        )
        step_exempt = [
            name
            for name, expr in _all_condition_strings(jobs).items()
            if "#" in name and ("needs.setup.outputs.draft" in expr or "dependabot" in expr)
        ]
        assert set(step_exempt) == set(SHARDS_STEP_EXEMPT_ALLOWLIST), (
            f"shards 白名单豁免漂移（step 级）：{step_exempt}"
            f"（合法落位集 = {sorted(SHARDS_STEP_EXEMPT_ALLOWLIST)}）"
        )

    def test_shards_reusable_step_if_whitelist(self) -> None:
        """shards reusable step 级 if 全集精确白名单（round-9 sh19 P1 同法）。

        job 级 if 有 ALLOWED_CARRIER_IFS/豁免位锁定，step 级 if 此前无任何
        圈定——新增带豁免语义条件的 step（如给 setup 或 review-shard 的守卫
        步加 needs.setup.outputs.draft 门）即未披露豁免落位。白名单 = 当前
        实际形态集（docs-only 判定步）；新增 step 级 if 必须显式登记并附
        假绿论证。
        """
        jobs = _load_workflow("droid-review-shards.yml")
        seen: dict[str, str] = {}
        for job_name, spec in jobs.items():
            for idx, step in enumerate(spec.get("steps") or []):
                if isinstance(step, dict) and "if" in step:
                    seen[f"{job_name}#{idx}"] = _norm_if(step)
        assert seen == SHARDS_STEP_IF_WHITELIST, (
            f"shards reusable step 级 if 白名单漂移：{seen}"
            f"（合法全集 = {SHARDS_STEP_IF_WHITELIST}；新增必须显式登记）"
        )


class TestQualityGateForkSameSourceGate:
    """Round-2 P1 #3（#381 终局分级）：quality-gate 必须持 same-source fork 门。

    失效模式（62ac55c 基线）：quality-gate.yml 的 quality-gate job 无任何
    fork 门（pull_request 触发器、无 if）——fork PR 触发即在共享持久 runner
    （arc-pve-01）上 checkout PR merge ref，并执行 PR 可控的本地 composite
    action（./.github/actions/ensure-jq）与聚合脚本路径。这是合并门禁链上
    最清晰的 fork 执行缺口（droid-review 有 pull_request_target + fork
    guard；ci/qa 执行 job 有 same-source 门；唯 quality-gate 裸奔）。

    修复形态与 ci.yml 执行 job 同构：`github.event_name != 'pull_request' ||
    (same-source)`——push 恒放行（required check 双触发面），fork PR 的
    pull_request 面显式 skip。skip 面的假绿豁免论证（test_carrier_job_has_no_context_gate
    的 CARRIER_SAME_SOURCE_EXEMPT 同款）：本测试即唯一豁免登记位。
    """

    QG_SAME_SOURCE_IF = (
        "github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.full_name == github.repository"
    )

    def test_quality_gate_job_carries_same_source_gate(self) -> None:
        jobs = _load_workflow("quality-gate.yml")
        assert "quality-gate" in jobs, "quality-gate.yml 缺承载 job"
        job_if = _norm_if(jobs["quality-gate"])
        assert job_if == self.QG_SAME_SOURCE_IF, (
            "quality-gate job 必须持 same-source fork 门（#3：fork PR 不得在共享"
            f"持久 runner 上执行 PR 可控 composite action/脚本），实际：{job_if!r}"
        )

    def test_fork_skip_is_registered_as_design_exemption(self) -> None:
        """fork skip 的假绿豁免必须在 CARRIER_SAME_SOURCE_EXEMPT 登记位显式存在。"""
        assert ("quality-gate.yml", "quality-gate") in CARRIER_SAME_SOURCE_EXEMPT, (
            "quality-gate 的 fork skip 必须登记进 CARRIER_SAME_SOURCE_EXEMPT（唯一"
            "豁免登记位），防止隐性豁免漂移"
        )

    def test_fork_skip_is_backed_by_independent_required_gate(self) -> None:
        """fork skip 可接受的前提：独立 required 门禁 fail-closed 仍拦截。"""
        shards_yml = (WORKFLOWS / "droid-review-shards.yml").read_text(encoding="utf-8")
        assert "pull_request.head.repo.full_name" in shards_yml
        assert "github.repository" in shards_yml
        assert "fork PR rejected" in shards_yml
        assert "exit 1" in shards_yml

        droid = _load_workflow("droid-review.yml")
        agg = droid["droid-review"]
        agg_run = "\n".join(
            str(s.get("run", "")) for s in (agg.get("steps") or []) if isinstance(s, dict)
        )
        assert "SHARDS_RESULT" in agg_run
        assert "success" in agg_run and "skipped" in agg_run
        assert "exit 1" in agg_run


class TestDroidReviewPrRollupSameSourceGate:
    """#420 Option A：PR-head 原生 rollup 持 same-source；fork → skipped≠success。"""

    ROLLUP_IF = (
        "github.event.pull_request.head.repo.full_name == github.repository && "
        "github.event.pull_request.state == 'open'"
    )

    def test_rollup_job_carries_same_source_gate(self) -> None:
        jobs = _load_workflow("droid-review-pr-rollup.yml")
        assert "droid-review" in jobs
        assert _norm_if(jobs["droid-review"]) == self.ROLLUP_IF

    def test_fork_skip_is_registered_as_design_exemption(self) -> None:
        assert ("droid-review-pr-rollup.yml", "droid-review") in CARRIER_SAME_SOURCE_EXEMPT


class TestCarrierStepIfWhitelist:
    """承载 job 的 step 级 if 正向白名单（round-4 P1 #9 补充）。"""

    # tuple 而非 set：str hash 随 PYTHONHASHSEED 变化，set 迭代序在 xdist 各
    # worker 间不一致 → parametrize 收集顺序漂移（"Different tests were
    # collected" 使整个会话收集失败）。parametrize 参数集必须用有序容器。
    NO_STEP_IF_JOBS = (
        ("ci.yml", "ci-ok"),
        ("ci.yml", "gate-tests"),
        ("quality-gate.yml", "quality-gate"),
        ("droid-review-pr-rollup.yml", "droid-review"),
    )
    DROID_STEP_IF_ALLOWED = frozenset({"steps.check_results.outputs.should_publish == 'true'"})

    @pytest.mark.parametrize("workflow,job", list(NO_STEP_IF_JOBS))
    def test_carrier_jobs_steps_have_no_if(self, workflow: str, job: str) -> None:
        jobs = _load_workflow(workflow)
        spec = jobs[job]
        for step in spec.get("steps") or []:
            if isinstance(step, dict):
                assert "if" not in step, (
                    f"{workflow}#{job} 步骤 {step.get('name', '?')} 禁止 step 级 if"
                    "（新增 step 级 if 必须同步登记假绿论证）"
                )

    def test_droid_review_step_if_whitelist(self) -> None:
        jobs = _load_workflow("droid-review.yml")
        spec = jobs["droid-review"]
        seen: set[str] = set()
        for step in spec.get("steps") or []:
            if isinstance(step, dict) and "if" in step:
                seen.add(_norm_if(step))
        assert seen == self.DROID_STEP_IF_ALLOWED, (
            f"droid-review 聚合 job step 级 if 必须精确等于白名单，实际：{seen!r}"
        )


class TestExemptionAggregation:
    """聚合不得产假 success：豁免造成的 skipped 不许被吸收成绿。"""

    def test_aggregation_job_always_runs(self) -> None:
        """droid-review 聚合：preflight 双绿后 always 语义真跑 check。"""
        jobs = _load_workflow("droid-review.yml")
        assert _norm_if(jobs["droid-review"]) == DROID_AGGREGATE_CHEAP_GATES_IF

    def test_aggregation_has_no_continue_on_error(self) -> None:
        """聚合 job 禁 continue-on-error：上游红必须落红 check（吸收失败 = 假绿）。"""
        jobs = _load_workflow("droid-review.yml")
        spec = jobs["droid-review"]
        assert "continue-on-error" not in spec, "聚合 job 禁止 continue-on-error"
        for step in spec.get("steps") or []:
            assert "continue-on-error" not in step, (
                f"聚合 job 步骤 {step.get('name', '?')} 禁止 continue-on-error"
            )

    def test_ci_ok_has_no_continue_on_error(self) -> None:
        """ci-ok（零红聚合门）自身与全部步骤禁 continue-on-error。"""
        jobs = _load_workflow("ci.yml")
        spec = jobs["ci-ok"]
        assert "continue-on-error" not in spec
        for step in spec.get("steps") or []:
            assert "continue-on-error" not in step, (
                f"ci-ok 步骤 {step.get('name', '?')} 禁止 continue-on-error"
            )

    def test_upstream_failure_becomes_explicit_red(self) -> None:
        """聚合 job 的上游失败必须转显式失败：guard 步引用 needs.shards.result
        且非 success/skipped 即 exit 1——shards 断链/失败不允许静默落绿 check。"""
        jobs = _load_workflow("droid-review.yml")
        runs = [
            str(step["run"]) for step in jobs["droid-review"].get("steps") or [] if "run" in step
        ]
        script = "\n".join(runs)
        assert "needs.shards.result" in script or "SHARDS_RESULT" in script, (
            "聚合 job 必须检视 needs.shards.result"
        )
        assert "::error::" in script, "上游失败必须产显式 ::error:: 注解（说明性红）"

    def test_review_shard_failure_fails_closed(self) -> None:
        """shards 内部：分片失败必须 fail-closed（exit 1），禁止吞错落绿。

        round-3 ⑧：continue-on-error 禁令改查 job/step dict（sibling 271/281
        同法）——旧实现查 run 脚本文本，而 continue-on-error 是 YAML 键、永不
        出现在 shell 文本里：job 级或 step 级 `continue-on-error: true` 加到
        review-shard 上旧断言恒绿，shard 失败被吸收成绿 droid-review check
        （正是本测试声称要堵的吸收失败假绿）。
        """
        jobs = _load_workflow("droid-review-shards.yml")
        spec = jobs["review-shard"]
        assert spec, "review-shard job 必须存在"
        # job 级禁 continue-on-error（dict 语义检查，非脚本文本）
        assert "continue-on-error" not in spec, "review-shard job 级禁止 continue-on-error"
        for step in spec.get("steps") or []:
            assert "continue-on-error" not in step, (
                f"review-shard 步骤 {step.get('name', '?')} 禁止 continue-on-error"
                "（吸收失败 = 假绿，round-3 ⑧）"
            )
        # fail-closed 语义保留：失败路径显式 exit 1
        runs = [str(step["run"]) for step in spec.get("steps") or [] if "run" in step]
        assert runs, "review-shard 必须有 run 步骤"
        script = "\n".join(runs)
        assert "exit 1" in script, "review-shard 失败路径必须 fail-closed（exit 1）"


class TestCarrierCheckNameStability:
    """承载 check 名与 ruleset required context 精确对应（名变 = 永久 pending）。"""

    @pytest.mark.parametrize(
        "workflow,job,check_name",
        [
            ("ci.yml", "gate-tests", "substrate-gate-suite"),
            ("quality-gate.yml", "quality-gate", "quality-gate"),
            ("droid-review.yml", "droid-review", "droid-review"),
            ("droid-review-pr-rollup.yml", "droid-review", "droid-review"),
            ("ci.yml", "ci-ok", "ci-ok"),
        ],
    )
    def test_check_name_via_display_name(self, workflow: str, job: str, check_name: str) -> None:
        jobs = _load_workflow(workflow)
        display = jobs[job].get("name", job)
        assert display == check_name, (
            f"{workflow}#{job} 显示名漂移：{display!r} != {check_name!r}（ruleset 按显示名匹配）"
        )


# ───────────────────────── §3.6 依赖图检查（DAG 契约） ─────────────────────────


def _all_job_needs(jobs: dict[str, Any]) -> list[str]:
    """展平某 workflow 全部 job 的 needs 名单。"""
    needs: list[str] = []
    for spec in jobs.values():
        raw = spec.get("needs") or []
        if isinstance(raw, str):
            needs.append(raw)
        else:
            needs.extend(str(item) for item in raw)
    return needs


class TestMergeDependencyDag:
    """§3.6：ci-ok 不等待 quality-gate（跨 workflow 依赖禁止 needs 化）。"""

    def test_ci_ok_does_not_wait_quality_gate(self) -> None:
        """ci.yml 任何 job 的 needs 不得引用 quality-gate——QG 是独立 workflow 的
        check（跨 workflow 无 needs 语义），错误等待会形成轮询死锁（#248 实证：
        QG 等 ci-ok 零红扫描、ci-ok 内嵌 droid-review 轮询超窗，QG 超时失败反毒
        ci-ok）。二者的唯一合法关系是 QG 用 API 轮询 ci-ok 的 check-run。"""
        jobs = _load_workflow("ci.yml")
        for job_name in jobs:
            raw = jobs[job_name].get("needs") or []
            need_list = [raw] if isinstance(raw, str) else [str(n) for n in raw]
            assert "quality-gate" not in need_list, (
                f"ci.yml#{job_name} 的 needs 引用 quality-gate（跨 workflow 依赖死锁面）"
            )

    def test_quality_gate_polls_ci_ok_not_needs(self) -> None:
        """quality-gate 聚合 ci-ok 走 API 轮询（check-runs 查询），不是 needs。"""
        jobs = _load_workflow("quality-gate.yml")
        assert "ci-ok" not in _all_job_needs(jobs)
        runs = [
            str(step["run"]) for step in jobs["quality-gate"].get("steps") or [] if "run" in step
        ]
        script = "\n".join(runs)
        assert "commits/${COMMIT_SHA}/check-runs" in script, (
            "quality-gate 必须经 check-runs API 轮询聚合 ci-ok（非 needs）"
        )

    def test_ci_ok_does_not_poll_droid_review(self) -> None:
        """ci-ok 不再轮询 droid-review（廉价门先绿再审；Droid 由 workflow_run 触发）。"""
        jobs = _load_workflow("ci.yml")
        runs = [str(step["run"]) for step in jobs["ci-ok"].get("steps") or [] if "run" in step]
        script = "\n".join(runs)
        assert "check_droid_review.sh" not in script, (
            "ci-ok 不得再内嵌 droid-review 轮询（与 Droid workflow_run 调度解耦）"
        )


class TestRerunPerHead:
    """§3.6：rerun 一律按 run/head 定位，禁止裸 github.sha 参与重跑路径。"""

    WORKFLOW_RERUN = REPO_ROOT / ".github/workflows/droid-review-watchdog.yml"
    HANDLERS_RERUN = REPO_ROOT / ".github/workflows/droid-review-watchdog-handlers.yml"

    def test_watchdog_rerun_targets_upstream_run_id(self) -> None:
        """watchdog 的 rerun-failed-jobs 以终态上游 run id 为目标
        （github.event.workflow_run.id / inputs.run_id）——rerun 的是「那一轮」，
        head 变更由新 push 的新 run 承载，两者不得混用。"""
        text = self.WORKFLOW_RERUN.read_text(encoding="utf-8")
        assert "runs/$RUN_ID/rerun-failed-jobs" in text
        assert "github.event.workflow_run.id" in text
        handlers = self.HANDLERS_RERUN.read_text(encoding="utf-8")
        assert "runs/$RUN_ID/rerun-failed-jobs" in handlers
        # reusable 执行体不依赖事件上下文流动：run id 由 caller 以 inputs 显式转发
        assert "RUN_ID: ${{ inputs.run_id }}" in handlers

    def test_watchdog_cancel_scopes_by_head_sha(self) -> None:
        """cancel 腿按 head SHA 圈定 in-progress run（runs?head_sha=）——
        只取消「当前 head」的 review，不误伤新 head 的在跑 run。"""
        text = self.WORKFLOW_RERUN.read_text(encoding="utf-8")
        assert "runs?head_sha=$HEAD_SHA&status=in_progress" in text
        assert "runs?head_sha=$HEAD_SHA&status=queued" in text

    def test_rerun_paths_no_bare_github_sha(self) -> None:
        """rerun/cancel 执行体禁用裸 github.sha 定位目标（workflow_run 事件里
        github.sha 是上游默认分支头，不是 PR head——用错即重跑错对象）。"""
        for path in (self.WORKFLOW_RERUN, self.HANDLERS_RERUN):
            text = path.read_text(encoding="utf-8")
            assert "rerun-failed-jobs" in text
            lines = [
                line
                for line in text.splitlines()
                if "rerun-failed-jobs" in line or "head_sha=$HEAD_SHA" in line
            ]
            for line in lines:
                assert "github.sha" not in line, (
                    f"{path.name} rerun/cancel 路径引用裸 github.sha：{line.strip()!r}"
                )


class TestHeadShaBinding:
    """§3.6 / VAL-M1-010：webhook/dispatch/merge/rerun 全部取当前 head SHA。

    head SHA 绑定是防假绿的最后一道锚：绿判、通知、合并、重跑若锚在旧 SHA，
    「旧 head 绿 → 新 head 未验」的窗口立刻 reopen（#372 形态）。"""

    def _carrier_script(self, workflow: str, job: str) -> str:
        jobs = _load_workflow(workflow)
        runs = [str(step["run"]) for step in jobs[job].get("steps") or [] if "run" in step]
        assert runs, f"{workflow}#{job} 必须有 run 步骤"
        return "\n".join(runs)

    def test_webhook_payload_bound_to_head(self) -> None:
        """webhook 通知（ci.yml notify）payload sha = PR head SHA。"""
        script = self._carrier_script("ci.yml", "notify-ci-complete")
        assert '--arg sha "${{ github.event.pull_request.head.sha || github.sha }}"' in script

    def test_quality_gate_aggregation_bound_to_head(self) -> None:
        """quality-gate 聚合基准 COMMIT_SHA = PR head SHA（push 面兜底 github.sha）。"""
        doc = yaml.safe_load((WORKFLOWS / "quality-gate.yml").read_text(encoding="utf-8"))
        env = doc["jobs"]["quality-gate"]["steps"][2]["env"]
        assert env["COMMIT_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"

    def test_merge_action_binds_head_sha_before_gates(self) -> None:
        """合并执行体（action.yml）：HEAD_SHA 从 PR_INFO 的 headRefOid 现取
        （当前 head），三门 REST 绿判与零红扫描都在该 SHA 上判——禁止合并旧头。"""
        action_text = (REPO_ROOT / "actions/auto-merge/action.yml").read_text(encoding="utf-8")
        assert "HEAD_SHA=$(echo \"$PR_INFO\" | jq -r '.headRefOid')" in action_text
        first_gate = action_text.index("HEAD_SHA=")
        three_gate = action_text.index("Checking required three gates (REST)")
        assert first_gate < three_gate, "HEAD_SHA 必须先于三门绿判取定"

    def test_dispatch_head_sha_falls_back_to_api(self) -> None:
        """droid-review dispatch 路径：显式 head_sha 优先，缺省回退 API 当前
        .head.sha——禁用任何「重放旧 SHA」的缺省。"""
        script = self._carrier_script("droid-review-shards.yml", "setup")
        assert 'HEAD_SHA="${{ inputs.head_sha }}"' in script
        assert "jq -r '.head.sha'" in script

    def test_triage_green_verdict_bound_to_head(self) -> None:
        """pipeline triage：取数含 headRefOid，REST 富集与绿判都在该 head 上。"""
        pipeline_text = (WORKFLOWS / "auto-merge-pipeline.yml").read_text(encoding="utf-8")
        assert "headRefOid" in pipeline_text
        assert "commits/$HEAD_SHA/check-runs" in pipeline_text
