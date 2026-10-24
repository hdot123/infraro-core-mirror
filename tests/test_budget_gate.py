#!/usr/bin/env python3
"""budget-gate 行为契约（VAL-BUD-001，a′ 收门 observe-first 阶段）。

ledger_budget_check <source> [kind] 语义（architecture §3.1，mission m3）：
- 当日窗口（UTC 自然日）按 source（可选 kind）统计已记账派发记录数，
  基准 = status/ledger/*.json 记录本体的 claimedAt 前缀
- 上限 = DISPATCH_DAILY_BUDGET_<SOURCE> 覆盖（source 大写、- 转 _），
  缺省回退 DISPATCH_DAILY_BUDGET_DEFAULT，再缺省 = 50
- 超预算 → stderr WARN + budget_over_limit 遥测（PostHog + events.jsonl
  审计双写）→ rc=0 放行。**任何拒发返回码都违规**（observe-first 裁定）
- 基建不可用 → budget_unavailable + WARN → rc=0 放行（fail-open，账本哲学）
- POSTHOG_DRY_RUN=1 隔离：PostHog 面由 lib/posthog.sh 守卫（打印不发送），
  events.jsonl 审计一并静默（沙盒零残留）；WARN 告警仍在

遥测观测面说明：沙盒无 POSTHOG_API_KEY，PostHog 发送被其内部跳过；
events.jsonl 审计记录（ledger_append）是本地可观测面，与 key 无关——
断言遥测以 events.jsonl 为准，DRY_RUN 断言以 posthog.sh 的 stdout 打印为准。

沙盒模式同 test_dispatch_ledger.py Part A：临时 WEBHOOK_BASE + 独立 bash 进程
source 真源库，零生产接触（禁写生产 ~/.factory/webhook/status/ledger/）。

竞态族标注（VAL-F-001）：大规模串行种子循环（×50）在 30s subprocess timeout
内的用例带 pushrun_sensitive 标记——push 语境条件跳过，PR/本地照常；
种子超时已按循环规模放宽（_seed_claims），契约见
tests/test_pushrun_sensitive_contract.py。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.ledger_check_helpers import (
    make_base,
    read_events,
    run_ledger_fn,
)

BUDGET_SOURCE = "trigger-release"
OTHER_SOURCE = "wiki-refresh"
# 运行时取真实 UTC 日期（硬编码日期 = 时间炸弹，跨日即全线翻车）；
# budget 侧窗口与 seed 侧 claimedAt 同用 LEDGER_BASE_TS 时间旅行钩子对齐。
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
_yest = datetime.now(timezone.utc).toordinal() - 1
YESTERDAY = datetime.fromordinal(_yest).strftime("%Y-%m-%d")


def _seed_claims(
    base: Path,
    count: int,
    *,
    source: str = BUDGET_SOURCE,
    kind: str = "linear-gateway",
    day: str = TODAY,
) -> None:
    """单 bash 进程内批量落 N 条已记账派发记录（走真 ledger_claim）。

    claimedAt 用 LEDGER_BASE_TS 回写到指定 UTC 日（账本库既有测试钩子），
    使计数窗口断言与 runner 实际日期解耦。
    """
    lines = []
    for seq in range(1, count + 1):
        uuid = f"b0d9e2a4-7777-4c0e-9f1a-{seq:012d}"
        lines.append(
            f'LEDGER_BASE_TS="{day}T12:00:00Z" ledger_claim "{uuid}" "{source}" '
            f'"INFRA-{2000 + seq}" "seed" "INFRA" "{kind}"'
        )
    # VAL-F-001：负载 runner 上单条 ledger_claim 可达 ~1s，超时按循环规模放宽
    # （run 38389808723 实证 ×50 在 30s 内未完成 → TimeoutExpired 轮换红）
    r = run_ledger_fn("\n".join(lines) + '\necho "seeded"', base, timeout=30 + count)
    assert "seeded" in r.stdout, f"seed failed: {r.stdout} {r.stderr}"


def _run_budget_check(
    base: Path,
    *,
    source: str = BUDGET_SOURCE,
    kind: str | None = None,
    default: str = "2",
    override: dict[str, str] | None = None,
    dry_run: bool = False,
    env_extra: dict[str, str] | None = None,
):
    env: dict[str, str] = {"DISPATCH_DAILY_BUDGET_DEFAULT": default}
    env.update(override or {})
    env.update(env_extra or {})
    if dry_run:
        env["POSTHOG_DRY_RUN"] = "1"
    # 窗口日期钉死到 TODAY（LEDGER_BASE_TS 钩子，与 seed 侧 claimedAt 同源），
    # 消除 seed 与 check 之间的跨午夜竞态；env_extra 可显式覆盖。
    env.setdefault("LEDGER_BASE_TS", f"{TODAY}T12:00:00Z")
    kind_arg = f' "{kind}"' if kind else ""
    return run_ledger_fn(f'ledger_budget_check "{source}"{kind_arg}; echo "rc=$?"', base, env)


def _budget_events(base: Path) -> list[dict]:
    """budget_over_limit / budget_unavailable 遥测事件（budget_ok 是正常计量
    留痕，PR #30x 起与告警事件并存，不算遥测告警——告警断言不含它）。"""
    return [
        e
        for e in read_events(base)
        if str(e.get("event", "")).startswith("budget") and e.get("event") != "budget_ok"
    ]


