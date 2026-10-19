"""VAL-DL-010 阶段2 契约：droid-review.yml 独立 auto-resolve job（PAT 路线）。

锁死 dl-ghapp-autoresolve 的 job 侧安全架构（feature 描述 + 契约测试边界）：

①  身份契约：job key 恰为 auto-resolve-threads——聚合 job key `droid-review`
   已被 check 名契约锁死，新 job 绝不得占用/改名聚合键。
②  触发契约：if 不得恒 always()——test_h1_alwayscheck_earlyexit.py 的
   "if 必须恰为 always()" 断言只作用于聚合 job，但本 job 必须带真实
   短路条件（聚合成功才 resolve），恒 always() 属契约边界违反。
③  权限契约：逐 job 显式 permissions；本 job 最小化（contents: read +
   actions: write 仅 artifact 下载面）；全 workflow 任何 job 禁 checks/
   id-token（test_workflow_permissions_contract 的全局面不因新增 job 破坏）。
④  零 checkout 契约：job 不得出现任何 actions/checkout 步——不落 PR 分支
   commit 到工作区、不执行 PR 提交中的代码（PAT 注入面最小化）；唯一允许的
   uses 步是同 run findings artifacts 下载（JSON 数据面，非代码执行面）。
⑤  门禁继承契约：发布器内建 resolve 因 GITHUB_TOKEN 被平台拒绝而失效后，
   本 job 是唯一存活的自动 resolve 器，必须继承其 P0/P1 门禁语义——本轮
   findings 的 P0/P1 path 锚定的 active bot thread 保持 unresolved；
   outdated 仍 resolve（outdated wins）；findings artifacts 缺失（docs-only/
   空计划/下载失败）时门禁不可判 → active 一律保守保留（宁漏勿错）。
⑥  白名单动作契约：脚本面仅允许 resolveReviewThread mutation；禁止任何
   REST 写动词（POST/PATCH/PUT/DELETE）与 pull-requests/issues 写意图。
⑦  幂等契约：采集只取 isResolved==false；mutation 失败 fail-open
   （::warning:: + 退出码 0），不红 check。
⑧  token 契约：GH_TOKEN 必须来自 secrets.DROID_REVIEW_RESOLVE_PAT
   （专用 PAT 通道，GITHUB_TOKEN 被平台拒绝是本 feature 的存在理由）；
   缺失时打审计 warning 并 exit 0（退化不红腿）。
⑨  防死循环契约：job 级 concurrency 组按 PR 串行 + cancel-in-progress:
   false（排队不取消，幂等腿空转收敛）；脚本零 comment/commit 产出。
⑩  审计契约：AUDIT 行携带 thread ID / pr / action / result + AUDIT-SUMMARY
   汇总行（thread 计数 / resolved / failed / gate_held 可 grep）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "droid-review.yml"

AGGREGATE_JOB_KEY = "droid-review"
AUTO_RESOLVE_JOB_KEY = "auto-resolve-threads"

# 白名单：本 job 脚本唯一允许出现的 GraphQL mutation 动作
ALLOWED_MUTATIONS = ("resolveReviewThread",)

# REST 写动词黑名单（脚本面禁止任何 REST 写意图）
REST_WRITE_VERBS = (
    "--method POST",
    "--method PATCH",
    "--method PUT",
    "--method DELETE",
    "-X POST",
    "-X PATCH",
    "-X PUT",
    "-X DELETE",
)


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _job_steps(job: dict) -> list[dict]:
    return job.get("steps") or []


def _joined_scripts(job: dict) -> str:
    return "\n".join(str(s.get("run") or "") for s in _job_steps(job))


# ---------------------------------------------------------------------------
# ① 身份契约：job key 不撞聚合键、恰存在
# ---------------------------------------------------------------------------


class TestAutoResolveJobIdentity:
    """新 job 命名边界（契约测试边界：不得命名 droid-review）。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.jobs = self.data.get("jobs", {})

    def test_job_key_is_auto_resolve_threads(self):
        """独立 auto-resolve job 以 auto-resolve-threads 存在。"""
        assert AUTO_RESOLVE_JOB_KEY in self.jobs, (
            f"job {AUTO_RESOLVE_JOB_KEY} 必须存在（VAL-DL-010 阶段2 承载体）"
        )

    def test_aggregate_job_key_untouched(self):
        """聚合 job key `droid-review` 仍存在且未被动过（check 名契约）。"""
        assert AGGREGATE_JOB_KEY in self.jobs, (
            f"聚合 job key `{AGGREGATE_JOB_KEY}` 必须存在（check 名精确契约）"
        )

    def test_auto_resolve_key_is_distinct_from_aggregate(self):
        assert AUTO_RESOLVE_JOB_KEY != AGGREGATE_JOB_KEY

    def test_new_job_key_not_used_by_other_job(self):
        """auto-resolve-threads 键恰被一个 job 使用（无重复键旁路）。"""
        job_keys = list(self.jobs.keys())
        assert job_keys.count(AUTO_RESOLVE_JOB_KEY) == 1


