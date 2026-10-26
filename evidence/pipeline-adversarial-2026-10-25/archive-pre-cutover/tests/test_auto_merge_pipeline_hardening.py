"""auto-merge-pipeline.yml 硬化不变量测试（M4 gate-watchdog-automerge）

memory-core 的 test_auto_merge_triage.py 曾在 auto-merge.yml（内联形态）上
锁定 INFRA-416/428 两轮硬化不变量。M4 切换后执行体迁入本仓 auto-merge-pipeline.yml
（reusable），硬化 pin 随之迁移到本文件——在 reusable 载体上逐条锁定，
防止切换过程中弱化或丢失：

- VAL-T416-001/002 + VAL-428-003：CONFLICTING 通知 sentinel 幂等 + 去重命中 exit 0
- VAL-T416-003：triage NotFound → skip（竞态守卫）
- VAL-T416-004：mergeable=UNKNOWN 重取一次
- VAL-T416-006：job timeout-minutes: 10
- VAL-428-002g：triage 取数含 isDraft + statusCheckRollup（early-fire 防护）
- VAL-428-004：update-branch 失败降级绿腿（竞态守卫）
- VAL-428-005：stalled 类别 + head SHA sentinel + stalled 标志输出/消费
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_PATH = REPO_ROOT / ".github/workflows/auto-merge-pipeline.yml"


def _load() -> dict[str, Any]:
    doc = yaml.safe_load(PIPELINE_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "auto-merge-pipeline.yml 必须是 YAML mapping"
    return doc


def _step(data: dict[str, Any], name: str) -> dict[str, Any]:
    steps = data["jobs"]["auto-merge"]["steps"]
    return next(s for s in steps if s.get("name") == name)


class TestNotifyStepIdempotency:
    """VAL-T416-001/002 + VAL-428-003：CONFLICTING 通知幂等。"""

    def test_notify_step_has_sentinel_dedup(self) -> None:
        """notify 步骤按 head SHA sentinel 去重评论，且查询既有评论/head SHA。"""
        run_block = _step(_load(), "Handle CONFLICTING PR (notify)")["run"]
        assert "auto-merge-conflict-" in run_block, (
            "notify 步骤必须包含 head SHA sentinel（幂等去重）"
        )
        assert "--json comments" in run_block or "--json headRefOid" in run_block, (
            "notify 步骤必须查询既有评论/head SHA"
        )

    def test_notify_skip_branch_does_not_comment(self) -> None:
        """已有同 SHA sentinel 时跳过评论：去重分支在 gh pr comment 之前短路。"""
        run_block = _step(_load(), "Handle CONFLICTING PR (notify)")["run"]
        assert "Skipping duplicate notification" in run_block, (
            "去重命中分支必须显式跳过且不调 gh pr comment"
        )
        assert run_block.index("Skipping duplicate notification") < run_block.index("gh pr comment")

    def test_notify_dedup_hit_exits_zero(self) -> None:
        """去重命中分支必须 exit 0（通知已送达=职责完成），否则未解决冲突每轮永久红腿。"""
        run_block = _step(_load(), "Handle CONFLICTING PR (notify)")["run"]
        segment = run_block.split("Skipping duplicate notification", 1)[1]
        segment_before_comment = segment.split("gh pr comment", 1)[0]
        assert "exit 0" in segment_before_comment
        assert "exit 1" not in segment_before_comment


class TestTriageStepHardening:
    """VAL-T416-003/004 + VAL-428-002g/005c：triage 步骤硬化。"""

    def test_triage_step_handles_pr_not_found(self) -> None:
        """PR 被并行腿合并后 gh pr view NotFound → skip 而非红腿。"""
        run_block = _step(_load(), "Triage PR mergeable state")["run"]
        assert "not found" in run_block or "Could not resolve" in run_block, (
            "triage 步骤必须识别 NotFound 并降级 skip（竞态守卫）"
        )
        assert "action=skip" in run_block

    def test_triage_step_retries_unknown_state(self) -> None:
        """mergeable=UNKNOWN 时等待重取一次，仍未知交 schedule 兜底。"""
        run_block = _step(_load(), "Triage PR mergeable state")["run"]
        assert '"$MERGEABLE" = "UNKNOWN"' in run_block, "triage 步骤必须对 UNKNOWN 状态做一次重取"
        assert "UNKNOWN retry" in run_block

    def test_workflow_triage_fetches_rollup_and_draft(self) -> None:
        """取数必须包含 isDraft + statusCheckRollup（early-fire/draft 盲合并防护）。"""
        triage_run = _step(_load(), "Triage PR mergeable state")["run"]
        fetch_line = next(
            (ln for ln in triage_run.splitlines() if "--json" in ln and "gh pr view" in ln),
            None,
        )
        assert fetch_line is not None, "triage 步骤未找到 gh pr view --json 取数行"
        assert "statusCheckRollup" in fetch_line, (
            "取数缺少 statusCheckRollup（early-fire 防护失效）"
        )
        assert "isDraft" in fetch_line, "取数缺少 isDraft（draft 盲合并防护失效）"

    def test_triage_outputs_stalled_flag(self) -> None:
        """triage 步骤输出 stalled 标志供 stalled 步骤门控。"""
        triage_run = _step(_load(), "Triage PR mergeable state")["run"]
        assert "stalled=" in triage_run, "triage 步骤未输出 stalled 标志"


class TestRestThreeGatesEnrichment:
    """v3.2.2 §3.2（VAL-M1-003）：pipeline 载体 triage 取数富集 REST check-runs(head_sha)。"""

    def test_triage_step_enriches_rest_check_runs(self) -> None:
        """triage 步骤在分类前经 REST check-runs(head_sha) 富集 checkRuns 字段。"""
        triage_run = _step(_load(), "Triage PR mergeable state")["run"]
        assert "check-runs" in triage_run, "triage 步骤必须经 REST check-runs(head_sha) 富集"
        assert "headRefOid" in triage_run, "REST 取数必须绑定当前 head SHA"
        assert "checkRuns" in triage_run, "取数结果必须注入 checkRuns 字段供 triage 消费"

    def test_triage_fetch_includes_head_sha(self) -> None:
        """取数 --json 行含 headRefOid（head SHA 绑定，webhook/dispatch/merge 同款铁律）。"""
        triage_run = _step(_load(), "Triage PR mergeable state")["run"]
        fetch_line = next(
            (ln for ln in triage_run.splitlines() if "--json" in ln and "gh pr view" in ln),
            None,
        )
        assert fetch_line is not None
        assert "headRefOid" in fetch_line, "triage 取数缺 headRefOid（REST 门判 SHA 绑定失效）"


class TestRestReadLegSingleCapture:
    """Round-2 P1 #8（#381 终局分级）：check-runs 读腿失败时单次捕获归一。

    失效模式（62ac55c 基线 pipeline:360）：`gh|jq -s ... || echo "[]"` 形态在
    set -euo pipefail 下 gh 失败（空 stdout）时 jq -s 在空输入上仍输出 '[]'
    且 rc=0，但 pipefail 使管道整体非零 → `|| echo "[]"` 也执行 → 命令替换
    捕获两行 '[]\n[]'：(a) 等值比较 `[ "$X" = "[]" ]` 永不命中 → ::warning::
    可观测性丢失；(b) 内嵌 --argjson 的两行 JSON 非法 → jq 红腿退出，与注释
    承诺的「失败可观测但不红腿，交下一轮 sweep」相反（fail-loud 毒化 head）。

    修复契约（与 #6/#7 实例选择语义不同构——本条是错误处理路径修复）：
    1. 读腿必须先落原始响应（失败时显式置单 '[]'），管道与兜底不得共存于
       同一次命令替换；
    2. 后续等值/聚合消费必须拿归一后的单一值。
    """

    def _triage_run(self) -> str:
        return _step(_load(), "Triage PR mergeable state")["run"]

    def test_no_inline_or_fallback_on_enrichment_capture(self) -> None:
        """富集命令替换禁止 `| jq ... || echo` 管道+兜底共存形态（#8 根因形态）。"""
        triage_run = self._triage_run()
        capture_lines = [
            ln for ln in triage_run.splitlines() if "gh api" in ln and "check-runs" in ln
        ]
        assert capture_lines, "triage 必须保留 REST 富集读腿（本契约的锚点）"
        for ln in capture_lines:
            assert "||" not in ln, (
                "check-runs 读腿命令替换内禁止管道+`|| echo` 兜底共存"
                "（pipefail 下 jq 空 stdin 输出 '[]' 且兜底同触发 → 双 '[]\\n[]'，"
                f"#8 fail-loud 根因形态），实际行：{ln.strip()}"
            )

    def test_read_leg_writes_raw_response_file(self) -> None:
        """读腿必须先落原始响应再本地归一（单次捕获，失败显式置单 '[]'）。"""
        triage_run = self._triage_run()
        assert "RUNNER_TEMP" in triage_run and "check_runs_raw" in triage_run, (
            "check-runs 读腿必须先落原始响应文件（RUNNER_TEMP/check_runs_raw.*，"
            "失败时显式置单 '[]'——单次捕获归一，#8 修复形态）"
        )
        assert 'echo "[]"' in triage_run, (
            "读腿失败分支必须显式输出单 '[]'（fail-closed 且 warning 可达）"
        )

    def test_warning_and_argjson_consume_normalized_value(self) -> None:
        "::warning:: 等值判断与 --argjson 注入必须消费归一后的单值（双 '[]' 形态即红）。"
        triage_run = self._triage_run()
        # 富集空结果的等值判断行（等值消费归一后的单值；::warning:: 在 if 块内）
        eq_lines = [
            ln
            for ln in triage_run.splitlines()
            if ln.strip().startswith('if [ "$CHECK_RUNS_JSON" = "[]" ]')
        ]
        assert eq_lines, (
            '富集空结果必须以等值比较消费归一后的单值 [ "$CHECK_RUNS_JSON" = "[]" ] '
            '（#8：双行 "[]" 使等值永不命中 → ::warning:: 可观测性丢失）'
        )
        # 注入行必须消费归一后的单值变量
        inject_line = next(
            (ln for ln in triage_run.splitlines() if "--argjson cr" in ln),
            None,
        )
        assert inject_line is not None, "必须保留 --argjson cr 注入行"
        assert "$CHECK_RUNS_JSON" in inject_line, (
            f"注入行必须引用归一后的单值变量，实际：{inject_line.strip()}"
        )
        # 原始管道聚合形态（`| jq -s ... || echo "[]"`）整体禁止（含换行续行拼接）
        assert "| jq -s" not in triage_run or '|| echo "[]" |' not in triage_run, (
            "管道聚合与兜底输出拼接形态残留（#8 双 '[]' 根因）"
        )


