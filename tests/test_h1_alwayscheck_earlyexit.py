"""H1 六件套 ③④⑤ 契约测试（h1-alwayscheck-earlyexit，VAL-H1-002/VAL-H1-003）。

锁死四张契约面（对 validation-contract VAL-H1-002/VAL-H1-003 的逐条落点）：

①  聚合 job if:always() + 上游失败 guard（droid-review.yml）
   - 聚合 job `droid-review`（check 名契约承载体）的 if 必须恒为 always()，
     不得再夹带 `needs.plan-shards.result == 'success'` 之类的抑制条件——
     plan-shards 失败时聚合必须照常运行并落**说明性 failure check**。
   - guard 步必须在 Check shard results 之前执行，捕获三上游 needs 结果，
     skipped 视为合法跳过，非 success/skipped 显式 exit 1 + ::error 判因。
   - 零新增权限：job 级 permissions 集合锁死（无 checks:write）——check 由
     GitHub 在 job 结束时自动发布，guard 无需任何 API 调用。

②  check_droid_review.sh 早退（④）
   - 轮询预算常量钉死：MAX_ATTEMPTS=90 × WAIT_SECONDS=30 = 45min（收紧自
     120×30s），且严格小于上游聚合天花板（shard 45min + 聚合 90min）与
     quality-gate 聚合窗（200×30s ≈ 100min，PR #248 慢尾死锁后自 130×30s
     扩容）。
   - 上游 run 对本 SHA 终态 failure/cancelled/neutral 家族 → 立即 exit 1
     + 判因输出（不再烧满预算等一个永远不会出现的 check）。
   - run 活跃/不可判定 → 保持轮询（fail-closed 不变）。

③  stale-head 早退（④）
   - PR head 已漂离被轮询 SHA（R10 relock race 家族）→ grace 一轮后
     exit 1 + 打印两个 SHA 判因。旧 SHA 上 run 即使 success 也不放行。

④  head-drift 守卫（⑤，C4 契约）
   - success 结论只准在「被轮询 SHA 仍是 PR head」时授予；head 已漂移时
     对旧 SHA 的 success 一律降级 neutral 语义 exit 1，绝不准 exit 0 冒充
     有效审查。专项 E2E + 结构断言双重锁死。

run 级用例复用 tests/test_check_droid_review.py 的 fake-curl PATH shim 模式
（sleep 即返、行为脚本化），对 scripts/ 与 src/infra_core/shell/ 两份副本
分别执行——两份必须字节一致（既有 TestScriptCopiesSync 锁）。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.poller_harness import (  # noqa: F401 — FAKE_CURL_TEMPLATE 供同步锚引用，fake_bin 为 pytest fixture
    FAKE_CURL_TEMPLATE,
    fake_bin,
)
from tests.poller_harness import run_poller as _run_poller_shared

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "droid-review.yml"
QUALITY_GATE_PATH = REPO_ROOT / ".github" / "workflows" / "quality-gate.yml"
SHELL_COPY = REPO_ROOT / "src" / "infra_core" / "shell" / "check_droid_review.sh"
SCRIPTS_COPY = REPO_ROOT / "scripts" / "check_droid_review.sh"
BOTH_POLLER_COPIES = [SHELL_COPY, SCRIPTS_COPY]

AGGREGATE_JOB_KEY = "droid-review"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _poller_text(path: Path) -> str:
    return path.read_text()


def _run(url: str) -> str:
    return f"https://api.github.com/repos/hdot123/memory/{url}"


CHECKRUNS_URL = _run("commits/deadbeefcafe/check-runs?check_name=droid-review")


def _make_run(head_sha: str, status: str, conclusion, run_number: int = 7) -> dict:
    return {
        "name": "Droid Auto Review",
        "event": "pull_request_target",
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "run_number": run_number,
    }


def _runs_body(*runs: dict) -> str:
    return json.dumps({"total_count": len(runs), "workflow_runs": list(runs)})


def _checkruns_body(conclusion) -> str:
    run = {"status": "completed", "conclusion": conclusion, "started_at": "2026-09-30T00:00:00Z"}
    return json.dumps({"total_count": 1, "check_runs": [run]})


EMPTY_CHECKRUNS = json.dumps({"total_count": 0, "check_runs": []})


# ---------------------------------------------------------------------------
# ① 聚合 job if:always() + guard 契约
# ---------------------------------------------------------------------------


class TestAggregateAlwaysCheck:
    """droid-review.yml 聚合 job：if 恒 always()，上游失败必落说明性 failure。"""

    def setup_method(self):
        self.data = _load_workflow()
        self.jobs = self.data.get("jobs", {})
        self.agg = self.jobs.get(AGGREGATE_JOB_KEY)
        assert self.agg is not None, (
            f"聚合 job key `{AGGREGATE_JOB_KEY}` 必须存在（check 名精确契约，"
            "branch-protection/ci-ok 依赖）"
        )

    def test_aggregate_if_is_unconditional_always(self):
        """VAL-H1-002：聚合 job if 必须恰为 always()——禁止 plan-shards.result 抑制。

        旧条件 `always() && needs.plan-shards.result == 'success'` 会在
        plan-shards 失败时整体 skip 聚合 → droid-review check 永不出现 →
        ci-ok 空转轮询到预算耗尽。always() 让任何上游失败都照常运行聚合，
        由 guard 步转成说明性 failure check。
        """
        job_if = str(self.agg.get("if", "")).strip()
        assert job_if == "always()", (
            f"聚合 job if 必须恒为 always()，实际：{job_if!r}——"
            "任何 `needs.*.result == 'success'` 抑制都会让 check 在上游失败时"
            "永不出现（ci-ok 空转根因）"
        )

    def test_guard_step_exists_before_check_results(self):
        """guard 步存在且先于 Check shard results（上游失败先短路再谈聚合）。"""
        steps = self.agg.get("steps") or []
        guard_at = [
            i
            for i, s in enumerate(steps)
            if "Guard upstream job failures" in str(s.get("name", ""))
        ]
        check_at = [i for i, s in enumerate(steps) if s.get("id") == "check_results"]
        assert len(guard_at) == 1, "Guard upstream job failures 步必须恰有 1 个"
        assert len(check_at) == 1, "check_results 步必须恰有 1 个"
        assert guard_at[0] < check_at[0], (
            "guard 步必须先于 Check shard results——上游失败先落说明性 failure，不再进入聚合发布逻辑"
        )

    def test_guard_captures_all_upstream_results(self):
        """guard 必须捕获全部上游 needs 结果（B-1 收敛后上游收敛为 shards
        调用 job 单点——分片三 job 业务体单源 reusable）。"""
        steps = self.agg.get("steps") or []
        guard = next(s for s in steps if "Guard upstream job failures" in str(s.get("name", "")))
        env = guard.get("env") or {}
        for var, expr in (("SHARDS_RESULT", "needs.shards.result"),):
            assert env.get(var) == f"${{{{ {expr} }}}}", f"guard env 缺少 {var} ← {expr} 捕获"

    def test_guard_skipped_is_legal_and_failure_is_explanatory(self):
        """skipped=合法跳过；非 success/skipped 显式 exit 1 + ::error 判因。"""
        steps = self.agg.get("steps") or []
        guard = next(s for s in steps if "Guard upstream job failures" in str(s.get("name", "")))
        script = str(guard.get("run") or "")
        # 全部上游结果都做 skipped 豁免（docs-only/空计划→零矩阵展开 = 合法跳过，
        # shards 调用 job 结论仍 success；skipped 豁免保留对 shell 链的兜底）
        for var in ("SHARDS_RESULT",):
            assert '"$' + var + '"' in script, f"guard 脚本必须引用 {var}"
        assert script.count('!= "skipped"') == 1, (
            "全部上游结果都必须豁免 skipped（合法跳过不构成失败）"
        )
        assert "exit 1" in script, "上游失败必须 exit 1（job 落 failure check）"
        assert "::error::" in script, "失败必须带 ::error 说明性判因输出"

    def test_guard_zero_new_permissions(self):
        """零新增权限：聚合 job permissions 集合锁死，无 checks:write。

        check 由 GitHub 在 job 结束时自动发布（job 名 = check 名），guard
        无需任何 API 调用——checks:write 是多余的权限面。
        """
        perms = self.agg.get("permissions") or {}
        assert perms == {
            "contents": "read",
            "pull-requests": "write",
            "issues": "write",
            "actions": "write",
        }, f"聚合 job permissions 必须保持既有四面，实际：{perms}"
        # 全 workflow 任何 job 都不得新增 checks 写权限
        for job_name, job in self.jobs.items():
            job_perms = (job or {}).get("permissions") or {}
            assert "checks" not in job_perms, (
                f"job {job_name} 出现 checks 权限——always-check 方案零新增权限"
            )
        assert "checks" not in (self.data.get("permissions") or {}), (
            "workflow 顶层不得新增 checks 权限"
        )

    def test_three_contracts_intact(self):
        """三契约回归：workflow 名 / 聚合 job key 不因本变更漂移。"""
        assert self.data.get("name") == "Droid Auto Review"
        assert AGGREGATE_JOB_KEY in self.jobs


# ---------------------------------------------------------------------------
# ② 轮询预算常量 + 聚合窗一致性
# ---------------------------------------------------------------------------


class TestPollingBudgetConstant:
    """VAL-H1-002：预算常量钉死 90×30s=45min，且严格小于上游天花板。"""

    @pytest.fixture(params=BOTH_POLLER_COPIES, ids=["shell-copy", "scripts-copy"])
    def poller_text(self, request):
        return _poller_text(request.param)

    def test_budget_constants_pinned(self, poller_text):
        """MAX_ATTEMPTS=90 / WAIT_SECONDS=30（120×30s 收敛建议落地值）。"""
        attempts = re.search(r"^MAX_ATTEMPTS=(\d+)$", poller_text, re.M)
        wait = re.search(r"^WAIT_SECONDS=(\d+)$", poller_text, re.M)
        assert attempts and attempts.group(1) == "90", (
            "MAX_ATTEMPTS 必须钉死 90（45min 预算，feature 规格收敛值）"
        )
        assert wait and wait.group(1) == "30", "WAIT_SECONDS 必须钉死 30s"

    def test_budget_below_upstream_ceiling(self, poller_text):
        """轮询预算 45min < 聚合 90min（VAL-H1-002 原文判据）。

        上游天花板 = shard timeout 默认 45min + 聚合 timeout 默认 90min
        （droid-review.yml 预算分层）；轮询预算 ≥ 天花板时永远等不出健康
        run，只会推迟 ci-ok 的红。
        """
        poller_minutes = 90 * 30 / 60
        wf_raw = WORKFLOW_PATH.read_text()
        shard_m = int(re.search(r"SHARD_TIMEOUT_MINUTES \|\| '(\d+)'", wf_raw).group(1))
        agg_m = int(re.search(r"DROID_REVIEW_TIMEOUT_MINUTES \|\| '(\d+)'", wf_raw).group(1))
        assert poller_minutes < agg_m, (
            f"轮询预算 {poller_minutes}min 必须严格小于聚合 timeout {agg_m}min"
            "（VAL-H1-002：预算 < 聚合 90min）"
        )
        assert poller_minutes < shard_m + agg_m, "轮询预算必须严格小于上游完整天花板（shard+聚合）"

    def test_budget_below_quality_gate_window(self, poller_text):
        """轮询预算 < quality-gate 聚合窗（200×30s≈100min），链条不打架。"""
        qg_raw = QUALITY_GATE_PATH.read_text()
        m = re.search(r"^          MAX_ATTEMPTS=(\d+)$", qg_raw, re.M)
        assert m, "quality-gate.yml 必须保留 MAX_ATTEMPTS 常量（聚合窗真源）"
        qg_minutes = int(m.group(1)) * 30 / 60
        assert 90 * 30 / 60 < qg_minutes, (
            f"轮询预算 45min 必须 < quality-gate 聚合窗 {qg_minutes}min"
        )

    def test_early_exit_scan_interval_pinned(self, poller_text):
        """早退扫描节奏常量在板（attempt 1 起，每 INTERVAL 轮一次）。"""
        assert re.search(r"^EARLY_EXIT_SCAN_INTERVAL=5$", poller_text, re.M), (
            "早退扫描节奏常量 EARLY_EXIT_SCAN_INTERVAL=5 必须在板"
        )
        assert "(attempt - 1) % EARLY_EXIT_SCAN_INTERVAL" in poller_text, (
            "轮询循环必须按节奏调用早退探测"
        )


# ---------------------------------------------------------------------------
# ③ 早退分支 E2E（上游终态失败 → 立即判因退出）
# ---------------------------------------------------------------------------


class TestEarlyExitUpstreamFailure:
    """上游 run 终态失败家族 → 立即 exit 1 + 判因，不再烧预算。"""

    @pytest.fixture(params=BOTH_POLLER_COPIES, ids=["shell-copy", "scripts-copy"])
    def poller_path(self, request):
        return request.param

    def _run_poller(self, poller_path, fake_bin, behaviors):
        return _run_poller_shared(poller_path, fake_bin, behaviors)

    SAME_SHA = "deadbeefcafe"

    def test_upstream_failure_exits_immediately_with_cause(self, poller_path, fake_bin):
        """run 终态 failure → attempt 1 即退出，判因含结论与 cause 说明。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS)],
            "runs": [(0, _runs_body(_make_run(self.SAME_SHA, "completed", "failure")))],
            "pulls": [(0, "[]")],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1
        assert "Attempt 1/90" in result.stdout, "必须在第一次探测就早退"
        assert "Attempt 2/90" not in result.stdout, "早退后不得再轮询"
        assert "early-exit" in result.stdout
        assert "concluded 'failure'" in result.stdout
        assert "cause:" in result.stdout, "判因输出（VAL-H1-002：判因输出）"

    @pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "neutral"])
    def test_upstream_terminal_family_exits(self, conclusion, poller_path, fake_bin):
        """cancelled/timed_out/neutral 家族同样早退（等不出 check 的终态）。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS)],
            "runs": [(0, _runs_body(_make_run(self.SAME_SHA, "completed", conclusion)))],
            "pulls": [(0, "[]")],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "early-exit" in result.stdout
        assert f"concluded '{conclusion}'" in result.stdout

    def test_active_run_keeps_polling_not_early_exit(self, poller_path, fake_bin):
        """run 活跃 → 不可判定，保持轮询到预算耗尽（fail-closed 不变）。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS)],
            "runs": [(0, _runs_body(_make_run(self.SAME_SHA, "in_progress", None)))],
            "pulls": [(0, "[]")],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1
        assert "early-exit" not in result.stdout, "活跃 run 不得早退"
        assert "not complete after 90 attempts" in result.stdout

    def test_run_probe_transient_failure_never_kills_poller(self, poller_path, fake_bin):
        """run 探测 curl 失败 → 静默保持轮询，正常成功路径不受影响。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS), (0, _checkruns_body("success"))],
            "runs": [(7, "")],
            "pulls": [(0, json.dumps([{"head": {"sha": self.SAME_SHA}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "✓ droid-review passed" in result.stdout


# ---------------------------------------------------------------------------
# ④ stale-head 早退（R10 家族）
# ---------------------------------------------------------------------------


class TestStaleHeadEarlyExit:
    """check 落在非 PR-head（head 漂移）→ 早退判因，绝不空转到预算耗尽。"""

    @pytest.fixture(params=BOTH_POLLER_COPIES, ids=["shell-copy", "scripts-copy"])
    def poller_path(self, request):
        return request.param

    def _run_poller(self, poller_path, fake_bin, behaviors):
        return _run_poller_shared(poller_path, fake_bin, behaviors)

    STALE_SHA = "deadbeefcafe"
    NEW_HEAD = "aa11bb22cc33"

    def test_stale_head_exits_with_cause_after_grace(self, poller_path, fake_bin):
        """PR head 漂移 + 旧 head run 仍活跃 → grace 一轮后 stale-head 早退。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS)],
            "runs": [(0, _runs_body(_make_run(self.STALE_SHA, "in_progress", None)))],
            "pulls": [(0, json.dumps([{"head": {"sha": self.NEW_HEAD}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1
        assert "stale-head probe" in result.stdout, "grace 轮必须先观察并明示"
        assert "grace 1/1" in result.stdout
        assert "non-PR-head SHA (stale-head)" in result.stdout
        assert self.STALE_SHA in result.stdout and self.NEW_HEAD in result.stdout, (
            "判因必须打印两个 SHA（checked + current PR head）"
        )
        assert "exhausted 90 attempts" not in result.stdout, "早退，不烧满预算"

    def test_success_on_stale_sha_still_exits_not_pass(self, poller_path, fake_bin):
        """C4 附加锁：旧 SHA 上 run 终态 success 也不得放行轮询。

        head 已漂移 → 该 success 与当前 PR head 无关（R10：relock 前的旧
        head 审查），stale-head 早退判因退出而非继续空转。
        """
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS)],
            "runs": [(0, _runs_body(_make_run(self.STALE_SHA, "completed", "success")))],
            "pulls": [(0, json.dumps([{"head": {"sha": self.NEW_HEAD}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1
        assert "stale-head" in result.stdout
        assert "✓ droid-review passed" not in result.stdout

    def test_same_head_never_triggers_stale_head(self, poller_path, fake_bin):
        """PR head 未漂移 → 探测不得误判 stale-head（健康路径回归）。"""
        behaviors = {
            "checkruns": [(0, EMPTY_CHECKRUNS), (0, _checkruns_body("success"))],
            "runs": [(0, _runs_body(_make_run(self.STALE_SHA, "in_progress", None)))],
            "pulls": [(0, json.dumps([{"head": {"sha": self.STALE_SHA}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "stale-head" not in result.stdout
        assert "✓ droid-review passed" in result.stdout


# ---------------------------------------------------------------------------
# ⑤ head-drift 守卫（C4 契约：漂移时绝不对旧 SHA 输出 success）
# ---------------------------------------------------------------------------


class TestHeadDriftGuardC4:
    """C4 契约（opus55-v5-confirmation）：head 漂移时只准 neutral/skip 语义，
    不得对旧 SHA 输出 success 冒充有效审查。专项测试锁死。"""

    @pytest.fixture(params=BOTH_POLLER_COPIES, ids=["shell-copy", "scripts-copy"])
    def poller_path(self, request):
        return request.param

    def _run_poller(self, poller_path, fake_bin, behaviors):
        return _run_poller_shared(poller_path, fake_bin, behaviors)

    CHECKED = "deadbeefcafe"
    DRIFTED = "ff00ff00ff00"

    def test_success_on_drifted_head_downgrades_to_neutral(self, poller_path, fake_bin):
        """核心 C4 场景：check success 但 PR head 已漂移 → exit 1 降级 neutral。"""
        behaviors = {
            "checkruns": [(0, _checkruns_body("success"))],
            "runs": [(0, _runs_body(_make_run(self.CHECKED, "completed", "success")))],
            "pulls": [(0, json.dumps([{"head": {"sha": self.DRIFTED}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 1, "C4：对旧 SHA 的 success 绝不准 exit 0（冒充有效审查）"
        assert "downgrades to neutral" in result.stdout
        assert self.CHECKED in result.stdout and self.DRIFTED in result.stdout
        assert "✓ droid-review passed" not in result.stdout, (
            "漂移场景下 success 文案（放行语义）禁止出现"
        )

    def test_success_on_current_head_grants_pass(self, poller_path, fake_bin):
        """回归：head 未漂移时 success 照常放行（守卫只拦漂移）。"""
        behaviors = {
            "checkruns": [(0, _checkruns_body("success"))],
            "runs": [(0, _runs_body(_make_run(self.CHECKED, "completed", "success")))],
            "pulls": [(0, json.dumps([{"head": {"sha": self.CHECKED}}]))],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "✓ droid-review passed" in result.stdout
        assert "downgrades" not in result.stdout

    def test_dependabot_neutral_downgrade_still_allows_merge(self, poller_path, fake_bin):
        """Dependabot 例外在降级路径依然成立（secret 限制语义不回退）。"""
        behaviors = {
            "checkruns": [(0, _checkruns_body("success"))],
            "runs": [(0, _runs_body(_make_run(self.CHECKED, "completed", "success")))],
            "pulls": [
                (
                    0,
                    json.dumps(
                        [
                            {"head": {"sha": self.DRIFTED}, "user": {"login": "dependabot[bot]"}},
                        ]
                    ),
                )
            ],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 0, (
            "Dependabot 降级 neutral 后仍放行（与既有 skipped/neutral 例外同语义）"
        )
        assert "allowing merge despite neutral review" in result.stdout

    def test_probe_failure_never_fakes_drift_verdict(self, poller_path, fake_bin):
        """探测失败（transient）不得伪造漂移判定：保守授予已观测结论。

        依据：head 漂移必经新 push → 新 head 上必有新 run → stale-head 探测
        会先flag；探测不可用时保守放行是「不伪造漂移红」的一侧。
        """
        behaviors = {
            "checkruns": [(0, _checkruns_body("success"))],
            "runs": [(0, _runs_body(_make_run(self.CHECKED, "completed", "success")))],
            "pulls": [(7, "")],
        }
        result = self._run_poller(poller_path, fake_bin, behaviors)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "✓ droid-review passed" in result.stdout

    def test_c4_guard_structure_in_both_copies(self):
        """结构锁：两份副本都带 C4 降级语义标记与 head 校验调用。"""
        for path in BOTH_POLLER_COPIES:
            text = _poller_text(path)
            assert "head-drift guard downgrades to neutral" in text, f"{path}: C4 降级语义标记缺失"
            assert "current_pr_head" in text, f"{path}: head 校验调用缺失"
            assert "C4" in text, f"{path}: C4 契约注释缺失"


# ---------------------------------------------------------------------------
# ⑥ run_case 判定表达式 fixture 级锁定
# ---------------------------------------------------------------------------

RUN_CASE_JQ_MARKER = 'run_case=$(echo "$runs_body" | jq -r --arg cs "$COMMIT_SHA" \''


def _extract_run_case_jq() -> str:
    """从 poller 副本提取 run 探测 Part A 判定 jq 表达式（唯一调用点）。"""
    text = _poller_text(SHELL_COPY)
    start = text.index(RUN_CASE_JQ_MARKER)
    start += len(RUN_CASE_JQ_MARKER) + 1  # skip the opening newline
    end = text.index("\n  ') || return 0", start)
    return text[start:end]


class TestWorkflowRunProbeVerdicts:
    """run 探测判定映射 fixture 级锁定（none/active/pending/终态家族）。"""

    def setup_method(self):
        self.jq_expr = _extract_run_case_jq()

    def _verdict(self, runs: list) -> str:
        payload = json.dumps({"workflow_runs": runs})
        result = subprocess.run(
            ["jq", "-r", "--arg", "cs", "deadbeefcafe", self.jq_expr],
            input=payload,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"jq failed: {result.stderr}"
        return result.stdout.strip()

    def test_no_runs_returns_none(self):
        assert self._verdict([]) == "none"

    def test_active_run_returns_active(self):
        verdict = self._verdict([_make_run("deadbeefcafe", "in_progress", None)])
        assert verdict == "active"

    def test_completed_failure_returns_failure(self):
        verdict = self._verdict([_make_run("deadbeefcafe", "completed", "failure")])
        assert verdict == "failure"

    def test_active_wins_over_stale_completed(self):
        """两相纪律：有活跃 run 就等待，即便更早的 run 已终态（同 SHA 双触发）。"""
        verdict = self._verdict(
            [
                _make_run("deadbeefcafe", "completed", "failure", run_number=6),
                _make_run("deadbeefcafe", "queued", None, run_number=7),
            ]
        )
        assert verdict == "active"

    def test_latest_conclusion_wins_when_all_completed(self):
        verdict = self._verdict(
            [
                _make_run("deadbeefcafe", "completed", "cancelled", run_number=6),
                _make_run("deadbeefcafe", "completed", "success", run_number=7),
            ]
        )
        assert verdict == "success"

    def test_other_workflow_runs_ignored(self):
        verdict = self._verdict(
            [
                {
                    "name": "CI",
                    "event": "pull_request",
                    "head_sha": "deadbeefcafe",
                    "status": "completed",
                    "conclusion": "failure",
                    "run_number": 1,
                },
            ]
        )
        assert verdict == "none", "非 Droid Auto Review 的 run 不得进入判定"

    def test_push_event_runs_ignored(self):
        verdict = self._verdict(
            [
                {
                    "name": "Droid Auto Review",
                    "event": "push",
                    "head_sha": "deadbeefcafe",
                    "status": "completed",
                    "conclusion": "failure",
                    "run_number": 1,
                },
            ]
        )
        assert verdict == "none", "push 事件 run 不得判定 pull_request 轮询"


# ---------------------------------------------------------------------------
# ⑦ ci-ok 消费面回归（四实参接口不变）
# ---------------------------------------------------------------------------


class TestCiOkInvocationUnchanged:
    """ci.yml 对 poller 的四实参调用不因早退/守卫改动而漂移。"""

    def test_ci_invocation_keeps_four_args(self):
        ci_raw = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        m = re.search(
            r"bash scripts/check_droid_review\.sh \\\n"
            r"\s+\"\$\{\{ github\.event_name \}\}\" \\\n"
            r"\s+\"\$\{\{ github\.repository \}\}\" \\\n"
            r"\s+\"\$\{\{ github\.event\.pull_request\.head\.sha \|\| github\.sha \}\}\" \\\n"
            r"\s+\"\$GH_TOKEN\"",
            ci_raw,
        )
        assert m, "ci.yml 必须保持四实参调用（event/repo/sha/token 顺序锁定）"

    def test_livetruth_probe_contract_updated_to_90(self):
        """T1-04 探针契约同步 90×30s（防探针与实现漂移双红）。"""
        probe_raw = (REPO_ROOT / "scripts" / "livetruth_probes.py").read_text()
        assert 'attempts.group(1) == "90"' in probe_raw, "livetruth T1-04 探针必须同步 90×30s 契约"
        assert "MAX_ATTEMPTS=90 × 30s" in probe_raw, "T1-14 文档锚 needle 必须同步新预算字面量"
