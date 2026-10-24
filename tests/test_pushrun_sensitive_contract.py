#!/usr/bin/env python3
"""push-run 条件跳过契约（VAL-F-001）。

背景：test_trigger_release_contract / test_budget_gate 中依赖「后台派发子壳
在固定 sleep 内完成」或「50 次种子循环 < 30s」的用例，在 CI main push-run
（root + -n 8 负载 runner）上轮换红；同内容 PR 上下文与本地全绿
（三环境对比：validation/m1-recovery/VAL-M1-005.preexisting-red-analysis.txt）。

修复形态（mission 授权方案①）：pytest marker（pushrun_sensitive）+
GITHUB_EVENT_NAME 条件判断——仅 push 事件语境跳过，PR 上下文与本地行为
严格不变；断言本体零删除。

本契约双向锁死该修复：
1. 静态完备性：竞态族全部成员必须带 pushrun_sensitive 标记（防漏标/被摘除）；
2. 行为三向：push 语境 → 族内跳过、族外对照照常跑；pull_request 语境 →
   全部照常跑（PR 行为不变）；本地（无环境锚）→ 全部照常跑。

竞态族成员资格判据（新增用例时对照）：
- 断言依赖后台子壳（trigger-release.sh 的 `( ... ) &` 派发腿）在固定
  sleep 内写完日志/捕获文件；或
- 单进程串行种子循环（ledger_claim ×50）在 30s subprocess timeout 内
  必须完成——两者都在负载 runner 上不可保证。
主进程同步落盘的日志行（锁已创建/幂等跳过/后台派发已启动）不在此列。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER = "pushrun_sensitive"

# 竞态族全量成员（模块, 类, 用例）。CI 实红观测（8/7 见
# validation/m1-recovery/VAL-M1-005.preexisting-red-analysis.txt 与
# run 38255243934 --log-failed）：session_id_extracted（稳定）、
# multiple_consumers（稳定）、droid_called_with_correct_options、
# diverged_repo_skipped_no_force、metadata_contains_tag、
# default_budget_is_50（TimeoutExpired）；其余为同形态同株成员
# （同一后台派发/固定 sleep 设计，轮换风险池）。
RACE_FAMILY: dict[str, list[tuple[str, str]]] = {
    "test_trigger_release_contract.py": [
        ("TestErrorIsolation", "test_droid_exec_failure_isolated"),
        ("TestErrorIsolation", "test_dirty_tree_skipped"),
        ("TestErrorIsolation", "test_diverged_repo_skipped_no_force"),
        ("TestCleanRepoFastForward", "test_clean_repo_pulls_before_dispatch"),
        ("TestErrorIsolationMultipleConsumers", "test_multiple_consumers_one_fails_others_succeed"),
        ("TestDroidCallShape", "test_droid_called_with_correct_options"),
        ("TestDroidCallShape", "test_metadata_contains_tag_sourceRepo_triggerSource"),
        ("TestDroidCallShape", "test_session_id_extracted_to_log"),
        ("TestBackgroundDispatch", "test_script_returns_immediately_dispatch_runs_in_background"),
    ],
    "test_budget_gate.py": [
        ("TestBudgetOverLimitTelemetry", "test_default_budget_is_50_when_unset_or_empty"),
    ],
}

# 族外对照（负载不敏感：主进程同步语义，任何语境都不得被跳过）
CONTROL_NODE = (
    "tests/test_budget_gate.py::TestBudgetContractGuards::"
    "test_missing_source_arg_is_usage_error_no_side_effects"
)
# 族内最快成员（零 sleep：脏树跳过在子壳毫秒级路径上，跑起来 <5s）
FAST_FAMILY_NODE = (
    "tests/test_trigger_release_contract.py::TestErrorIsolation::test_dirty_tree_skipped"
)


def _marker_present(module: object, cls_name: str, fn_name: str) -> bool:
    cls = getattr(module, cls_name)
    fn = getattr(cls, fn_name)
    marks = getattr(fn, "pytestmark", [])
    return any(getattr(m, "name", "") == MARKER for m in marks)


def test_race_family_fully_marked_pushrun_sensitive():
    """竞态族每一成员都带 pushrun_sensitive 标记（漏标/被摘除即红）。"""
    from tests import test_budget_gate, test_trigger_release_contract

    modules = {
        "test_trigger_release_contract.py": test_trigger_release_contract,
        "test_budget_gate.py": test_budget_gate,
    }
    missing: list[str] = []
    for filename, members in RACE_FAMILY.items():
        module = modules[filename]
        for cls_name, fn_name in members:
            if not _marker_present(module, cls_name, fn_name):
                missing.append(f"{filename}::{cls_name}::{fn_name}")
    assert not missing, (
        f"竞态族成员缺少 pushrun_sensitive 标记（push-run 条件跳过失效，VAL-F-001 回归）: {missing}"
    )


def _run_inner_pytest(nodes: list[str], event_name: str | None) -> subprocess.CompletedProcess:
    """子进程跑真实树的指定节点，返回 CompletedProcess。

    显式设/清 GITHUB_EVENT_NAME 以模拟三种语境（push / pull_request / 本地）。
    -p no:cacheprovider：内层运行不与外层并发争 .pytest_cache。
    """
    env = os.environ.copy()
    if event_name is None:
        env.pop("GITHUB_EVENT_NAME", None)
    else:
        env["GITHUB_EVENT_NAME"] = event_name
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-cov", "-rs", "-p", "no:cacheprovider", *nodes],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_push_event_context_skips_family_but_not_control():
    """push 语境：族内成员 skipped、族外对照照常 passed。"""
    r = _run_inner_pytest([FAST_FAMILY_NODE, CONTROL_NODE], event_name="push")
    assert r.returncode == 0, f"push 语境应全绿（跳过+放行），实况:\n{r.stdout}\n{r.stderr}"
    assert "1 skipped" in r.stdout, f"族内成员应在 push 语境被跳过:\n{r.stdout}"
    assert "1 passed" in r.stdout, f"族外对照不应被跳过:\n{r.stdout}"
    assert "VAL-F-001" in r.stdout, f"跳过原因应可溯源到 VAL-F-001:\n{r.stdout}"


def test_pr_context_and_local_keep_running_family():
    """pull_request 语境与本地（无锚）语境：族内成员照常跑（PR 行为不变）。"""
    for event_name in ("pull_request", None):
        r = _run_inner_pytest([FAST_FAMILY_NODE], event_name=event_name)
        context_label = event_name or "local"
        assert r.returncode == 0, f"{context_label} 语境族内成员应照常跑:\n{r.stdout}\n{r.stderr}"
        assert "1 passed" in r.stdout, (
            f"{context_label} 语境族内成员不得被跳过（PR 行为不变铁律）:\n{r.stdout}"
        )
        assert "skipped" not in r.stdout, f"{context_label} 语境出现意外跳过:\n{r.stdout}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