# ---------------------------------------------------------------------------
# ② 触发契约：if 带真实条件、依赖聚合成功
# ---------------------------------------------------------------------------


class TestAutoResolveTrigger:
    """if 不得恒 always()（契约测试边界）；resolve 只在聚合成功后跑。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})

    def test_if_is_not_bare_always(self):
        """if 不得恰为 always()——必须带聚合成功短路，防上游失败面放大。"""
        job_if = str(self.job.get("if", "")).strip()
        assert job_if != "always()", (
            f"{AUTO_RESOLVE_JOB_KEY} 的 if 不得恒 always()——必须以聚合结果短路"
            "（契约边界：恒 always 违反 test_h1_alwayscheck_earlyexit 精神）"
        )

    def test_if_short_circuits_on_aggregate_success(self):
        """if 必须引用 droid-review 的 result == 'success'。"""
        job_if = str(self.job.get("if", ""))
        assert "needs.droid-review.result" in job_if, (
            "if 必须引用 needs.droid-review.result（聚合是 thread 的事实源）"
        )
        assert "success" in job_if, "if 必须在聚合 success 时才放行"

    def test_needs_includes_aggregate(self):
        """needs 必须包含聚合 job（数据依赖：publish 先于 resolve）。"""
        needs = self.job.get("needs") or []
        needs_list = needs if isinstance(needs, list) else [needs]
        assert AGGREGATE_JOB_KEY in needs_list, (
            f"needs 必须含 {AGGREGATE_JOB_KEY}（publish 之后才 resolve）"
        )


# ---------------------------------------------------------------------------
# ③ 权限契约：最小化 + 全 workflow 面
# ---------------------------------------------------------------------------


class TestAutoResolvePermissions:
    """逐 job 显式 permissions；本 job 最小化（contents read + actions write 下载面）。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.jobs = self.data.get("jobs", {})
        self.job = self.jobs.get(AUTO_RESOLVE_JOB_KEY, {})

    def test_job_has_explicit_permissions(self):
        perms = self.job.get("permissions")
        assert isinstance(perms, dict) and perms, f"{AUTO_RESOLVE_JOB_KEY} 必须显式声明 permissions"

    def test_job_permissions_minimal_download_only(self):
        """最小权限：contents: read + actions: write（仅 artifact 下载面）。

        写能力收敛在 PAT 侧，job token 零 PR/issue 写面；actions: write 是
        download-artifact 的下载面（聚合 job 同款），非事件触发面。
        """
        assert self.job.get("permissions") == {"contents": "read", "actions": "write"}, (
            f"{AUTO_RESOLVE_JOB_KEY} permissions 必须恰为 {{contents: read, actions: write}}，"
            f"实际：{self.job.get('permissions')}"
        )

    def test_job_permissions_no_pr_write(self):
        """job token 零 pull-requests/issues 写面（PR 写能力全部收敛在 PAT 侧）。"""
        perms = self.job.get("permissions") or {}
        assert perms.get("pull-requests") in (None, "read"), (
            "job token 不得持有 pull-requests: write"
        )
        assert perms.get("issues") in (None, "read"), "job token 不得持有 issues: write"

    def test_no_checks_permission_anywhere(self):
        """全 workflow 任何 job 禁 checks 权限（既有契约不因新增 job 破坏）。"""
        for job_name, job in self.jobs.items():
            job_perms = (job or {}).get("permissions") or {}
            assert "checks" not in job_perms, f"job {job_name} 出现 checks 权限"

    def test_no_id_token_write_anywhere(self):
        """全 workflow 禁 id-token: write（publish-pypi.yml 白名单除外面）。"""
        for job_name, job in self.jobs.items():
            perms = (job or {}).get("permissions") or {}
            assert perms.get("id-token") != "write", f"job {job_name} 出现 id-token: write"


