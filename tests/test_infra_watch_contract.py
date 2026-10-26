"""infra-watch.yml 契约测试（VAL-A-001，基础设施异常巡查）。

锁定 .github/workflows/infra-watch.yml 的形状契约：
- 触发器：schedule */15 + workflow_dispatch（阈值可临时改写，供验证用）
- 监控自举不成立：唯一 job 跑 GitHub 托管 runner（ubuntu-latest），
  不依赖被监控的自建 runner
- 判定 A（runner-offline）：轮询 memory/infraro-core 两仓 runners API，
  在线数 < 阈值即投 PostHog 事件（属性含仓标识 / offline 名单 / online 数）
- 判定 B（stuck-run）：扫描两仓 queued/in_progress 且年龄超阈的 run，
  每条投事件（属性含 run id / 状态 / 年龄 / workflow 名）
- 投递面复用 error-gateway 链路语义：us.posthog.com/batch/ + POSTHOG_API_KEY，
  零自建告警面、零明文密钥
- 跨仓轮询走 PAT（secrets.DISPATCH_TOKEN）而非仓内默认 token，缺配 fail loud
- 投递失败显式失败（HTTP_CODE 非 2xx → ::error:: → job 红），不静默吞掉
- 零权限基线 + timeout 上界（workflow permissions 契约约束）

风格对齐 tests/test_release_announce_contract.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "infra-watch.yml"
WATCHED_REPOS = ("hdot123/memory", "hdot123/infraro-core")


def _load_workflow() -> dict[str, Any]:
    """Load and parse the infra-watch.yml workflow."""
    if not WORKFLOW_PATH.exists():
        pytest.fail(f"infra-watch.yml not found at {WORKFLOW_PATH}")
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "infra-watch.yml must be a YAML mapping"
    return doc


def _get_triggers(wf: dict[str, Any]) -> dict[str, Any]:
    """Extract trigger events. YAML parses 'on:' as boolean True key."""
    triggers = wf.get(True, wf.get("on", {}))
    if triggers is None:
        return {}
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return {t: None for t in triggers}
    return triggers


def _get_jobs(wf: dict[str, Any]) -> dict[str, Any]:
    """Return the jobs mapping."""
    jobs = wf.get("jobs", {})
    assert isinstance(jobs, dict) and jobs, "workflow must define non-empty jobs mapping"
    return jobs


def _all_run_scripts(wf: dict[str, Any]) -> str:
    """Concatenate all run step scripts across all jobs."""
    runs = []
    for job in _get_jobs(wf).values():
        for step in job.get("steps") or []:
            if "run" in step:
                runs.append(str(step["run"]))
    return "\n".join(runs)


def _all_step_env_blocks(wf: dict[str, Any]) -> dict[str, str]:
    """Merge every env block (workflow-level, job-level, step-level) into one mapping."""
    merged: dict[str, str] = {}
    for key, value in (wf.get("env") or {}).items():
        merged[key] = str(value)
    for job in _get_jobs(wf).values():
        for key, value in (job.get("env") or {}).items():
            merged[key] = str(value)
        for step in job.get("steps") or []:
            for key, value in (step.get("env") or {}).items():
                merged[key] = str(value)
    return merged


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return _load_workflow()


@pytest.fixture(scope="module")
def scripts(workflow: dict[str, Any]) -> str:
    return _all_run_scripts(workflow)


class TestTriggers:
    """定时巡查 + 手动触发（阈值改写入口）双触发器契约。"""

    def test_schedule_is_every_15_minutes(self, workflow: dict[str, Any]) -> None:
        """schedule 腿为 */15（巡查节奏与 AGENTS.md 定时口径一致）。"""
        triggers = _get_triggers(workflow)
        assert "schedule" in triggers, "必须含 schedule 触发器"
        crons = [s["cron"] for s in triggers["schedule"]]
        assert "*/15 * * * *" in crons, f"cron 应为 */15 * * * *，实际 {crons}"

    def test_workflow_dispatch_has_threshold_inputs(self, workflow: dict[str, Any]) -> None:
        """workflow_dispatch 必须暴露 min_online / stuck_age_minutes 阈值入参。

        这是验证路径（真实 offline 样本缺席时调高阈值制造判定命中）的契约面，
        也让 schedule 腿与 dispatch 腿共用同一套判定逻辑（无旁路代码）。
        """
        triggers = _get_triggers(workflow)
        assert "workflow_dispatch" in triggers, "必须含 workflow_dispatch 触发器"
        inputs = (triggers.get("workflow_dispatch") or {}).get("inputs") or {}
        assert "min_online" in inputs, "dispatch inputs 缺 min_online"
        assert "stuck_age_minutes" in inputs, "dispatch inputs 缺 stuck_age_minutes"


class TestMonitorBootstrap:
    """监控自举约束：监控本体不得依赖被监控对象。"""

    def test_single_job_runs_on_hosted_runner(self, workflow: dict[str, Any]) -> None:
        """唯一 job 必须跑在 GitHub 托管 runner（ubuntu-latest）。"""
        jobs = _get_jobs(workflow)
        assert len(jobs) == 1, f"应为单 job，实际 {len(jobs)}"
        for name, job in jobs.items():
            runs_on = job.get("runs-on")
            assert runs_on == "ubuntu-latest", (
                f"job '{name}' runs-on={runs_on!r} — 巡查必须跑托管 runner，"
                f"禁止 self-hosted（否则被监控对象掉线时监控同死）"
            )

    def test_timeout_is_bounded(self, workflow: dict[str, Any]) -> None:
        """巡查必须有 timeout 上界（防止巡查自身挂死成为新的僵尸 run）。"""
        for name, job in _get_jobs(workflow).items():
            timeout = job.get("timeout-minutes")
            assert isinstance(timeout, int) and 0 < timeout <= 15, (
                f"job '{name}' timeout-minutes={timeout!r} — 应设 ≤15 分钟上界"
            )

    def test_top_level_permissions_empty(self, workflow: dict[str, Any]) -> None:
        """零权限基线（workflow permissions 契约；轮询走 PAT 非 GITHUB_TOKEN）。"""
        assert workflow.get("permissions") == {}, (
            "顶层 permissions 应为 {}（零权限基线；跨仓轮询用 PAT，不给默认 token 任何权限）"
        )


class TestRunnerOfflineJudgment:
    """判定 A：runner 掉线告警。"""

    def test_polls_both_repos_runners_api(self, scripts: str) -> None:
        """两仓 runners API 都必须被轮询（仓库循环 + /actions/runners 路径）。"""
        for repo in WATCHED_REPOS:
            assert repo in scripts, f"脚本未轮询 {repo}"
        assert "/actions/runners" in scripts, "脚本缺 runners API 轮询路径"

    def test_offline_event_with_required_properties(self, scripts: str) -> None:
        """runner-offline 事件属性契约：仓标识 / offline 名单 / online 数。"""
        assert "runner-offline" in scripts, "未投递 runner-offline 事件"
        for prop in ("online_count", "offline_runners", "repo"):
            assert prop in scripts, f"runner-offline 事件属性缺 {prop}"

    def test_threshold_gating_present(self, scripts: str) -> None:
        """在线数与阈值比较逻辑必须在场（MIN_ONLINE 环境变量）。"""
        assert "MIN_ONLINE" in scripts, "缺 MIN_ONLINE 阈值门控"


class TestStuckRunJudgment:
    """判定 B：僵尸 run 巡查。"""

    def test_scans_both_repos_queued_and_in_progress(self, scripts: str) -> None:
        """两仓 queued / in_progress 两种状态都必须扫描。"""
        for repo in WATCHED_REPOS:
            assert repo in scripts, f"脚本未扫描 {repo}"
        for status in ("queued", "in_progress"):
            assert status in scripts, f"扫描状态缺 {status}"

    def test_stuck_event_with_required_properties(self, scripts: str) -> None:
        """stuck-run 事件属性契约：run id / 状态 / 年龄 / workflow 名。"""
        assert "stuck-run" in scripts, "未投递 stuck-run 事件"
        for prop in ("run_id", "status", "age_minutes", "workflow_name", "repo"):
            assert prop in scripts, f"stuck-run 事件属性缺 {prop}"

    def test_age_threshold_gating_present(self, scripts: str) -> None:
        """年龄与阈值比较逻辑必须在场（STUCK_AGE_MINUTES 环境变量）。"""
        assert "STUCK_AGE_MINUTES" in scripts, "缺 STUCK_AGE_MINUTES 阈值门控"


class TestAlertChainReuse:
    """投递面复用 error-gateway 链路语义，不自建告警面。"""

    def test_posthog_api_key_from_secret(self, workflow: dict[str, Any]) -> None:
        """POSTHOG_API_KEY 只来自 repo secret，值零明文。"""
        env = _all_step_env_blocks(workflow)
        assert env.get("POSTHOG_API_KEY") == "${{ secrets.POSTHOG_API_KEY }}", (
            f"POSTHOG_API_KEY 必须引用 secrets.POSTHOG_API_KEY，实际 {env.get('POSTHOG_API_KEY')!r}"
        )

    def test_posthog_batch_endpoint_matches_repo_precedent(self, scripts: str) -> None:
        """投递端点与仓内既有先例（ci.yml / release-announce.yml）一致。"""
        assert "https://us.posthog.com/batch/" in scripts, (
            "投递端点应为 us.posthog.com/batch/（与 ci.yml/release-announce.yml 先例一致）"
        )

    def test_delivery_failure_is_loud(self, scripts: str) -> None:
        """投递失败必须显式可见：HTTP_CODE 回显 + 非 2xx 报错 + job 失败。"""
        assert "HTTP_CODE" in scripts, "缺 HTTP_CODE 回显（审计面）"
        assert "::error::" in scripts, "缺 ::error:: 失败注解"
        assert "exit 1" in scripts, "投递失败后必须 exit 1（fail loud，不静默吞掉告警链断裂）"


class TestCrossRepoToken:
    """跨仓轮询凭证契约。"""

    def test_pat_token_wired(self, workflow: dict[str, Any]) -> None:
        """跨仓轮询走 secrets.INFRA_WATCH_GH_PAT（默认 GITHUB_TOKEN 出不了仓）。"""
        env = _all_step_env_blocks(workflow)
        assert env.get("GH_TOKEN") == "${{ secrets.INFRA_WATCH_GH_PAT }}", (
            f"GH_TOKEN 必须引用 secrets.INFRA_WATCH_GH_PAT，实际 {env.get('GH_TOKEN')!r}"
        )

    def test_missing_token_fails_loud(self, scripts: str) -> None:
        """PAT 缺配时显式报错退出（fail loud，不静默产空巡查）。"""
        assert "::error::" in scripts and "GH_TOKEN" in scripts, (
            "缺 PAT 缺配的显式报错（fail loud）"
        )

    def test_no_hardcoded_key_values(self, workflow: dict[str, Any]) -> None:
        """工作流全文零明文密钥（phc_ 前缀为 PostHog key 特征）。"""
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "phc_" not in text, "工作流中出现明文 PostHog key 特征（phc_）"
