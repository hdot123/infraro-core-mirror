"""cancel-on-pr-closed job 契约（规格书 docs/guides/cancel-on-pr-closed.md）。

锁定 watchdog 新增的「PR 关闭止损边」：pull_request_target types:[closed]
触发、job 级 actions:write（顶层只读基线不破）、cancel 硬过滤
（status × event × 五白名单 name）× 自排除、kill-switch/dry-run 护栏、
cancel 失败 ::warning:: 不红 job。白名单名与 .github/workflows/*.yml 的
name: 字节一致（动态对账，workflow 显示名漂移或增删要同步本测试）。

TDD 契约：实现前本文件必须红（job 不存在），实现后全绿。
"""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.business_policy

REPO_ROOT = Path(__file__).parent.parent
WATCHDOG_PATH = REPO_ROOT / ".github/workflows/droid-review-watchdog.yml"
WORKFLOWS_DIR = REPO_ROOT / ".github/workflows"

# 规格书白名单：仅这些 workflow 的 PR 系 run 会被 cancel
WHITELIST = ["CI", "QA", "Quality Gate", "Droid Auto Review", "Auto Merge"]
# cancel 事件硬过滤（负向：push/schedule/workflow_dispatch/workflow_run 一律不 cancel）
CANCEL_EVENTS = ["pull_request", "pull_request_target"]
JOB_NAME = "cancel-on-pr-closed"


def _load() -> dict:
    return yaml.safe_load(WATCHDOG_PATH.read_text())


def _job() -> dict:
    return _load()["jobs"][JOB_NAME]


def _action_run() -> str:
    return _job()["steps"][-1]["run"]


class TestJobPresenceAndTrigger:
    """job 存在 + 触发面：pull_request_target types:[closed]。"""

    def test_job_exists(self):
        assert JOB_NAME in _load()["jobs"], f"watchdog 缺 {JOB_NAME} job（PR 关闭止损边未落地）"

    def test_pull_request_target_closed_trigger(self):
        on = _load()[True]  # yaml 把 `on:` 解析为布尔 True 键
        prt = on.get("pull_request_target")
        assert prt is not None, "watchdog 触发器缺 pull_request_target"
        assert prt.get("types") == ["closed"]

    def test_event_name_gate_prevents_non_pr_entry(self):
        """job if 显式门控 event_name，防 schedule/workflow_run 等事件误入（规格硬约束）。"""
        assert "github.event_name == 'pull_request_target'" in _job()["if"]

    def test_kill_switch_gates_job(self):
        """G1：新 job 同样接 watchdog 全局 kill-switch（job 级 if + 步内双保险）。"""
        assert "vars.WATCHDOG_KILL_SWITCH != 'enabled'" in _job()["if"]
        guard = _job()["steps"][0]["run"]
        assert "KILL_SWITCH" in guard
        assert "WATCHDOG_MODE=skip" in guard

    def test_runner_and_timeout(self):
        assert _job()["runs-on"] == "ubuntu-latest", "规格硬约束：薄腿走 hosted，勿占 pve"
        assert _job()["timeout-minutes"] <= 5

    def test_no_head_checkout(self):
        """禁止 checkout PR head（fork 面不可信代码，规格书非目标）。

        强断言（#457 审查 P2）：本 job 是纯 API 止损腿，规格明令「禁止
        checkout PR head 代码」——任何 actions/checkout 步骤都违规（无
        with.ref 的默认行为同样落到事件 ref，裸表达式绕不过）。同时覆盖
        fork 可控的 ref/sha 各种写法，防精确串匹配的假阴性。
        """
        job = _job()
        checkout_steps = [s for s in job["steps"] if "actions/checkout" in s.get("uses", "")]
        assert not checkout_steps, (
            f"cancel-on-pr-closed 出现 checkout 步骤（规格非目标：fork 面禁 checkout）："
            f"{[s.get('name') for s in checkout_steps]}"
        )
        # 纵深防御：任何步骤的 with 值都禁 PR head 来源 ref（防未来以其他
        # action/checkout 变体夹带）。注意 HEAD_SHA env 是合法 API 查询输入
        # （规格 §Cancel 范围），不在禁令内。
        for step in job["steps"]:
            with_vals = [str(v) for v in (step.get("with") or {}).values()]
            for token in (
                "github.event.pull_request.head.sha",
                "github.event.pull_request.head.ref",
                "refs/pull/",
            ):
                hits = [v for v in with_vals if token in v]
                assert not hits, (
                    f"步骤 {step.get('name')} 的 with 夹带 PR head 来源 ref {token}: {hits}"
                )