# ---------------------------------------------------------------------------
# ④ 零 checkout 契约
# ---------------------------------------------------------------------------


class TestAutoResolveZeroCheckout:
    """job 不 checkout 任何分支（不落 PR 代码到工作区、不执行 PR 提交）。

    唯一允许的 uses 步：同 run findings artifacts 下载（JSON 数据面，
    与聚合 job 同款 pin SHA——门禁源，不是代码执行面）。
    """

    ALLOWED_USES_PREFIXES = ("actions/download-artifact@",)

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})

    def test_no_checkout_steps(self):
        checkouts = [s for s in _job_steps(self.job) if "checkout" in str(s.get("uses", ""))]
        assert checkouts == [], (
            f"{AUTO_RESOLVE_JOB_KEY} 必须零 checkout 步（安全架构：不落 PR 代码），"
            f"实际：{[c.get('uses') for c in checkouts]}"
        )

    def test_uses_whitelist_only_download_artifact(self):
        """uses 步白名单封闭：仅允许 download-artifact（findings 门禁源）。"""
        uses_steps = [s for s in _job_steps(self.job) if s.get("uses")]
        offenders = [
            s.get("uses")
            for s in uses_steps
            if not any(str(s.get("uses", "")).startswith(p) for p in self.ALLOWED_USES_PREFIXES)
        ]
        assert offenders == [], (
            f"{AUTO_RESOLVE_JOB_KEY} uses 白名单（{self.ALLOWED_USES_PREFIXES}）外出现：{offenders}"
        )

    def test_download_uses_same_run_pattern_and_pinned_sha(self):
        """下载步必须锚定同 run artifact + pin SHA（与聚合 job 同款防篡改面）。"""
        steps = _job_steps(self.job)
        dl = next(
            (s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@")),
            None,
        )
        assert dl is not None, "缺少 findings artifacts 下载步（门禁源）"
        # 40 位 pin SHA
        import re

        assert re.search(r"@[0-9a-f]{40}", str(dl.get("uses"))), (
            "download-artifact 必须 pin 40 位 SHA（供应链面与聚合 job 一致）"
        )
        pattern = str((dl.get("with") or {}).get("pattern", ""))
        assert "droid-review-debug-" in pattern and "github.run_id" in pattern, (
            "下载 pattern 必须锚定同 run 的 findings artifacts"
        )

    def test_findings_path_not_executed(self):
        """findings 只读不执行：脚本面无 bash/python 执行 findings 路径的语句。"""
        script = _joined_scripts(self.job)
        # findings 文件仅允许被 open/json.loads（数据面）
        assert "bash findings" not in script and "sh findings" not in script
        assert "eval" not in script, "脚本面禁 eval（数据/代码面隔离）"


# ---------------------------------------------------------------------------
# ⑤b 门禁继承契约（P0/P1 active 保持 unresolved——VAL-DL-002 语义在 CI 侧延续）
# ---------------------------------------------------------------------------


class TestAutoResolveGateInheritance:
    """唯一存活自动 resolve 器必须继承发布器门禁语义（fail-loud 于门禁操作）。

    本轮 P0/P1 path 的 active bot thread 保持 unresolved（真门禁）；
    outdated 仍 resolve；findings artifacts 不可用时门禁不可判 → active
    一律保守保留。
    """

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})
        self.scripts = _joined_scripts(self.job)

    def test_reads_blocking_severities_from_findings(self):
        """脚本从 findings artifacts 读 P0/P1 path 集合（门禁数据源在板）。"""
        assert "P0" in self.scripts and "P1" in self.scripts, "门禁严重度 P0/P1 必须在脚本面"
        assert "blocking_paths" in self.scripts, "P0/P1 path 集合必须存在"
        assert "findings-shard-" in self.scripts, "必须消费同 run findings artifacts"

    def test_gate_held_threads_kept_unresolved(self):
        """P0/P1 path 锚定的 active thread 走 keep 分支（不进 mutation 队列）。"""
        assert "gate-held-p0p1" in self.scripts, "门禁保持分支必须带可 grep 审计标记"

    def test_outdated_still_resolved(self):
        """outdated bot thread 仍 resolve（outdated wins，发布器同款语义）。"""
        assert "isOutdated" in self.scripts, "脚本必须读取 isOutdated"
        assert '"outdated"' in self.scripts, "outdated resolve 理由必须显式审计"

    def test_active_dissolved_resolved(self):
        """active bot thread 且本轮无该位置 finding → P2/P3 已消解语义 resolve。"""
        assert "active-dissolved" in self.scripts, "已消解 resolve 理由必须显式审计"

    def test_gate_unavailable_conservative(self):
        """findings artifacts 缺失 → active thread 一律保留（宁漏勿错）。"""
        assert "gate_available" in self.scripts, "门禁可用性判定必须在板"
        assert "gate-unavailable-conservative" in self.scripts, (
            "门禁不可判保守保留分支必须带可 grep 审计标记"
        )

    def test_gate_source_download_step_precedes_resolve(self):
        """下载步（门禁源）必须先于 resolve 步。"""
        steps = _job_steps(self.job)
        dl_at = [i for i, s in enumerate(steps) if "download-artifact" in str(s.get("uses", ""))]
        rs_at = [
            i
            for i, s in enumerate(steps)
            if "Resolve stale bot review threads" in str(s.get("name", ""))
        ]
        assert len(dl_at) == 1 and len(rs_at) == 1
        assert dl_at[0] < rs_at[0], "findings 下载步必须先于 resolve 步"


