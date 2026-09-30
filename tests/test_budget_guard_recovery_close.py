"""budget-guard 恢复即关契约测试（Issue #125 方案 A）。

背景：actions-budget-guard.yml 的开单逻辑此前只有"违规→开单/追加评论"
分支；某周恢复合规后，上一违规窗口遗留的 open 告警单永远不会被自动关闭。

契约（本套测试钉死）：
- else 分支（零违规）必须搜索本仓 open 状态、label 为 automation、标题匹配
  "Actions budget alert:" 前缀的 issue（与开单分支同构的标题指纹幂等查重）
- 命中则对每张执行 gh issue close --comment（评论携带本周 SUMMARY 报告并
  说明这是恢复窗口自动关闭）
- 找不到则维持幂等 no-op 日志

模式先例：tests/test_engine_authorization_gate_contract.py 的 B 面契约测试
（解析 yml 提取 run 脚本后断言字符串锚点，如 'in:title \\"${ALERT_TITLE}\\"'）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

pytestmark = __import__("pytest").mark.security

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "actions-budget-guard.yml"


def _run_script() -> str:
    """提取预算守卫 job 主 step 的 run 脚本（契约测试被测对象）。"""
    doc: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["budget-report"]["steps"]
    run_steps = [s for s in steps if s.get("run")]
    assert run_steps, "budget-report job 必须有 run 脚本 step"
    return run_steps[-1]["run"]


class TestBudgetGuardRecoveryClose:
    """恢复即关（Issue #125 方案 A）：零违规窗口自动关闭遗留告警单。"""

    def test_else_branch_searches_stale_alerts_by_title_fingerprint(self) -> None:
        """else 分支必须按标题前缀指纹搜索 open 告警单（与开单分支同构）。"""
        script = _run_script()
        assert 'in:title \\"Actions budget alert:\\"' in script, (
            'else 分支必须以 in:title "Actions budget alert:" 搜索串查重'
            "（与现有开单分支的标题指纹幂等模式同构）"
        )

    def test_else_branch_closes_with_recovery_comment(self) -> None:
        """命中遗留告警单时执行 gh issue close --comment，评论含恢复说明。"""
        script = _run_script()
        assert "gh issue close" in script, "else 分支必须关闭遗留告警单"
        assert 'gh issue close "${ALERT}"' in script, (
            "必须对搜索命中的每张告警单执行关闭（STALE_ALERTS 循环变量）"
        )
        assert "--comment" in script, "关闭必须带恢复报告评论"
        assert "Recovery window" in script, "评论必须说明这是恢复窗口自动关闭"
        assert "${SUMMARY}" in script, "恢复评论必须携带本周 SUMMARY（No violations 结论 + 表格）"

    def test_else_branch_search_scopes_open_state_and_label(self) -> None:
        """搜索必须限定 open 状态 + automation label（不误关历史/其他单）。"""
        script = _run_script()
        assert "--state open" in script, "搜索必须限定 open 状态"
        assert '--label "${LABEL}"' in script, "搜索必须带 automation label"

    def test_else_branch_noop_when_no_stale_alert(self) -> None:
        """未命中时保持幂等 no-op 日志（不因搜索失败破坏工作流）。"""
        script = _run_script()
        assert "STALE_ALERTS=$(gh issue list" in script, (
            "搜索结果必须先落入变量再判空（容忍 gh 失败的 || echo 兜底）"
        )
        assert '|| echo ""' in script, "gh issue list 失败必须兜底为空串（fail-open）"
        assert "idempotent no-op" in script, "未命中路径保留幂等 no-op 日志锚点"

    def test_violation_branch_untouched(self) -> None:
        """开单分支（违规路径）的幂等结构不受本次改动影响。"""
        script = _run_script()
        assert 'if [ -n "$VIOLATIONS" ]; then' in script, "违规分支结构保持"
        assert "gh issue create" in script and "gh issue comment" in script, (
            "开单/追加评论幂等结构保持"
        )

    def test_close_loop_covers_all_matches(self) -> None:
        """循环关闭全部命中（正常一张，历史残留多张也全关）。"""
        script = _run_script()
        assert "for ALERT in $STALE_ALERTS; do" in script, "必须循环处理全部命中"
        assert "done" in script, "循环必须闭合"