def _override_var(source: str) -> str:
    return "DISPATCH_DAILY_BUDGET_" + source.upper().replace("-", "_")


# ============================================================================
# 日窗口计数
# ============================================================================


class TestBudgetDailyWindow:
    def test_under_limit_passes_clean_without_events(self, tmp_path):
        base = make_base(tmp_path)
        r = _run_budget_check(base, default="2")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert "WARN" not in r.stderr
        assert _budget_events(base) == [], "未超限不得产生任何 budget 遥测"

    def test_counts_only_current_day_for_same_source(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 1)
        r = _run_budget_check(base, default="2")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert _budget_events(base) == []

    def test_other_sources_do_not_count_into_window(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2, source=OTHER_SOURCE)
        r = _run_budget_check(base, default="2")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert _budget_events(base) == [], "他 source 计数不得计入本 source 窗口"

    def test_day_boundary_resets_window(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2, day=YESTERDAY)
        r = _run_budget_check(base, default="2")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert _budget_events(base) == [], "跨日必须重置窗口（昨日记录不计入今日）"

    def test_kind_scoping_isolated(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2, kind="linear-gateway")
        r = _run_budget_check(base, kind="wiki-refresh", default="2")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert _budget_events(base) == [], "kind 维度必须隔离计数"


# ============================================================================
# 超预算：遥测 + 放行（绝不拒发）
# ============================================================================