# ---------------------------------------------------------------------------
# ⑥ 白名单动作契约
# ---------------------------------------------------------------------------


class TestAutoResolveWhitelistActions:
    """脚本面仅 resolveReviewThread mutation；零 REST 写动词。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})
        self.scripts = _joined_scripts(self.job)

    def test_uses_resolve_review_thread_mutation(self):
        assert "resolveReviewThread" in self.scripts, (
            "脚本必须调 resolveReviewThread mutation（白名单唯一动作）"
        )

    def test_no_other_mutation_names(self):
        """GraphQL mutation 白名单封闭：出现其他 mutation 名即违规。"""
        import re

        found = set(re.findall(r"mutation\b[^{(]*", self.scripts))
        # 提取脚本中声明的所有 mutation 语句的动词名
        declared = set(re.findall(r"\bmutation\s*\([^)]*\)\s*\{\s*(\w+)\(", self.scripts))
        assert declared, "脚本应声明至少一个 GraphQL mutation"
        assert declared <= set(ALLOWED_MUTATIONS), (
            f"白名单外 mutation：{sorted(declared - set(ALLOWED_MUTATIONS))}（仅允许 {ALLOWED_MUTATIONS}）"
        )
        assert found is not None  # 保持 lint 静默

    def test_no_rest_write_verbs(self):
        """禁 REST 写动词（POST/PATCH/PUT/DELETE）——动作面收敛在 GraphQL mutation。"""
        for verb in REST_WRITE_VERBS:
            assert verb not in self.scripts, (
                f"脚本出现 REST 写动词 {verb!r}——白名单仅 resolveReviewThread mutation"
            )

    def test_no_pull_requests_write_intent(self):
        """禁 pulls/{n}/comments 之类 REST 写端点（发布面归聚合 job，不重叠）。"""
        assert "pulls/${PR_NUMBER}/comments" not in self.scripts
        assert "/labels" not in self.scripts, "不得打 label（那是 round-cap 的面）"


# ---------------------------------------------------------------------------
# ⑥ 幂等 + fail-open 契约
# ---------------------------------------------------------------------------


class TestAutoResolveIdempotencyAndFailOpen:
    """只 resolve 未决 bot thread；失败 ::warning:: 退化不红腿。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})
        self.scripts = _joined_scripts(self.job)

    def test_collects_only_unresolved_threads(self):
        """采集过滤含 isResolved 判定（幂等：重复运行零副作用）。"""
        assert "isResolved" in self.scripts, "脚本必须读取 isResolved"
        # isResolved 为 true 的 thread 不进 mutation 队列
        assert "isResolved" in self.scripts and "continue" in self.scripts

    def test_bot_eligibility_dual_spelling(self):
        """资格线与发布器一致：双拼写 + __typename=Bot 第二道线。"""
        assert "github-actions" in self.scripts, "裸 github-actions 拼写必须在资格集内"
        assert "github-actions[bot]" in self.scripts, "github-actions[bot] 拼写必须在资格集内"
        assert "__typename" in self.scripts and "Bot" in self.scripts, (
            "__typename != Bot 的作者必须被过滤（资格误判=错误清门禁，宁漏勿错）"
        )

    def test_mutation_failure_is_warning_not_exit_1(self):
        """mutation/查询失败路径：::warning:: + 显式 sys.exit(0)，不红 check。"""
        assert "::warning::" in self.scripts, "失败退化必须打 ::warning::（退化可见）"
        assert "sys.exit(0)" in self.scripts, "失败路径必须显式 exit 0（fail-open）"

    def test_no_exit_1_in_scripts(self):
        """整脚本零 exit 1——本 job 任何失败都不得红腿（手术模板兜底在会话侧）。"""
        assert "exit 1" not in self.scripts and "sys.exit(1)" not in self.scripts, (
            "auto-resolve job 零 exit 1 契约（失败路径一律退化 + 审计）"
        )


