#!/usr/bin/env python3
"""record-dispatch.sh + droid-task 接收闸契约（VAL-BUD-004，a′ 收门 observe-first）。

record-dispatch.sh <source> <ref> [kind] [title] 语义（architecture §3.4）：
- source lib → ledger_budget_check（超预算 WARN+遥测，rc 恒 0 放行）→
  ledger_claim + ledger_append 记账
- observe-first 裁定：本脚本任何路径都不得阻塞派发——内部全 best-effort，
  恒 exit 0（缺参/lib 缺失/账本不可用只 WARN）；调用方 workflow 步骤再兜
  || true 双保险。**任何拒发路径都违规**
- 接收闸步骤（Budget record (observe-only)）：Gate A 同层、Execute droid 之前，
  payload 值经 env 注入（VAL-PILOT-008 注入面契约），带 step id；仅新增本步骤，
  Gate A 与下游全部步骤的 if 条件链零改动

沙盒模式同 test_budget_gate.py：临时 WEBHOOK_BASE + 独立 bash 进程，零生产接触
（禁写生产 ~/.factory/webhook/status/ledger/）。遥测断言以 events.jsonl 与
stderr WARN 为准（沙盒无 POSTHOG_API_KEY，PostHog 发送面自跳过）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from tests.ledger_check_helpers import make_base, read_events, run_ledger_fn
from tests.shellcheck_helpers import assert_shellcheck_clean

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORD_DISPATCH = REPO_ROOT / "webhook-scripts" / "record-dispatch.sh"
MANIFEST = REPO_ROOT / "webhook-scripts" / "MANIFEST.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "droid-task.yml"

SOURCE = "droid-task"
KIND = "linear-gateway"
REF = "INFRA-2100"
# 与 test_budget_gate.py 同款：runtime 真实 UTC 日期 + LEDGER_BASE_TS 时间旅行
# 钩子对齐 seed 与记账窗口，消除跨午夜竞态。
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def run_record_dispatch(
    base: Path,
    args: list[str],
    *,
    env_extra: dict[str, str] | None = None,
    dry_run: bool = False,
) -> subprocess.CompletedProcess:
    """独立 bash 进程内执行真源 record-dispatch.sh（WEBHOOK_BASE 指向沙盒）。"""
    env = os.environ.copy()
    # 与 ledger_check_helpers prelude 同款沙盒隔离：清外发 key 与回执落点
    env.pop("POSTHOG_API_KEY", None)
    env.pop("LOG_FILE", None)
    env["WEBHOOK_BASE"] = str(base)
    env["PYTHON_BIN"] = "python3"
    env["LEDGER_BASE_TS"] = f"{TODAY}T12:00:00Z"
    if dry_run:
        env["POSTHOG_DRY_RUN"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(RECORD_DISPATCH), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _seed_claim(base: Path, *, source: str = SOURCE, kind: str = KIND, ref: str = REF) -> None:
    r = run_ledger_fn(
        f'ledger_claim "a1b2c3d4-0000-4000-8000-000000000001" "{source}" "{ref}" '
        f'"seed" "INFRA" "{kind}"; echo "seeded"',
        base,
        {"LEDGER_BASE_TS": f"{TODAY}T12:00:00Z"},
    )
    assert "seeded" in r.stdout, f"seed failed: {r.stdout} {r.stderr}"


def _budget_events(base: Path) -> list[dict]:
    """budget_over_limit / budget_unavailable 遥测事件（budget_ok 是正常计量
    留痕，与告警事件并存，不算遥测告警——告警断言不含它；口径对齐
    tests/test_budget_gate.py 的同名 helper）。"""
    return [
        e
        for e in read_events(base)
        if str(e.get("event", "")).startswith("budget") and e.get("event") != "budget_ok"
    ]


def _records_for_source(base: Path, source: str) -> list[dict]:
    ledger_dir = base / "status" / "ledger"
    if not ledger_dir.is_dir():
        return []
    out = []
    for f in ledger_dir.glob("*.json"):
        rec = json_load(f)
        if rec and rec.get("source") == source:
            out.append(rec)
    return out


def json_load(path: Path) -> dict | None:
    import json

    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ============================================================================
# record-dispatch.sh 沙盒行为
# ============================================================================


class TestRecordDispatchSandbox:
    def test_happy_path_claims_record_and_event(self, tmp_path):
        base = make_base(tmp_path)
        r = run_record_dispatch(base, [SOURCE, REF, KIND, "card title"])
        assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
        assert "WARN" not in r.stderr
        records = _records_for_source(base, SOURCE)
        assert len(records) == 1, "应恰好落一条记账记录"
        assert records[0]["linearRef"] == REF
        assert records[0]["kind"] == KIND
        assert records[0]["title"] == "card title"
        attempted = [e for e in read_events(base) if e.get("event") == "dispatch_attempted"]
        assert len(attempted) == 1, "dispatch_attempted 应留痕事件流"
        assert _budget_events(base) == [], "未超限不得产生 budget 遥测"

    def test_under_limit_no_budget_events(self, tmp_path):
        base = make_base(tmp_path)
        r = run_record_dispatch(
            base, [SOURCE, REF, KIND], env_extra={"DISPATCH_DAILY_BUDGET_DEFAULT": "50"}
        )
        assert r.returncode == 0
        assert _budget_events(base) == []

    def test_over_limit_warns_telemetrizes_and_still_records(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claim(base)
        r = run_record_dispatch(
            base,
            [SOURCE, REF, KIND],
            env_extra={"DISPATCH_DAILY_BUDGET_DROID_TASK": "1"},
        )
        # observe-first 核心：超预算只告警放行，记账照常落（= 派发照常进行）
        assert r.returncode == 0, f"超限不得产生非零 rc: {r.stderr}"
        assert "budget_over_limit" in r.stderr, "超限必须 WARN 可观测"
        over = [e for e in _budget_events(base) if e.get("event") == "budget_over_limit"]
        assert len(over) == 1, "超限必须发 budget_over_limit 遥测"
        assert len(_records_for_source(base, SOURCE)) == 2, "超限后新派发仍须记账（无拒发路径）"

    def test_fail_open_when_ledger_unavailable(self, tmp_path):
        # WEBHOOK_BASE 指向普通文件 → mkdir 建不出账本目录 → 基建不可用
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        r = run_record_dispatch(blocker, [SOURCE, REF, KIND])
        assert r.returncode == 0, "账本不可用必须 fail-open exit 0"
        assert "budget_unavailable" in r.stderr
        assert "fail-open" in r.stderr

    def test_lib_missing_metering_disabled_exit_zero(self, tmp_path):
        # 脚本被复制到无 lib/ 的目录（模拟 sync 漂移窗口）：只 WARN 禁用计量，exit 0
        orphan_dir = tmp_path / "orphan"
        orphan_dir.mkdir()
        shutil.copy2(RECORD_DISPATCH, orphan_dir / "record-dispatch.sh")
        env = os.environ.copy()
        env.pop("POSTHOG_API_KEY", None)
        env.pop("LOG_FILE", None)
        env["PYTHON_BIN"] = "python3"
        r = subprocess.run(
            ["bash", str(orphan_dir / "record-dispatch.sh"), SOURCE, REF, KIND],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert r.returncode == 0, "lib 缺失不得产生非零 rc"
        assert "lib unavailable" in r.stderr

    def test_missing_args_exit_zero(self, tmp_path):
        base = make_base(tmp_path)
        r = run_record_dispatch(base, [])
        assert r.returncode == 0, "缺参只 WARN 用法，不得阻塞调用方"
        assert "usage" in r.stderr.lower()

    def test_dry_run_isolates_telemetry(self, tmp_path):
        base = make_base(tmp_path)
        _seed_claim(base)
        r = run_record_dispatch(
            base,
            [SOURCE, REF, KIND],
            env_extra={"DISPATCH_DAILY_BUDGET_DROID_TASK": "1"},
            dry_run=True,
        )
        assert r.returncode == 0
        assert "[POSTHOG_DRY_RUN]" in r.stdout, "DRY_RUN 下遥测打印不发送"
        assert _budget_events(base) == [], "DRY_RUN 下 budget 审计留痕一并静默"
        assert len(_records_for_source(base, SOURCE)) == 2, "DRY_RUN 只隔离遥测不隔离记账"


# ============================================================================
# 仓内受管面
# ============================================================================


class TestRecordDispatchSurface:
    def test_shellcheck_clean(self):
        assert_shellcheck_clean(RECORD_DISPATCH)

    def test_manifest_registered(self):
        text = MANIFEST.read_text(encoding="utf-8")
        assert '"record-dispatch.sh"' in text, "record-dispatch.sh 必须注册进 MANIFEST 受管清单"

    def test_script_exists_and_executable(self):
        assert RECORD_DISPATCH.is_file()
        assert os.access(RECORD_DISPATCH, os.X_OK), "同步部署需要可执行位"


# ============================================================================
# droid-task 接收闸步骤契约（VAL-BUD-004）
# ============================================================================


def _load_workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _steps(data: dict) -> list[dict]:
    return data["jobs"]["execute"]["steps"]


def _step_by_name(data: dict, name: str) -> dict:
    for s in _steps(data):
        if s.get("name") == name:
            return s
    raise AssertionError(f"step not found: {name}")


class TestDroidTaskBudgetStepContract:
    def test_budget_step_exists_between_gate_a_and_execute(self):
        data = _load_workflow()
        names = [s.get("name", "") for s in _steps(data)]
        assert "Budget record (observe-only)" in names, "接收闸预算步骤缺失"
        assert (
            names.index("Pre-flight (Linear Gate A)")
            < names.index("Budget record (observe-only)")
            < names.index("Execute droid")
        ), "预算步骤须在 Gate A 之后、Execute droid 之前"

    def test_budget_step_id_and_no_rejection_path(self):
        data = _load_workflow()
        step = _step_by_name(data, "Budget record (observe-only)")
        assert step.get("id") == "budget_record", "预算步骤必须带 step id（Gate A 模式）"
        run = step.get("run", "")
        assert "|| true" in run, "步骤体必须 || true 兜底（observe-only 无拒发路径）"
        assert "GITHUB_OUTPUT" not in run, "预算步骤不得产出条件输出（不得成为闸门）"

    def test_budget_step_payload_via_env_not_inline(self):
        data = _load_workflow()
        step = _step_by_name(data, "Budget record (observe-only)")
        env = step.get("env", {})
        assert "B_REF" in env and "context.identifier" in env["B_REF"]
        assert "B_KIND" in env and "client_payload.kind" in env["B_KIND"]
        assert "client_payload" not in step.get("run", ""), "run 体内联 payload 表达式=注入面违规"

    def test_gate_a_condition_chain_unchanged(self):
        """下游步骤 if 条件链逐一锁定（本 feature 只准新增步骤，不准改条件）。"""
        data = _load_workflow()
        expected = {
            "Pre-flight (Linear Gate A)": "github.event.client_payload.kind == 'linear-gateway'",
            "Checkout target repo": "steps.linear_gate.outputs.skip != 'true'",
            "Configure git identity": "steps.linear_gate.outputs.skip != 'true'",
            "Build prompt (error-gateway)": (
                "github.event.client_payload.kind == 'error-gateway' "
                "&& steps.linear_gate.outputs.skip != 'true'"
            ),
            "Build prompt (linear-gateway)": (
                "github.event.client_payload.kind == 'linear-gateway' "
                "&& steps.linear_gate.outputs.skip != 'true'"
            ),
            "Execute droid": "steps.linear_gate.outputs.skip != 'true'",
            "Failure fallback (create Issue)": "failure() && steps.execute.outcome == 'failure'",
        }
        for name, cond in expected.items():
            step = _step_by_name(data, name)
            actual = step.get("if")
            assert actual == cond, f"{name} 条件链被改动: {actual!r} != {cond!r}"

    def test_actionlint_clean(self):
        if not shutil.which("actionlint"):
            pytest.skip("actionlint not installed")
        r = subprocess.run(
            ["actionlint", str(WORKFLOW)], capture_output=True, text=True, timeout=120
        )
        assert r.returncode == 0, f"actionlint failed:\n{r.stdout}\n{r.stderr}"