class TestBudgetOverLimitTelemetry:
    def test_over_limit_warns_telemetrizes_and_passes(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2)
        r = _run_budget_check(base, default="2")
        assert r.returncode == 0, "超预算必须放行（observe-first，拒发即违规）"
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_over_limit" in r.stderr
        events = _budget_events(base)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "budget_over_limit"
        assert f"source={BUDGET_SOURCE}" in ev["detail"]
        assert "count=2" in ev["detail"]
        assert "limit=2" in ev["detail"]

    def test_over_limit_dry_run_no_audit_but_still_passes(self, tmp_path):
        """POSTHOG_DRY_RUN=1：posthog.sh 打印不发送，events.jsonl 审计静默，WARN 仍在。"""
        base = make_base(tmp_path)
        _seed_claims(base, 2)
        r = _run_budget_check(base, default="2", dry_run=True)
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_over_limit" in r.stderr
        assert "[POSTHOG_DRY_RUN]" in r.stdout, "dry-run 面由 lib/posthog.sh 守卫（打印不发送）"
        assert "budget_over_limit" in r.stdout
        assert _budget_events(base) == [], "DRY_RUN=1 不得落遥测审计事件"

    def test_never_rejects_under_set_e(self, tmp_path):
        """任何拒发返回码都违规：set -e 下超限路径必须存活（rc=0）。"""
        base = make_base(tmp_path)
        _seed_claims(base, 2)
        r = run_ledger_fn(
            f'set -e\nledger_budget_check "{BUDGET_SOURCE}"\necho "survived=$?"',
            base,
            {"DISPATCH_DAILY_BUDGET_DEFAULT": "2"},
        )
        assert r.returncode == 0
        assert "survived=0" in r.stdout

    def test_per_source_override_raises_limit(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2)
        r = _run_budget_check(base, default="1", override={_override_var(BUDGET_SOURCE): "3"})
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert _budget_events(base) == []

    def test_per_source_override_lower_than_default_applies(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 1)
        r = _run_budget_check(base, default="50", override={_override_var(BUDGET_SOURCE): "1"})
        assert r.returncode == 0
        assert "budget_over_limit" in r.stderr

    def test_override_of_other_source_not_applied(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claims(base, 2)
        r = _run_budget_check(base, default="1", override={_override_var(OTHER_SOURCE): "99"})
        assert r.returncode == 0
        assert "budget_over_limit" in r.stderr

    @pytest.mark.pushrun_sensitive
    def test_default_budget_is_50_when_unset_or_empty(self, tmp_path):
        """无 DEFAULT 配置（含空串视同未配置）时缺省上限 = 50。"""
        base = make_base(tmp_path)
        _seed_claims(base, 50)
        r = run_ledger_fn(
            f'ledger_budget_check "{BUDGET_SOURCE}"; echo "rc=$?"',
            base,
            {
                "DISPATCH_DAILY_BUDGET_DEFAULT": "",
                "LEDGER_BASE_TS": f"{TODAY}T12:00:00Z",
            },
        )
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert "budget_over_limit" in r.stderr
        events = _budget_events(base)
        assert len(events) == 1
        assert "limit=50" in events[0]["detail"]

    def test_corrupt_limit_config_fails_open_with_unavailable(self, tmp_path):
        """上限配置非数字 = 配置不可用 → budget_unavailable + 放行。"""
        base = make_base(tmp_path)
        _seed_claims(base, 1)
        r = _run_budget_check(base, default="not-a-number")
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_unavailable" in r.stderr


# ============================================================================
# 基建不可用：fail-open 放行
# ============================================================================


class TestBudgetUnavailableFailOpen:
    def _check_against_blocked_base(self, tmp_path: Path, *, dry_run: bool):
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        try:
            r = _run_budget_check(blocked, default="2", dry_run=dry_run)
        finally:
            os.chmod(blocked, 0o755)
        return r

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root 不受 0o500 写权限约束，宿主不可写语义不成立",
    )
    def test_unavailable_fails_open_with_warn(self, tmp_path):
        """宿主目录不可写 = 基建不可用 → budget_unavailable 事件 + WARN + 放行。"""
        r = self._check_against_blocked_base(tmp_path, dry_run=False)
        assert r.returncode == 0, "基建不可用必须放行（fail-open，账本哲学）"
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_unavailable" in r.stderr
        # 宿主不可写：审计面也落不了盘（无 events.jsonl 残留）
        assert not (tmp_path / "blocked" / "status" / "ledger" / "events.jsonl").exists()

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root 不受 0o500 写权限约束，宿主不可写语义不成立",
    )
    def test_unavailable_dry_run_silent_pass(self, tmp_path):
        """DRY_RUN=1 下 unavailable 事件只打印不发送（print 面即隔离证明）。"""
        r = self._check_against_blocked_base(tmp_path, dry_run=True)
        assert r.returncode == 0
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_unavailable" in r.stderr
        assert "[POSTHOG_DRY_RUN]" in r.stdout, "unavailable 事件必须走 dry-run 打印面"
        assert "budget_unavailable" in r.stdout
        # 宿主不可写：审计面落不了盘（零残留）
        assert not (tmp_path / "blocked" / "status" / "ledger" / "events.jsonl").exists()

    def test_unavailable_writes_no_telemetry_events(self, tmp_path):
        """count_probe_failed 分支（PYTHON_BIN 失效）真实触达性验证。

        前置：先 seed 一条 claim 使账本目录就绪，绕过 fresh-dir 懒创建
        短路（_ledger_init 早退路径不经过计数探针，空账本上跑本用例是
        空洞断言）。探针失败后：
        - audit 面（ledger_append，纯 bash）不受 PYTHON_BIN 影响，
          events.jsonl 必落 budget_unavailable/count_probe_failed 事件；
        - PostHog 面（posthog.sh）在沙盒无 key 时自跳过，零外发。
        """
        base = make_base(tmp_path)
        _seed_claims(base, 1)
        r = _run_budget_check(base, default="2", env_extra={"PYTHON_BIN": "/nonexistent/python"})
        assert r.returncode == 0, "探针失败必须 fail-open 放行"
        assert "rc=0" in r.stdout
        assert "WARN" in r.stderr
        assert "budget_unavailable" in r.stderr
        assert "count_probe_failed" in r.stderr
        events = _budget_events(base)
        assert len(events) == 1, "探针失败分支的审计事件必须真实落盘"
        assert events[0]["event"] == "budget_unavailable"
        assert "count_probe_failed" in events[0]["detail"]


# ============================================================================
# 契约守卫
# ============================================================================


class TestBudgetContractGuards:
    def test_missing_source_arg_is_usage_error_no_side_effects(self, tmp_path):
        """缺 source 参数 = 用法错误（rc=1，精确匹配），但与派发路径无关、零遥测副作用。"""
        base = make_base(tmp_path)
        r = run_ledger_fn('ledger_budget_check; echo "rc=$?"', base)
        assert r.stdout.strip().splitlines()[-1] == "rc=1"