# ---------------------------------------------------------------------------
# ⑦ token 契约：PAT 专用通道 + 缺失退化
# ---------------------------------------------------------------------------


class TestAutoResolveTokenContract:
    """GH_TOKEN 必须来自 DROID_REVIEW_RESOLVE_PAT；缺失打审计并退化。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})

    def _script_text(self) -> str:
        import json as _json

        return _json.dumps(self.job, ensure_ascii=False)

    def test_gh_token_from_pat_secret(self):
        """GH_TOKEN 恰为 secrets.DROID_REVIEW_RESOLVE_PAT（PAT 路线的存在理由）。"""
        raw = self._script_text()
        assert "secrets.DROID_REVIEW_RESOLVE_PAT" in raw, (
            "job 必须以 secrets.DROID_REVIEW_RESOLVE_PAT 作为 GH_TOKEN 源"
        )
        # 不得回落 GITHUB_TOKEN（平台拒绝调 mutation，回落=静默零生效）
        assert "secrets.GITHUB_TOKEN" not in raw, (
            "job 不得引用 secrets.GITHUB_TOKEN（integration token 被平台拒绝调 "
            "resolveReviewThread，#208 实证）"
        )

    def test_missing_pat_degrades_with_audit_marker(self):
        """PAT 缺失分支：打 pat-missing 审计标记 + exit 0（不红腿）。"""
        script = _joined_scripts(self.job)
        assert "pat-missing" in script, "PAT 缺失必须打 pat-missing 审计标记"
        assert "DROID_REVIEW_RESOLVE_PAT" in script, "缺失判定必须指向该 secret 变量"

    def test_no_pat_value_echo(self):
        """零打印 token 值（非打印管道延续到 CI 侧）。"""
        script = _joined_scripts(self.job)
        for bad in ("echo $GH_TOKEN", 'echo "$GH_TOKEN"', "print($GH_TOKEN", "env | grep GH_TOKEN"):
            assert bad not in script, f"脚本出现 token 打印风险语句：{bad}"


# ---------------------------------------------------------------------------
# ⑧ 防死循环契约：concurrency + 零 comment/commit 产出
# ---------------------------------------------------------------------------


class TestAutoResolveLoopSafety:
    """concurrency 按 PR 串行 + cancel-in-progress: false + 零回写产出。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})

    def test_job_level_concurrency_group_by_pr(self):
        conc = self.job.get("concurrency") or {}
        group = str(conc.get("group", ""))
        assert "auto-resolve-threads-" in group, "job 级 concurrency 组必须按 PR 串行"
        assert "pr_number" in group or "run_id" in group, (
            "组名必须锚定 PR（fallback run_id）——跨 PR 不互斥"
        )

    def test_concurrency_queues_without_cancel(self):
        conc = self.job.get("concurrency") or {}
        assert conc.get("cancel-in-progress") is False, (
            "cancel-in-progress 必须 false：排队不取消（幂等腿收敛为空操作，"
            "取消在飞腿会留下半批 unresolved 的中间态）"
        )

    def test_no_comment_or_commit_output(self):
        """零回写：脚本不得产 comment/commit/review——否则与聚合 job 循环互激。"""
        script = "\n".join(str(s.get("run") or "") for s in self.job.get("steps") or [])
        for banned in (
            "issues/",
            "git commit",
            "git push",
            "createReview",
            "addComment",
            "addPullRequestReview",
        ):
            assert banned not in script, (
                f"脚本出现回写产出语句 {banned!r}——本 job 必须零回写（防死循环）"
            )


