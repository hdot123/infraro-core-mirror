"""Quality Gate workflow structure contract (governance mission M1, VAL-CORE-002).

2026-09-19 quality-gate refactor (feature core-quality-gate-and-drift):

The branch ruleset's required checks moved from {ci-ok, qa-ok, droid-review}
to {quality-gate, droid-review, substrate-gate-suite} (fixes the #85 drift
where substrate-gate-suite was required but no live check produced it).

This file locks the structural invariants of the aggregation workflow:

* check context is exactly ``quality-gate`` (job key AND display name — the
  display name is what the ruleset matches);
* the aggregation surface is ci-ok + substrate-gate-suite + (PR-only) qa-ok;
* droid-review is NOT in the surface (independently required; ci-ok already
  waits for it — duplicating the 10-35 min poll here would double latency);
* push events drop qa-ok from the expected set (QA has no push trigger — an
  always-expected qa-ok would deadlock every main push);
* runs on both pull_request and push to main (a required check that skips
  the push event would be permanently pending on main);
* no top-level or job-level write permissions (read-only gate);
* no paths filter on triggers (paths filters would make the required check
  permanently pending for skipped PRs — same class as
  TestRequiredCheckReachability in test_workflow_permissions_contract.py).
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
QG_YML = REPO_ROOT / ".github/workflows/quality-gate.yml"


@pytest.fixture(scope="module")
def qg_doc() -> dict[str, Any]:
    doc = yaml.safe_load(QG_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "quality-gate.yml 必须是 YAML mapping"
    return doc


@pytest.fixture(scope="module")
def qg_script(qg_doc: dict[str, Any]) -> str:
    """拼接 quality-gate job 全部 run 步骤脚本文本。"""
    jobs = qg_doc.get("jobs")
    assert isinstance(jobs, dict) and "quality-gate" in jobs, "必须有 quality-gate job"
    steps = jobs["quality-gate"].get("steps") or []
    runs = [str(step["run"]) for step in steps if "run" in step]
    assert runs, "quality-gate job 必须包含至少一个 run 步骤"
    return "\n".join(runs)


class TestQualityGateNaming:
    def test_workflow_name(self, qg_doc: dict[str, Any]) -> None:
        """workflow 名为 'Quality Gate'（面板可读；与 check 名区分）。"""
        assert qg_doc["name"] == "Quality Gate"

    def test_check_context_is_quality_gate(self, qg_doc: dict[str, Any]) -> None:
        """job key 与显示名都是 quality-gate —— ruleset required context 精确匹配。"""
        job = qg_doc["jobs"]["quality-gate"]
        assert job.get("name") == "quality-gate", (
            "job 显示名必须字节级为 quality-gate（ruleset required context 按显示名匹配，"
            "改名 = 永久 pending 阻断合并）"
        )


class TestQualityGateTriggers:
    def test_pull_request_and_push_triggers(self, qg_doc: dict[str, Any]) -> None:
        """required check 必须双触发：PR + main push（漏 push 面 = main 上永久 pending）。"""
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        assert "pull_request" in triggers, "必须有 pull_request 触发"
        assert "push" in triggers, "必须有 push 触发（main 面聚合验证）"

    def test_no_paths_filter(self, qg_doc: dict[str, Any]) -> None:
        """触发器禁止 paths/paths-ignore（同 TestRequiredCheckReachability 语义）。"""
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        for _event, config in triggers.items():
            if isinstance(config, dict):
                assert "paths" not in config, "quality-gate 触发器禁止 paths 过滤"
                assert "paths-ignore" not in config, "quality-gate 触发器禁止 paths-ignore"

    def test_not_reusable_workflow_call(self, qg_doc: dict[str, Any]) -> None:
        """quality-gate 不是 reusable（workflow_call 顶层 concurrency 禁令与
        跨仓引用陷阱均不适用；本仓聚合器必须独立成 check）。"""
        triggers = qg_doc.get(True) or qg_doc.get("on") or {}
        assert "workflow_call" not in triggers


class TestQualityGateAggregationSurface:
    def test_aggregates_ci_ok_and_substrate(self, qg_script: str) -> None:
        """聚合面必须含 ci-ok 与 substrate-gate-suite。"""
        assert '"ci-ok"' in qg_script or "ci-ok" in qg_script
        assert "substrate-gate-suite" in qg_script

    def test_qa_surface_pr_only(self, qg_script: str) -> None:
        """qa-ok 只在 pull_request 事件面纳入（QA 无 push 触发，
        push 面恒等 qa-ok 会死锁 main 验证）。"""
        assert "qa-ok" in qg_script
        assert '"$EVENT_NAME" = "pull_request"' in qg_script

    def test_droid_review_not_in_surface(self, qg_script: str) -> None:
        """droid-review 不进聚合面（独立 required context + ci-ok 已等待，
        重复轮询 = 双倍 10-35 分钟延迟）。"""
        # 聚合面的期望清单里不得出现 droid-review
        assert 'EXPECTED_CHECKS="ci-ok substrate-gate-suite"' in qg_script
        # jq 选择器同样只圈定三个聚合面 check
        assert '"droid-review"' not in qg_script

    def test_zero_red_inherited(self, qg_script: str) -> None:
        """任何非 success 结论即失败（零红继承，不允许 skip 吸收红项）。"""
        assert '"completed/success"' in qg_script
        assert "FAILED=1" in qg_script
        assert '"$FAILED" -eq 1' in qg_script


class TestQualityGatePermissions:
    def test_top_level_read_only(self, qg_doc: dict[str, Any]) -> None:
        """顶层权限只读（F4 契约：新 workflow 必须有顶层 baseline）。

        checks: read 为私有仓 check-runs API 必需（公开期匿名可读；flip 后
        缺此权限时响应无 check_runs → 聚合恒 pending 至超时）。"""
        perms = qg_doc.get("permissions")
        assert perms is not None, "必须有顶层 permissions baseline"
        assert set(perms) == {"contents", "checks"}
        assert set(perms.values()) == {"read"}, "只读面：任何 write 权限都违约"

    def test_job_level_read_only(self, qg_doc: dict[str, Any]) -> None:
        """job 级权限只读（聚合轮询零写面）；checks: read 同顶层理由。"""
        job = qg_doc["jobs"]["quality-gate"]
        assert job.get("permissions") == {"contents": "read", "checks": "read"}


class TestQualityGateRunnerAndTimeout:
    def test_runs_on(self, qg_doc: dict[str, Any]) -> None:
        assert qg_doc["jobs"]["quality-gate"].get("runs-on") == ["self-hosted", "pve-linux"]

    def test_timeout_bounded(self, qg_doc: dict[str, Any]) -> None:
        """超时上限存在（轮询型 job 必须有界，防 runner 永久占用）。"""
        assert qg_doc["jobs"]["quality-gate"].get("timeout-minutes") is not None

    def test_timeout_covers_slow_review_tail(self, qg_doc: dict[str, Any], qg_script: str) -> None:
        """预算必须覆盖实证慢尾（PR #248，2026-10-04 死锁回归锁）。

        事故：droid-review 单跑 81 分钟 + runner 排队，ci-ok 生命周期超 65
        分钟；旧预算 130×30s 超时后 quality-gate 先红，其 failure 又被 ci-ok
        零红扫描判红（环形引用），同 SHA 上任何 rerun 均无法转绿。
        修复双管：零红扫描排除 quality-gate（test_check_zero_red.py）+ 本处
        预算扩容。锁定：MAX_ATTEMPTS=200（≈100min）且 job timeout ≥ 聚合窗
        +10min 缓冲（轮询循环退出、上传日志等收尾时间）。
        """
        job = qg_doc["jobs"]["quality-gate"]
        m = re.search(r"^\s*MAX_ATTEMPTS=(\d+)\s*$", qg_script, re.M)
        assert m, "quality-gate.yml 必须保留 MAX_ATTEMPTS 常量（聚合窗真源）"
        attempts = int(m.group(1))
        assert attempts >= 200, (
            f"聚合窗 MAX_ATTEMPTS={attempts} 不得低于 200（PR #248 实证 81min "
            "droid-review 慢尾 + 排队 → 130×30s 死锁）"
        )
        timeout = job.get("timeout-minutes")
        assert timeout is not None and timeout >= attempts * 30 / 60 + 10, (
            f"job timeout {timeout}min 必须 ≥ 聚合窗 {attempts * 30 / 60}min + 10min 收尾缓冲"
        )