class TestPermissions:
    """G4 权限对齐：actions:write 仅 job 级，顶层只读基线不被突破。"""

    def test_actions_write_job_level_only(self):
        data = _load()
        assert data["permissions"] == {"contents": "read"}, "顶层只读基线被突破"
        assert _job()["permissions"] == {"actions": "write"}

    def test_other_jobs_permissions_unchanged(self):
        """新 job 不搭车扩权：其余 job 权限面保持原样（含 absence-sentinel 只读+checks 面）。

        逐 job 快照对账（#457 审查 P2）：sibling job 权限面以 D-1 落地态为
        基线快照，任何 scope 增删或读写级别变化（含 actions/checks 等非
        contents scope 升 write）都须显式改本快照——防「新触发面引入后
        sibling 借车扩权」静默通过。
        """
        baseline = {
            "self-heal-rerun": {"actions": "write", "pull-requests": "read"},
            "cancel-on-ci-fail": {"actions": "write"},
            "quota-sweep": {"actions": "write"},
            "absence-sentinel": {
                "contents": "read",
                "checks": "write",
                "pull-requests": "read",
                "actions": "read",
            },
        }
        jobs = _load()["jobs"]
        for name, expected in baseline.items():
            assert name in jobs, f"job {name} 被移除（快照基线失配）"
            actual = jobs[name].get("permissions")
            assert actual == expected, (
                f"job {name} 权限面漂移（搭车扩权检查）：expected={expected} actual={actual}"
            )


class TestHardFilter:
    """cancel 硬过滤：status × event × 白名单 name，jq 在行动步真实执行（规格 §Cancel 范围）。"""

    def test_filters_both_statuses(self):
        """全部非终态 status 都被列举（API 按参数过滤，实现为循环枚举）。

        超集口径（#457 审查 P3@207 采纳）：非终态 = queued/in_progress/
        waiting/requested——waiting（环境审批门）与 requested 同样占着
        closed PR；仅查 queued/in_progress 会漏。API 实证：status= 查询
        参数接受 waiting/requested 枚举值（2026-10-26 实测 200）。
        """
        run = _action_run()
        assert "for STATUS in in_progress queued waiting requested" in run
        assert "status=$STATUS" in run

    def test_event_filter_excludes_push_schedule_dispatch(self):
        """负向：过滤集仅 PR 系事件；push/schedule/workflow_dispatch/workflow_run 不入。"""
        run = _action_run()
        for ev in CANCEL_EVENTS:
            assert f'.event == "{ev}"' in run
        for ev in ("push", "schedule", "workflow_dispatch", "workflow_run"):
            assert f'.event == "{ev}"' not in run, f"负向事件 {ev} 不得出现在 cancel 过滤集"

    def test_event_filter_covers_both_pr_events_in_one_select(self):
        """event 过滤单条 select 同时覆盖两个允许事件（jq 值级 or 是正确语义，
        缺一即漏 cancel 另一系；第三方事件混入由排除断言拦截）。"""
        run = _action_run()
        sel_lines = [
            ln.strip() for ln in run.splitlines() if ".event ==" in ln and "pull_request" in ln
        ]
        assert sel_lines, "缺 PR 系 event 过滤 select"
        for ln in sel_lines:
            assert '.event == "pull_request"' in ln and '.event == "pull_request_target"' in ln, (
                f"event select 未同时覆盖两个允许事件：{ln}"
            )

    def test_whitelist_names_appear_in_filter(self):
        run = _action_run()
        for name in WHITELIST:
            assert name in run, f"白名单 workflow {name!r} 未出现在 cancel 过滤中"

    def test_self_exclusion_by_run_id(self):
        """自排除 GITHUB_RUN_ID：勿 cancel 自己（规格 §Cancel 范围第 3 条）。

        实现为 shell 循环比较（gh api 无 --arg 旗标，jq 变量注入不可用），
        断言打在行动步的 shell 排除分支 + GITHUB_RUN_ID 显式声明上。
        """
        job = _job()
        run = _action_run()
        assert job["steps"][-1]["env"]["GITHUB_RUN_ID"] == "${{ github.run_id }}"
        assert '[ "$RUN_ID" = "$GITHUB_RUN_ID" ]' in run
        assert "self-exclusion" in run

    def test_cancel_scope_pinned_to_closed_pr_number(self):
        """cancel 范围钉死到被关 PR（#457 审查 P1@181 抄袭面）。

        head_sha 可被第三方复制（victim PR commits 是公开引用，同 SHA 推到
        fork 再开假 PR 即可借 close 误杀 victim 在飞 run）；pull_requests[]
        编号不随 SHA 复制，candidate 必须挂在被关 PR 名下才可 cancel。
        """
        job = _job()
        run = _action_run()
        assert job["steps"][-1]["env"]["PR_NUMBER"] == "${{ github.event.pull_request.number }}"
        assert ".pull_requests[]?; .number == '\"$PR_NUMBER\"'" in run, (
            "jq 过滤缺 PR 编号归属校验（仅 head_sha 可被同 SHA 假 PR 借道）"
        )

    def test_listing_failure_warns_not_silent(self):
        """列举失败显式 ::warning::（#457 审查 P2@208）：限流/网络失败不得
        静默退化成空集 + 绿色 nothing-to-do（止损悄无声息失效）。"""
        run = _action_run()
        assert re.search(r"2>/dev/null\)\s*\|\|\s*\{", run) or (
            'IDS=""' in run and "::warning::" in run.split("for STATUS in")[1].split("done")[0]
        ), "列举失败缺 ::warning:: 信号（静默吞错回归）"

    def test_cancel_failure_is_warning_not_hard_fail(self):
        """cancel 失败 ::warning:: 后继续，不红整 job（止损尽力而为）。"""
        assert "::warning::" in _action_run()

    def test_detection_before_mode_gate(self):
        """G6：检测逻辑（run 列举）出现在模式门控之前（observe 也完整执行检测）。"""
        run = _action_run()
        assert run.index("COLLECT_RUN_IDS=") < run.index("WATCHDOG_MODE:-live")