# ---------------------------------------------------------------------------
# ⑨ 审计契约
# ---------------------------------------------------------------------------


class TestAutoResolveAudit:
    """逐 thread 审计行 + 汇总行（thread ID/PR 号/动作结果可 grep）。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.job = self.data.get("jobs", {}).get(AUTO_RESOLVE_JOB_KEY, {})
        self.scripts = "\n".join(str(s.get("run") or "") for s in self.job.get("steps") or [])

    def test_per_thread_audit_line(self):
        assert "AUDIT pr=" in self.scripts, "逐 thread 审计行必须带 pr 号"
        assert "action=resolveReviewThread" in self.scripts, "审计行必须记录动作名"
        assert "thread=" in self.scripts, "审计行必须记录 thread ID"

    def test_summary_audit_line_with_counts(self):
        assert "AUDIT-SUMMARY" in self.scripts, "必须有汇总审计行"
        for field in ("eligible=", "resolved=", "failed=", "gate_held="):
            assert field in self.scripts, f"汇总行必须含 {field} 计数"

    def test_audit_includes_result_field(self):
        assert "result=" in self.scripts, "审计行必须记录动作结果（success/failed）"


# ---------------------------------------------------------------------------
# ⑩ workflow 级回归：三契约 + 既有 job 不因新增 job 漂移
# ---------------------------------------------------------------------------


class TestWorkflowRegressionAfterNewJob:
    """新增 job 后的既有契约回归（三契约 + 顶层基线）。"""

    def setup_method(self):
        self.data = _load_workflow()

    def test_workflow_name_unchanged(self):
        assert self.data.get("name") == "Droid Auto Review"

    def test_top_level_permissions_baseline_unchanged(self):
        assert self.data.get("permissions") == {"contents": "read"}

    def test_aggregate_job_if_still_always(self):
        agg = self.data.get("jobs", {}).get(AGGREGATE_JOB_KEY, {})
        agg_if = " ".join(str(agg.get("if", "")).split())
        assert "always()" in agg_if
        assert "processing" in agg_if

    def test_aggregate_permissions_untouched(self):
        agg = self.data.get("jobs", {}).get(AGGREGATE_JOB_KEY, {})
        assert agg.get("permissions") == {
            "contents": "read",
            "pull-requests": "write",
            "issues": "write",
            "actions": "write",
        }