class TestStalledStep:
    """VAL-428-005：stalled 处理步骤存在且幂等。"""

    def test_workflow_has_stalled_step_with_sentinel(self) -> None:
        data = _load()
        steps = data["jobs"]["auto-merge"]["steps"]
        stalled_step = next(
            (s for s in steps if "stalled" in str(s.get("name", "")).lower()),
            None,
        )
        assert stalled_step is not None, "缺少 stalled 处理步骤"
        run_block = stalled_step["run"]
        assert "auto-merge-stalled-" in run_block, "stalled 步骤必须按 head SHA sentinel 幂等"
        assert "gh pr comment" in run_block

    def test_stalled_step_consumes_flag(self) -> None:
        """stalled 步骤的 if 条件必须消费 triage 的 stalled 标志。"""
        data = _load()
        steps = data["jobs"]["auto-merge"]["steps"]
        stalled_step = next(s for s in steps if "stalled" in str(s.get("name", "")).lower())
        assert "steps.triage.outputs.stalled" in str(stalled_step.get("if", ""))


class TestUpdateBranchRaceGuard:
    """VAL-428-004：update-branch 失败降级绿腿（并行腿竞态正常收敛）。"""

    def test_update_branch_race_guard(self) -> None:
        run_block = _step(_load(), "Handle BEHIND PR (update-branch self-heal)")["run"]
        assert "if ! gh pr update-branch" in run_block or (
            "gh pr update-branch" in run_block and "exit 0" in run_block
        ), "update-branch 必须有失败降级分支（竞态守卫），不允许裸 exit 1"