class TestGuardrails:
    """护栏：dry-run 三态 + guard 早退落 skip + 行动步复检（跨步门控闭环）。"""

    def test_dry_run_mode_wired(self):
        """G6 三态护栏接线：observe 分支在位，默认 live。

        默认 live 的理由（副本注释同步）：本腿 ubuntu-latest 每跑全新 VM，
        auto-rollout 的 /tmp 计数器恒 1，auto 形态退化为永久 observe（护栏
        变相永久禁用、活体验证永远过不去）；cancel 目标是 closed PR 的 run，
        kill-switch 全覆盖，显式 WATCHDOG_DRY_RUN=true 仍可锁定观察窗。
        """
        env = _job()["steps"][0]["env"]
        assert env["DRY_RUN"] == "${{ vars.WATCHDOG_DRY_RUN || 'live' }}"
        run = _action_run()
        assert "observe" in run, "行动步缺 observe 分支（dry-run 护栏断裂）"
        assert "G6 dry-run" in run

    def test_no_hosted_runner_rollout_counter(self):
        """auto-rollout 计数器不得混入本 job：ubuntu 每跑全新 VM，计数器恒 1
        会让 auto 形态退化为永久 observe（护栏变相永久禁用）。"""
        guard = _job()["steps"][0]["run"]
        assert "ROLLOUT_COUNTER" not in guard

    def test_guard_exports_watchdog_mode(self):
        guard = _job()["steps"][0]["run"]
        assert 'echo "WATCHDOG_MODE=$MODE" >> "$GITHUB_ENV"' in guard

    def test_action_rechecks_skip_before_any_api_call(self):
        """行动步入口复检 skip，且复检先于一切 GitHub API 调用（早退零执行）。"""
        run = _action_run()
        recheck = '[ "${WATCHDOG_MODE:-}" = "skip" ]'
        assert recheck in run, "行动步缺 skip 复检（guard 早退后行动步兜底 live，门控断裂）"
        assert run.index(recheck) < run.index("gh api")

    def test_pagination_upper_bound(self):
        """G2：列举分页带上界（≤ 2 页 × 100），无 --paginate 无界跟随。"""
        run = _action_run()
        assert "--paginate" not in run, "无界分页跟随违反 G2"
        assert "MAX_PAGES" in run
        assert "per_page=100" in run

    def test_guard_early_exit_falls_to_skip(self):
        """guard 每个 exit 0 早退分支先落 WATCHDOG_MODE=skip（2026-10-06 三轮返修同款）。

        #457 审查 P2 双修：
        ① 匹配放宽到一切 exit 0 变体（`exit 0  # x` / `exit 0;` / 裸 `exit`），
          且显式断言至少命中一条早退线——零命中即空转假绿，直接红；
        ② 归属范围收窄到所在 if 分支（「之前的所有行」会让更早分支的 skip
          写入满足后续所有分支，某个缺 skip 的新早退分支漏检）。
        """
        guard = _job()["steps"][0]["run"]
        lines = guard.splitlines()
        skip_export = 'echo "WATCHDOG_MODE=skip" >> "$GITHUB_ENV"'
        exit_re = re.compile(r"^exit( 0[^;]*)?;?$")
        matched = 0
        for i, ln in enumerate(lines):
            if not exit_re.match(ln.strip()):
                continue
            matched += 1
            # 收窄归属：只看本行与上一个 if 开头之间（所在分支块），
            # 更早分支的 skip 写入不算数（跨分支满足即漏检）。
            block_start = 0
            for j in range(i - 1, -1, -1):
                if re.match(r"^\s*if\b", lines[j]):
                    block_start = j
                    break
            branch = "\n".join(lines[block_start : i + 1])
            assert skip_export in branch, (
                f"guard 步第 {i + 1} 行早退（{ln.strip()!r}）所在分支未落 WATCHDOG_MODE=skip"
            )
        assert matched > 0, "guard 步未检出任何 exit 0 早退线——断言空转（假绿）"


