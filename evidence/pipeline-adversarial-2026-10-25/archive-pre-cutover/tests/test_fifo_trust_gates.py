"""FIFO 双保险 + autofix 信任门契约测试（v3.2.2 §3.3/§3.4，VAL-M1-005/007）。

FIFO（§3.4，VAL-M1-005）：拆 processing 标签墙后合并串行化靠「截断 + 单飞」
双保险，二者必须同时成立：
- resolve 步在输出 pr_numbers 之前完成 behind 截断（按 created_at 只留最老
  1 个），禁止只在 matrix 腿内截断而输出未截断列表；
- merge matrix max-parallel:1（修订对 max-parallel:5 的旧 pin）。

autofix 信任门（§3.3，VAL-M1-007）：droid-autofix 仅同仓 PR 触发（job 级
if，workflow_run 对象实有 head_repository 字段）；作者 association ∈
{OWNER, MEMBER, COLLABORATOR} 白名单在 ctx 步统一 fail-closed 执行
（REST pulls 是唯一实有 author_association 的载体——workflow_run 对象
没有该字段，job 级 if 引用即静默放行；gh pr view --json 也不暴露），
dispatch 与 workflow_run 双路径共享同一道门；PR body 仅作上下文摘要喂
模型，绝不解析为命令。

附加（M1-A review 遗留，VAL-AF-003）：autofix 的 notify-only 失败过滤按
job 显示名精确匹配，与 ci.yml notify job name 的映射此前无契约锁定——本
文件锁定两侧字面一致，防任一侧重命名漂移（过滤静默失效 → 送达失败被当
代码失败误修）。

行为测试（triage default 模式跑真实脚本）：多 PR 双向输入下 behind 按
created_at 只留最老 1 个、其余剔除，且禁止原地突变输入（纯函数语义）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_YML = REPO_ROOT / ".github" / "workflows" / "auto-merge-pipeline.yml"
TRIAGE_SH = REPO_ROOT / "src" / "infra_core" / "shell" / "auto_merge_triage.sh"
AUTOFIX_YML = REPO_ROOT / ".github" / "workflows" / "droid-autofix.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

NOTIFY_JOB_NAME = "Notify CI complete to webhook gateway"
THREE_GATES = ("substrate-gate-suite", "quality-gate", "droid-review")


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{path.name} 必须是 YAML mapping"
    return doc


def _pipeline() -> dict[str, Any]:
    return _load(PIPELINE_YML)


def _autofix() -> dict[str, Any]:
    return _load(AUTOFIX_YML)


def _autofix_step_by_id(step_id: str) -> dict[str, Any]:
    steps = _autofix()["jobs"]["autofix"]["steps"]
    return next(s for s in steps if s.get("id") == step_id)


def _resolve_script() -> str:
    """resolve job 的 id=resolve 步骤 run 脚本（fleet 截断载体）。"""
    steps = _pipeline()["jobs"]["resolve"]["steps"]
    resolve_steps = [s for s in steps if s.get("id") == "resolve"]
    assert len(resolve_steps) == 1, "resolve job 必须恰含一个 id=resolve 步骤"
    return resolve_steps[0]["run"]


def _pr(
    number: int,
    *,
    merge_state: str = "CLEAN",
    created_at: str = "2026-01-01T00:00:00Z",
    labels: list[str] | None = None,
    fork: bool = False,
) -> dict[str, Any]:
    """构造 gh pr view 取数形态的 PR 对象（REST 三门全绿背景）。

    类别仅由 mergeStateStatus 驱动：CLEAN → mergeable（全门绿），
    BEHIND → behind。createdAt 是 resolve fleet 截断的排序键（gh pr list
    --json createdAt 字段）。fork=True 模拟跨仓 PR（isCrossRepository），
    是 behind 截断资格过滤（round-1 P1-2）的判定字段。
    """
    check_runs = {
        "total_count": len(THREE_GATES),
        "check_runs": [
            {
                "id": idx + 1,
                "name": gate,
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-10-19T00:00:00Z",
            }
            for idx, gate in enumerate(THREE_GATES)
        ],
    }
    return {
        "number": number,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": merge_state,
        "isDraft": False,
        "statusCheckRollup": [
            {"name": gate, "status": "completed", "conclusion": "success"} for gate in THREE_GATES
        ],
        "labels": [{"name": name} for name in (labels or [])],
        "headRefOid": f"{number:040x}",
        "checkRuns": check_runs,
        "createdAt": created_at,
        "isCrossRepository": fork,
    }


def _run_triage(prs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """对（已 resolve 的）PR 列表跑 triage default 模式，返回分类 JSON。"""
    result = subprocess.run(
        ["bash", str(TRIAGE_SH)],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"triage 失败: {result.stderr}"
    return json.loads(result.stdout)


class TestFifoDoubleInsurance:
    """v3.2.2 §3.4（VAL-M1-005）：截断 + max-parallel:1 双保险同时成立。"""

    def test_resolve_truncates_behind_before_output(self) -> None:
        """resolve 步在输出 pr_numbers 之前完成 behind 截断（按 created_at 留最老 1 个）。"""
        script = _resolve_script()
        # 截断三要素：behind 识别、created_at 排序、只留 1 个
        assert "BEHIND" in script, "resolve 步必须识别 behind 类 PR"
        assert ".createdAt" in script, "resolve 截断必须按 created_at 排序（FIFO）"
        assert "[0:1]" in script, "behind 截断必须只留 1 个"
        # 禁止「输出未截断列表后在各腿内截断」：resolve 步内联 triage 执行体
        # 即意味着分类被推迟到 matrix 腿——截断必须 precede 输出，而非腿内补救。
        assert "TRIAGE_EOF" not in script, "resolve 步禁止内联 triage heredoc"
        assert "auto_merge_triage.sh" not in script, "resolve 步禁止引用 triage 脚本文件"
        # 截断必须发生在 pr_numbers 写入 job output 之前
        echo_line = 'pr_numbers=$PRS" >> "$GITHUB_OUTPUT"'
        assert echo_line in script, "resolve 步必须显式输出 pr_numbers"
        assert script.index("[0:1]") < script.index(echo_line), (
            "behind 截断必须先于 pr_numbers 输出（禁止输出未截断列表）"
        )

    def test_merge_matrix_max_parallel_is_one(self) -> None:
        """merge matrix max-parallel:1（FIFO 单飞腿；修订旧 pin 5）。"""
        strategy = _pipeline()["jobs"]["auto-merge"]["strategy"]
        assert strategy["max-parallel"] == 1, (
            "FIFO 双保险之单飞腿：max-parallel 必须为 1（修订对 5 的旧 pin）"
        )
        assert strategy["fail-fast"] is False, "串行腿不得因单腿失败放弃队列"

    def test_triage_resolvable_prs_truncates_behind_to_oldest(self) -> None:
        """行为测试：多 PR 输入下 behind 按 created_at 只留最老 1 个，其余剔除。

        截断对象 = behind 类（VAL-M1-005 契约）；mergeable 候选不截断，
        由 merge matrix max-parallel:1 串行执行（双保险另一腿）。
        """
        oldest = _pr(1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z")
        middle = _pr(2, merge_state="BEHIND", created_at="2026-06-01T00:00:00Z")
        newest = _pr(3, merge_state="BEHIND", created_at="2026-12-01T00:00:00Z")
        candidate = _pr(9)
        # 输入乱序：最老的不在首位（排序必须按 createdAt 而非输入顺序）
        out = _run_triage([newest, candidate, middle, oldest])
        assert [p["number"] for p in out["mergeable"]] == [9]
        assert [p["number"] for p in out["behind"]] == [1], (
            "behind 类必须按 created_at 只留最老 1 个（其余剔除，等待后续 sweep）"
        )

    def test_triage_mergeable_candidates_not_truncated(self) -> None:
        """mergeable 候选不做 created_at 截断（串行由 max-parallel:1 保证）。"""
        a = _pr(10, created_at="2026-01-01T00:00:00Z")
        b = _pr(11, created_at="2026-06-01T00:00:00Z")
        out = _run_triage([b, a])
        assert sorted(p["number"] for p in out["mergeable"]) == [10, 11]

    def test_triage_multiple_behind_only_kept_pr_has_update_branch(self) -> None:
        """被剔除的 behind 候选不产生任何动作（update-branch 单飞截断后成立）。"""
        older = _pr(1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z")
        newer = _pr(2, merge_state="BEHIND", created_at="2026-06-01T00:00:00Z")
        out = _run_triage([newer, older])
        assert [p["number"] for p in out["behind"]] == [1]
        assert all(p["action"] == "update-branch" for p in out["behind"])

    def test_triage_resolvable_prs_single_behind_untouched(self) -> None:
        """单个 behind PR：截断是 no-op，原样保留（不误删唯一候选）。"""
        only = _pr(1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z")
        out = _run_triage([only])
        assert [p["number"] for p in out["behind"]] == [1]

    def test_triage_resolvable_prs_does_not_mutate_input(self) -> None:
        """截断必须产新列表：禁止原地突变输入数组（跨载体共享数据）。"""
        prs = [
            _pr(3, merge_state="BEHIND", created_at="2026-12-01T00:00:00Z"),
            _pr(1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z"),
        ]
        snapshot = json.loads(json.dumps(prs))
        _run_triage(prs)
        assert prs == snapshot, "triage 必须是纯函数：禁止原地删除/重排输入元素"

    def test_triage_resolvable_prs_preserves_actions_on_kept_pr(self) -> None:
        """被保留的最老 behind 仍带 update-branch 动作字段（不因截断丢失）。"""
        older = _pr(1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z")
        newer = _pr(2, merge_state="BEHIND", created_at="2026-06-01T00:00:00Z")
        out = _run_triage([newer, older])
        assert out["behind"][0]["action"] == "update-branch"

    def test_truncation_eligibility_filters_do_not_merge_and_forks(self) -> None:
        """round-1 P1-2：截断前必须过滤 do-not-merge / fork 等 update-branch
        资格不符的候选。

        旧语义：截断面向全部 behind 类——最老的 PR 若持 do-not-merge（或为
        fork，update-branch 需 head 仓写权限），它永远占据 behind=1 槽位且
        自身不可 update-branch，后续可收敛的 behind 候选被无限期饿死。
        终态语义：先按资格过滤（do-not-merge / fork 剔出截断面），再在可
        update-branch 的候选中按 created_at 截 behind=1。被过滤者不产动作
        （维持自身分类），仅不占 FIFO 槽位。
        """
        prs = [
            _pr(
                1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z", labels=["do-not-merge"]
            ),
            _pr(2, merge_state="BEHIND", created_at="2026-06-01T00:00:00Z"),
            _pr(3, merge_state="BEHIND", created_at="2026-12-01T00:00:00Z", fork=True),
        ]
        out = _run_triage(prs)
        # #1 do-not-merge：自身类别不变（逃生舱优先级最高），不占 behind 槽
        assert [p["number"] for p in out["do_not_merge"]] == [1]
        # #3 fork：BEHIND 分类不变，但不可 update-branch（资格过滤，不占槽）
        assert [p["number"] for p in out["behind"]] == [2], (
            "截断必须在资格过滤后进行：do-not-merge/fork 不占 behind=1 槽位，可收敛的 #2 才能被选出"
        )

    def test_truncation_all_candidates_ineligible_keeps_no_behind_slot(self) -> None:
        """全部 behind 候选资格不符时：不产生 update-branch 动作（无槽可选）。"""
        prs = [
            _pr(
                1, merge_state="BEHIND", created_at="2026-01-01T00:00:00Z", labels=["do-not-merge"]
            ),
            _pr(2, merge_state="BEHIND", created_at="2026-02-01T00:00:00Z", fork=True),
        ]
        out = _run_triage(prs)
        assert out["behind"] == [], "资格全不符时 behind 槽位必须为空（无候选可 update-branch）"
        assert sorted(p["number"] for p in out["do_not_merge"]) == [1]
        assert out["unknown"] == [], "fork BEHIND 候选按 behind 分类（资格过滤只影响截断面）"

    def test_fork_flag_carried_through_fetch_contract(self) -> None:
        """fork 资格判定的取数契约：resolve 截断腿必须取回 fork 标志字段。

        gh pr list 的 isCrossRepository（布尔）是 fork 判定字段——与
        mergeStateStatus/createdAt 同一次取回。resolve 步 fleet 取数缺该
        字段时截断腿无法过滤 fork 候选（P1-2 的取数半边）。
        """
        script = _resolve_script()
        assert "isCrossRepository" in script, (
            "resolve fleet 取数缺 isCrossRepository（fork 资格过滤失效）"
        )
        assert "do-not-merge" in script or "do_not_merge" in script, (
            "resolve 截断缺 do-not-merge 资格过滤（最老不可收敛 PR 永占 behind 槽）"
        )


class TestAutofixTrustGate:
    """v3.2.2 §3.3（VAL-M1-007）：droid-autofix 信任门。"""

    def _job_if(self) -> str:
        cond = _autofix()["jobs"]["autofix"].get("if") or ""
        assert cond, "autofix job 必须保留门禁 if（自动开火默认关）"
        return cond

    def test_job_if_gates_same_repo(self) -> None:
        """workflow_run 自动路径仅同仓 PR 触发（fork 一律不自动修）。"""
        cond = self._job_if()
        assert "github.event.workflow_run.head_repository.full_name == github.repository" in cond, (
            "信任门缺失：job 级 if 必须绑定 head_repository.full_name == github.repository"
        )

    def test_author_association_not_in_job_if(self) -> None:
        """association 白名单禁止放 job 级 if（workflow_run 对象无此字段）。

        workflow_run 对象（octokit payload 与 REST runs 双源实证）没有
        author_association：缺失属性在表达式中求值为空串，而
        contains('OWNER MEMBER COLLABORATOR', '') == true——放在 job 级
        if 的白名单会静默放行而非拦截。校验必须由 ctx 步在 REST 载体上
        执行（test_ctx_gate_rest_carrier_fail_closed）。
        """
        assert "author_association" not in self._job_if(), (
            "workflow_run 对象无 author_association 字段，job 级 if 引用即静默放行；"
            "association 校验必须在 ctx 步用 REST pulls 载体执行"
        )

    def test_ctx_gate_rest_carrier_fail_closed(self) -> None:
        """association 白名单在 ctx 步统一执行：REST 载体 + fail-closed + 双路径共享。

        载体契约：REST pulls 端点的 author_association 是唯一实有字段
        （workflow_run 对象没有；gh pr view --json 也无任何 association
        字段，gh 2.88.1 实证 Unknown JSON field）。位置契约：先于 draft/
        活跃执行者等后续守卫（越权作者不消费任何上下文采集）。
        """
        ctx = _autofix_step_by_id("ctx")["run"]
        assert "author_association" in ctx, (
            "ctx 步必须以 author_association 执行作者白名单（VAL-M1-007）"
        )
        assert "/pulls/" in ctx and "gh api" in ctx, (
            "association 载体必须是 REST pulls 端点（gh api repos/…/pulls/<n>）"
        )
        assert "--json authorAssociation" not in ctx, (
            "gh pr view --json authorAssociation 是不存在的字段（gh 实证 Unknown field），禁止回归"
        )
        for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
            assert assoc in ctx, f"association 白名单缺 {assoc}"
        assert "OWNER|MEMBER|COLLABORATOR" in ctx, (
            "白名单必须以 case 模式 OWNER|MEMBER|COLLABORATOR 显式枚举"
        )
        # fail-closed：取不到/非白名单显式 skip，不静默放行
        assert "skip=true" in ctx, "非白名单必须 skip=true（fail-closed）"
        # 双路径共享：门在 dispatch/workflow_run 分支合流之后（state 检查后、
        # draft 守卫前）
        assert ctx.index("author_association") < ctx.index("DRAFT="), (
            "association 门必须先于 draft/活跃执行者守卫（合流后统一执行）"
        )

    def test_assoc_read_two_states_api_error_vs_untrusted(self) -> None:
        """round-10 sh2 P1：ASSOC 读取失败与作者不受信必须分态，禁止静默合并。

        `--jq '... // ""' || true` 把 gh api 失败（网络/auth/限流）与「作者
        确实不在白名单」压成同一个空串——瞬态故障与真实低权限作者产出完全
        相同的静默 skip，门是否真的评估过不可审计。契约：读取失败显式
        ::warning:: 留痕 + ASSOC=api-error 分态走 skip（fail-closed 语义
        不变），只有真实读取成功才进白名单 case。
        """
        ctx = _autofix_step_by_id("ctx")["run"]
        # 读取不再吞错：gh api 取 author_association 的行不得带 || true
        assoc_line = next(ln for ln in ctx.splitlines() if "author_association //" in ln)
        assert "|| true" not in assoc_line, (
            "ASSOC 读取禁止 || true（吞掉 gh api 失败诊断，瞬态故障与作者不受信不可区分）"
        )
        # 顺序契约：读取失败 → warning 留痕 → 置分态 → case 分态 skip
        i_warn = ctx.index("::warning::")
        i_assign = ctx.index('ASSOC="api-error"')
        i_label = ctx.index("\n  api-error)")
        i_case = ctx.index('case "$ASSOC" in')
        assert i_warn < i_assign, "读取失败必须先 ::warning:: 留痕再置分态（诊断不被吞）"
        assert i_case < i_label, "api-error 必须是白名单 case 的显式分支（分态 skip 留痕）"
        err_case = ctx[i_label : ctx.index(";;", i_label)]
        assert "skip=true" in err_case, "api-error 分支同样 skip（fail-closed 语义不变）"

    def test_job_if_has_no_label_gate(self) -> None:
        """拆门后 job 级 if 不得引用 labels（标签门零残留）。"""
        assert "labels" not in self._job_if(), "job 级 if 不得含 labels 门"

    def test_dispatch_path_shares_maintainer_gate(self) -> None:
        """workflow_dispatch 路径限 MAINTAINERS：与 workflow_run 共用同一道门。

        MAINTAINERS 白名单 = OWNER/MEMBER/COLLABORATOR（规格 §3.3）。拓扑
        契约：全脚本恰有一处 association 判定，且位于 dispatch/workflow_run
        分支合流之后的共享段——dispatch 分支内不得有独立的 association
        旁路或影子实现（两处白名单必然漂移）。
        """
        ctx = _autofix_step_by_id("ctx")["run"]
        # 恰一处白名单（共享门，无旁路副本）
        assert ctx.count("OWNER|MEMBER|COLLABORATOR") == 1, (
            "association 白名单判定必须恰一处（dispatch 与 workflow_run 共享）"
        )
        # dispatch 分支内无独立 association 取数/判定（YAML block scalar
        # 解析后脚本顶格，分支分隔符 = 行首 else）
        i_dispatch = ctx.index('"$EVENT_NAME" = "workflow_dispatch"')
        i_else = ctx.index("\nelse\n", i_dispatch)
        dispatch_branch = ctx[i_dispatch:i_else]
        assert "ASSOC" not in dispatch_branch, (
            "dispatch 分支禁止独立 association 判定（统一走合流后的共享门）"
        )
        # fail-closed：非白名单显式 skip
        assert "skip=true" in ctx, "非白名单必须 skip=true（fail-closed）"

    def test_pr_body_is_context_only_never_command(self) -> None:
        """PR body 只作上下文摘要注入提示词，绝不解析为命令执行。"""
        steps = _autofix()["jobs"]["autofix"]["steps"]
        prompt = next(s for s in steps if s.get("name") == "Build autofix prompt")["run"]
        # body 仅经描述性标签注入（echo 摘录行），无 eval/直接执行通道
        assert "PR 描述摘录" in prompt, "PR body 必须以描述摘录形式进入提示词"
        for forbidden in ("eval ", "bash -c", "| sh"):
            assert forbidden not in prompt, f"提示词组装禁止出现执行通道 {forbidden!r}"
        # body 变量的每次出现都必须落在描述摘录 echo 行内（唯一合法用途）
        body_lines = [ln for ln in prompt.splitlines() if "PR_BODY" in ln]
        assert body_lines, "提示词组装必须引用 PR body（上下文摘要）"
        for ln in body_lines:
            assert "PR 描述摘录" in ln and ln.strip().startswith("echo"), (
                "PR body 只能经 echo 摘录行进入提示词（禁止任何其他用途）"
            )
        # 模型输入唯一通道 = /tmp/prompt.md 文件（-f 注入，非 shell 拼接）
        exec_run = next(s for s in steps if s.get("id") == "exec")["run"]
        assert "droid exec" in exec_run and "-f /tmp/prompt.md" in exec_run, (
            "droid exec 必须经 -f /tmp/prompt.md 注入提示词（无 shell 拼接通道）"
        )

    def test_notify_filter_matches_exact_job_name(self) -> None:
        """notify-only 过滤按 job 显示名精确匹配（字面量仍锁定在过滤步内）。"""
        script = _autofix_step_by_id("notify_filter")["run"]
        assert NOTIFY_JOB_NAME in script


class TestNotifyJobNameMappingLock:
    """附加（M1-A review 遗留，VAL-AF-003）：autofix notify 过滤 ↔ ci.yml notify
    job 显示名的映射此前无契约锁定。任一侧重命名 → 过滤静默失效（notify-only
    送达失败被当代码失败误修）。锁定两侧字面一致。"""

    def test_ci_notify_job_display_name(self) -> None:
        """ci.yml 的 notify job 显示名（映射源端）。"""
        job = _load(CI_YML)["jobs"]["notify-ci-complete"]
        assert job["name"] == NOTIFY_JOB_NAME, (
            "ci.yml notify job 显示名漂移：须同步 droid-autofix 过滤字面量"
        )

    def test_autofix_filter_literal_matches_ci_notify_name(self) -> None:
        """autofix 过滤字面量 == ci.yml notify job 显示名（映射锁定）。"""
        script = _autofix_step_by_id("notify_filter")["run"]
        assert NOTIFY_JOB_NAME in script, (
            "droid-autofix 过滤字面量与 ci.yml notify job 名漂移（重命名须双侧同改）"
        )