class TestJobBounds:
    """VAL-T416-006：单腿时长限定，防 gh/网络挂死堆叠。"""

    def test_auto_merge_job_has_timeout(self) -> None:
        assert _load()["jobs"]["auto-merge"].get("timeout-minutes") == 10

    def test_matrix_bounds(self) -> None:
        strategy = _load()["jobs"]["auto-merge"]["strategy"]
        # v3.2.2 §3.4 FIFO 双保险之单飞腿（VAL-M1-005）：max-parallel:1
        # 串行合并/更新分支；截断腿在 resolve 输出前（test_fifo_trust_gates 锁定）。
        assert strategy["max-parallel"] == 1
        assert strategy["fail-fast"] is False


class TestCheckRunsArgLimitHardening:
    """sh1 P1（PR-C #411 round-1）：check-runs 载荷禁止经 argv 全量内嵌。

    失效模式：归一后的 CHECK_RUNS_JSON 以单个 --argjson 注入 jq。check-run
    全量对象携带 output.summary/output.text（各至多 64KB），busy head SHA 上
    单参数可超 Linux MAX_ARG_STRLEN（128KiB）→ jq E2BIG（"Argument list too
    long"）→ set -e 下 PR_DATA 赋值红腿 triage 步——毒化 head SHA 零红扫描，
    正是 round-2 #8「读腿失败可观测但不红腿」承诺禁止的红腿向量（'[]' 归一
    不覆盖该 E2BIG 路径）。

    修复契约：归一化时即瘦身（只留 triage 消费面字段 id/name/status/
    conclusion/started_at，剥 output/description 等大字段），--argjson 通道
    保留但载荷恒小；瘦身步必须有与归一同步的空值兜底（jq 红腿 → 空集 →
    fail-closed 非绿，不红腿）。
    """

    def _triage_run(self) -> str:
        return _step(_load(), "Triage PR mergeable state")["run"]

    def test_normalized_check_runs_are_slimmed_before_injection(self) -> None:
        """归一化必须内联瘦身到 triage 消费面（id/name/status/conclusion/
        started_at），全量 check-run 对象禁止进入注入变量。"""
        triage_run = self._triage_run()
        slim_line = next(
            (ln for ln in triage_run.splitlines() if "CHECK_RUNS_JSON=" in ln and "map(" in ln),
            None,
        )
        assert slim_line is not None, "CHECK_RUNS_JSON 归一化行缺失"
        # gate_latest_completed 只读这五个字段；全量对象携带 output.summary/
        # output.text（各至多 64KB），busy head SHA 上单 --argjson 参数可超
        # Linux MAX_ARG_STRLEN（128KiB）→ jq E2BIG → set -e 下 PR_DATA 赋值
        # 红腿 triage——'[]' 归一化不覆盖该向量。
        assert "{id, name, status, conclusion, started_at}" in slim_line, (
            "归一化必须内联瘦身 map(select(.id != null) | {id, name, status, "
            "conclusion, started_at})——全量对象进 argv 即 E2BIG 红腿面"
        )
        for field in ("output", "details_url", "description"):
            assert field not in slim_line, f"瘦身表达式残留大字段 {field}（E2BIG 回归面）"

    def test_injection_argjson_carries_slim_variable_only(self) -> None:
        """--argjson 载荷必须是瘦身后的单一变量，禁止在注入行二次内嵌原始列。"""
        triage_run = self._triage_run()
        inject_line = next(ln for ln in triage_run.splitlines() if "--argjson cr" in ln)
        assert inject_line.count('"$CHECK_RUNS_JSON"') == 1, (
            "注入行必须且只能引用一次瘦身后的 $CHECK_RUNS_JSON（二次内嵌=E2BIG 回归）"
        )

    def test_slimming_failure_fails_closed_not_red(self) -> None:
        """瘦身 jq 失败（非零）必须兜底空集 + 可观测 warning，交下一轮 sweep；
        禁止 set -e 在赋值处红腿（与 #8「不红腿 fail-closed」承诺同轴）。"""
        triage_run = self._triage_run()
        slim_line = next(
            ln for ln in triage_run.splitlines() if "CHECK_RUNS_JSON=" in ln and "map(" in ln
        )
        assert '|| echo "[]"' in slim_line, "瘦身归一必须有 jq 失败兜底空集（fail-closed 不红腿）"
        assert slim_line.count("|| echo") == 1, (
            "瘦身归一必须单次捕获（管道 + || 兜底共存 = #8 双 '[]\\n[]' 根因形态）"
        )
        assert "slimmed" in triage_run, (
            "瘦身失败/空集路径必须打 ::warning:: 可观测（triage 将 fail-closed 非绿）"
        )