class TestWhitelistByteExactness:
    """白名单五名与 .github/workflows/*.yml 的 name: 字节一致（动态对账）。"""

    def _display_names(self) -> set[str]:
        names = set()
        # .yml 与 .yaml 双扩展（#457 审查 P2@210）：合法 workflow 文件两种
        # 扩展都常用，漏一种会让改名/新增 .yaml workflow 的显示名漂移失察
        for pattern in ("*.yml", "*.yaml"):
            for path in WORKFLOWS_DIR.glob(pattern):
                data = yaml.safe_load(path.read_text()) or {}
                name = data.get("name")
                if isinstance(name, str):
                    names.add(name)
        return names

    def test_whitelist_names_byte_exact(self):
        display = self._display_names()
        missing = [n for n in WHITELIST if n not in display]
        assert not missing, f"白名单名与 workflow 显示名漂移（字节不一致）：{missing}"

    def test_whitelist_filter_order_matches_spec(self):
        """行动步 jq 过滤的 name 序列含全部五名（字面顺序不锁死，集合须完备）。"""
        run = _action_run()
        found = [n for n in WHITELIST if f'.name == "{n}"' in run]
        assert len(found) == len(WHITELIST), f"jq 过滤缺白名单名：{set(WHITELIST) - set(found)}"


class TestNoForbiddenTouches:
    """规格书非目标：不碰 ruleset/required check/零红语义/check 结论。"""

    def test_no_gate_bypass_tokens(self):
        for step in _job()["steps"]:
            run = step.get("run", "")
            for forbidden in ("check-runs", "annotations", "--admin"):
                assert forbidden not in run, f"门禁绕过面出现：{forbidden!r}"

    def test_merged_and_closed_both_cancel(self):
        """merged 与未合并 closed 一律 cancel PR 系 run（规格：merged 仅日志、不作过滤条件）。"""
        run = _action_run()
        # 若出现 merged 门控，必须只用于日志而非过滤（本仓当前形态：无 merged 过滤）
        assert ".pull_request.merged ==" not in run

    def test_whitelist_match_requires_full_filter_chain(self):
        """白名单断言防假阴性加固（#457 审查 P3 归档）：五种过滤名必须以
        jq 精确等值形态出现，裸子串出现不足以满足本断言（「CI」出现在
        注释/无关文本里不算接线）。主断言由
        test_whitelist_filter_order_matches_spec 承担，本测试锁五名齐活。"""
        run = _action_run()
        missing = [n for n in WHITELIST if f'.name == "{n}"' not in run]
        assert not missing, f"jq 过滤缺白名单名（jq 精确形态）：{missing}"
