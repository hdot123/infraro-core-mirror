"""H0 auto-merge 403 修复契约：读/写 token 分离 + REST fail-loud。

背景（2026-09-29 H0 诊断 + R7 对抗验证终稿，
audits/round4/h0-final-diagnosis.md）：
DISPATCH_TOKEN 绑定的 fine-grained PAT 无 Checks:read（PAT 设置 UI 不可配，
GitHub 文档-UI 已知缺口），check-runs REST 读腿被拒（HTTP 403）。旧实现
`gh api ... > file 2>/dev/null || true` 吞掉非零退出与 stderr，403 错误体经
stdout 落盘后被 jq 聚合 `.check_runs // .` 回退当成一条 check-run
（status="403" ≠ completed）→ INCOMPLETE=1 → ::warning:: → exit 0 绿腿，
合并静默永不发生（#168 全绿 MERGEABLE 挂数小时实锤）。

本文件锁定五条不变量：
1. 读/写分离——composite action 新增 GH_READ_TOKEN=${{ github.token }}，
   check-runs 读步显式 GH_TOKEN="$GH_READ_TOKEN"（默认 token 受 job 级
   permissions checks:read 约束）；写腿（pr view/comment/merge）保持
   GH_TOKEN=env.GITHUB_TOKEN（DISPATCH_TOKEN PAT，防递归抑制铁律不变）。
2. fail-loud REST——读步 stderr 落盘（禁 2>/dev/null）、非零退出显式
   ::error:: + exit 1（禁 || true 吞错、禁把错误体喂给 jq 聚合）。
3. 聚合无回退——`.check_runs // .` 的 `// .` 回退删除（错误体不得成为
   聚合元素）；无 id 字段的元素一律丢弃。
4. 行为级红绿——真实 403 错误体注入聚合管道：修复前必红（错误体被计入），
   修复后绿（错误体零计入、真实 check 正常聚合）。
5. 写腿语义不变——合并凭证仍经 env GITHUB_TOKEN（caller 注入
   DISPATCH_TOKEN PAT），merge 命令与竞态守卫语义保持。
6. EXIT trap 退出路径完整性——trap 引用的每个变量必须在 trap 装载前赋值
   （set -u 下晚赋值变量会在早期退出路径的 trap 展开中 unbound，把
   exit 0 改写成 exit 1，scrutiny r1 blocking 实证）；行为级断言：
   三门拒合路径 exit 0、门读取失败路径诊断输出零 trap 噪音。
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_YML = REPO_ROOT / "actions/auto-merge/action.yml"


def _load_action() -> dict[str, Any]:
    assert ACTION_YML.exists(), "actions/auto-merge/action.yml 必须存在"
    doc = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "action.yml 必须是 YAML mapping"
    return doc


def _action_script() -> str:
    steps = _load_action()["runs"]["steps"]
    return "\n".join(str(step.get("run", "")) for step in steps)


def _first_step_env() -> dict[str, str]:
    first = _load_action()["runs"]["steps"][0]
    env = first.get("env") or {}
    assert isinstance(env, dict), "首步 env 必须是 mapping"
    return {str(k): str(v) for k, v in env.items()}


def _extract_jq_program(marker: str, script: str) -> str:
    """提取 `MARKER=$(jq -s '<program>' "$CHECK_RUNS_FILE"` 中的 jq 程序。"""
    match = re.search(re.escape(marker) + r"=\$\(jq\s+-s\s+'((?:[^'\\]|\\.)*)'", script)
    assert match, f"脚本中必须存在 {marker}=$(jq -s '...') 聚合赋值"
    return match.group(1)


def _run_jq_aggregation(jq_program: str, pages: list[dict[str, Any]]) -> str:
    """以 --paginate 多页形态（换行分隔 JSON 文档）运行聚合 jq，返回 stdout。"""
    stdin_text = "\n".join(_compact(page) for page in pages)
    result = subprocess.run(
        ["jq", "-s", jq_program],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _compact(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def _failed_scan_program() -> str:
    """提取旁路零红扫描（FAILED 判定）的 jq 程序。

    输入形态与 GATE_FAIL 的 `jq -s '<program>'` 聚合不同：FAILED 判定消费
    FILTERED_CHECKS（已 self-exclude 的单数组），生产形如
    `FAILED=$(echo "$FILTERED_CHECKS" | jq '<program>')`。
    """
    match = re.search(
        r"FAILED=\$\(echo \"\$FILTERED_CHECKS\" \| jq\s+'((?:[^'\\]|\\.)*)'",
        _action_script(),
    )
    assert match, "脚本必须存在 FAILED=$(echo \"$FILTERED_CHECKS\" | jq '<program>') 红扫描判定"
    return match.group(1)


def _run_failed_scan(jq_program: str, checks: list[dict[str, Any]]) -> str:
    """以 FILTERED_CHECKS 数组形态（生产输入）运行旁路红扫描 jq，返回 stdout。"""
    import json

    result = subprocess.run(
        ["jq", jq_program],
        input=json.dumps(checks),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _check_run(
    cid: int,
    name: str = "ci",
    status: str = "completed",
    conclusion: str = "success",
) -> dict[str, Any]:
    return {
        "id": cid,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/hdot123/infraro-core/actions/runs/99{cid}",
        "app": {"slug": "github-actions"},
    }


def _http_error_body(status: str = "403") -> dict[str, Any]:
    """gh api 非零退出时错误体经 stdout 落盘的实际形态（h0 诊断 verbatim）。"""
    return {
        "message": "Resource not accessible by personal access token",
        "status": status,
        "documentation_url": (
            "https://docs.github.com/rest/checks/runs#list-check-runs-for-a-git-reference"
        ),
    }


class TestReadWriteTokenSeparation:
    """读/写分离：check-runs 读走默认 token，写腿保持 DISPATCH_TOKEN PAT。"""

    def test_read_token_env_declared(self) -> None:
        """首步 env 必须声明 GH_READ_TOKEN=${{ github.token }}（默认 token 读腿）。"""
        env = _first_step_env()
        assert env.get("GH_READ_TOKEN") == "${{ github.token }}", (
            f"GH_READ_TOKEN 必须取 github.token（默认 workflow token），实际: "
            f"{env.get('GH_READ_TOKEN')}"
        )

    def test_write_leg_keeps_pat_token(self) -> None:
        """写腿凭证语义不变：GH_TOKEN 仍取 env.GITHUB_TOKEN（caller 注入 PAT）。"""
        env = _first_step_env()
        assert env.get("GH_TOKEN") == "${{ env.GITHUB_TOKEN }}", (
            "GH_TOKEN（写腿 pr view/comment/merge 凭证）必须保持 "
            f"${{{{ env.GITHUB_TOKEN }}}}，实际: {env.get('GH_TOKEN')}"
        )

    def test_read_call_uses_read_token_explicitly(self) -> None:
        """check-runs 读步必须显式 GH_TOKEN="$GH_READ_TOKEN" 覆盖（PAT 不得触碰读腿）。"""
        script = _action_script()
        pattern = re.compile(
            r'GH_TOKEN="\$GH_READ_TOKEN"\s+gh\s+api\s+"?(?:repos/)?\$\{?REPO\}?/commits/'
        )
        assert pattern.search(script), (
            'check-runs 读步必须以 GH_TOKEN="$GH_READ_TOKEN" 前缀调用 gh api '
            "（env 级 GH_TOKEN=PAT 只服务写腿）"
        )

    def test_caller_write_injection_unchanged(self) -> None:
        """写腿凭证注入面不变：pipeline 载体 merge 步 env.GITHUB_TOKEN 绑
        dispatch_token；自仓壳（B-2 收敛后 uses-only，零 steps）只验 secrets
        透传对接（壳自身不再承载 merge 步）。"""
        for carrier in (
            REPO_ROOT / ".github/workflows/auto-merge.yml",
            REPO_ROOT / ".github/workflows/auto-merge-pipeline.yml",
        ):
            doc = yaml.safe_load(carrier.read_text(encoding="utf-8"))
            caller_job = doc["jobs"]["auto-merge"]
            if carrier.name == "auto-merge.yml":
                # B-2 收敛：壳是 uses-only 触发面，凭证经 workflow_call secrets
                # 显式传递（snake 单形态键），写腿注入在 pipeline 载体验证。
                assert caller_job.get("steps") is None
                assert caller_job["secrets"] == {
                    "dispatch_token": "${{ secrets.DISPATCH_TOKEN }}"
                }, f"{carrier.name} 壳必须以 secrets.dispatch_token 透传 DISPATCH_TOKEN"
                continue
            steps = caller_job["steps"]
            merge_step = next(
                s
                for s in steps
                if re.search(r"/actions/auto-merge@[0-9a-f]{40}", str(s.get("uses", "")))
            )
            env = merge_step.get("env", {})
            assert env.get("GITHUB_TOKEN") == "${{ secrets.dispatch_token }}", (
                f"{carrier.name} merge 步 GITHUB_TOKEN 注入面不得变化，实际: "
                f"{env.get('GITHUB_TOKEN')}"
            )

    def test_job_permissions_checks_read_retained(self) -> None:
        """job 级 permissions 的 checks:read 必须保留（读腿默认 token 的权限依据）。"""
        for carrier in (
            REPO_ROOT / ".github/workflows/auto-merge.yml",
            REPO_ROOT / ".github/workflows/auto-merge-pipeline.yml",
        ):
            doc = yaml.safe_load(carrier.read_text(encoding="utf-8"))
            perms = doc["jobs"]["auto-merge"]["permissions"]
            assert perms.get("checks") == "read", (
                f"{carrier.name} auto-merge job permissions.checks 必须为 read（读腿依据）"
            )


class TestFailLoudRestRead:
    """REST 读步 fail-loud：stderr 落盘、非零退出显式处理、禁静默吞错。"""

    def _read_call_line_index(self, lines: list[str]) -> int:
        for i, line in enumerate(lines):
            if "gh api" in line and "check-runs" in line:
                return i
        pytest.fail("脚本中必须存在 check-runs 的 gh api 读调用")

    def test_stderr_captured_to_file(self) -> None:
        """读步 stderr 必须落盘（禁 2>/dev/null 丢弃——403 message 曾被吞）。"""
        script = _action_script()
        lines = script.splitlines()
        idx = self._read_call_line_index(lines)
        call_line = lines[idx]
        assert "2>/dev/null" not in call_line, (
            "check-runs 读步禁止 2>/dev/null（stderr 必须落盘可取证）"
        )
        assert "|| true" not in call_line, "check-runs 读步禁止 || true（非零退出必须显式处理）"
        assert '2>"$' in call_line, '读步必须存在 stderr 落盘重定向（2>"$..."）'

    def test_nonzero_exit_fails_loud(self) -> None:
        """读步失败必须 ::error:: + exit 1，禁止带着空/错误文件继续聚合。"""
        script = _action_script()
        lines = script.splitlines()
        idx = self._read_call_line_index(lines)
        agg_idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("COLLECTED_COUNT=")),
            None,
        )
        assert agg_idx is not None and agg_idx > idx, "聚合必须发生在读步之后"
        between = "\n".join(lines[idx : agg_idx + 1])
        assert "::error::" in between, "读步失败分支必须打 ::error:: 注解"
        assert re.search(r"\bexit 1\b", between), "读步失败分支必须显式 exit 1（fail-loud）"

    def test_no_silent_skip_on_failed_read(self) -> None:
        """读失败分支禁止 exit 0（旧的静默绿腿正是合并永不发生的机制）。

        v3.2.2 §3.2 后脚本有两段 check-runs 读腿（三门前置门 + 零红扫描），
        各自的「读失败→聚合」区间都不得含 exit 0——绿判拒绝（GATE_FAIL 非空
        的策略性 exit 0）发生在读腿成功之后，不属于读失败静默。
        """
        script = _action_script()
        lines = script.splitlines()
        read_indices = [i for i, ln in enumerate(lines) if "gh api" in ln and "check-runs" in ln]
        assert len(read_indices) >= 2, "必须存在两段 check-runs 读腿（三门门判 + 零红扫描）"
        for idx in read_indices:
            agg_idx = next(
                (
                    i
                    for i, ln in enumerate(lines[idx:], idx)
                    if ln.startswith("COLLECTED_COUNT=") or ln.startswith("GATE_FAIL=")
                ),
                len(lines) - 1,
            )
            between = "\n".join(lines[idx:agg_idx])
            exit_zero = re.findall(r"\bexit 0\b", between)
            assert not exit_zero, (
                f"读腿 L{idx + 1} 失败分支不得 exit 0（静默绿=合并永不发生），实际: {len(exit_zero)} 处"
            )


class TestAggregationNoFallback:
    """jq 聚合契约：错误体不得成为聚合元素，无 id 元素一律丢弃。"""

    @pytest.mark.parametrize("marker", ["COLLECTED_COUNT", "ALL_CHECKS"])
    def test_no_dot_fallback(self, marker: str) -> None:
        """`.check_runs // .` 的 `// .` 回退必须删除（403 体曾借此伪装成 check）。"""
        program = _extract_jq_program(marker, _action_script())
        assert "// ." not in program, (
            f"{marker} 聚合禁止 `// .` 回退——错误体（无 .check_runs 键）会整体"
            f"成为聚合元素。实际程序: {program}"
        )
        assert ".check_runs // []" in program, (
            f"{marker} 聚合必须用 `.check_runs // []`——错误体贡献零元素"
        )

    @pytest.mark.parametrize("marker", ["COLLECTED_COUNT", "ALL_CHECKS"])
    def test_id_less_elements_dropped(self, marker: str) -> None:
        """无 id 字段的元素必须丢弃（错误体形状：name/status 有值而 id 缺失）。"""
        program = _extract_jq_program(marker, _action_script())
        assert re.search(r"select\(\.id\s*!=\s*null\)", program), (
            f"{marker} 聚合必须 select(.id != null) 丢弃无 id 元素。实际程序: {program}"
        )


class TestGateInstanceSelectionFailClosed:
    """Round-2 P1 #7 同类根治（#381 终局分级）：action.yml 三门实例选择 fail-closed。

    action.yml 的 gate_latest_completed 与 triage 双载体同构（round-2 相邻面
    发现：`gh|jq -s||echo "[]"` 毒模式仅存于 pipeline triage 行，action.yml
    为 fail-loud——但实例选择语义与 triage 62ac55c 同缺陷）：

    - #6 同构：先按 status=="completed" 预过滤再取 started_at 最新——同门
      更新的 in_progress/queued 实例被忽略，旧 success 冒充最新实例判绿；
    - #7 同构：jq sort_by(.started_at) 把 null 排最前——completed+cancelled
      实例（常带 null started_at）被当作最旧，旧 success 顶替。

    修复语义与 triage 一致：全部实例（不按 status 预过滤）中取 started_at
    最新者（null 视为最新），仅当该实例 completed 且 conclusion=success
    才算绿。
    """

    def _gate_program(self) -> str:
        return _extract_jq_program("GATE_FAIL", _action_script())

    def test_gate_selection_does_not_prefilter_completed(self) -> None:
        """三门实例选择禁止按 status==completed 预过滤（#6 同构 fail-open）。"""
        program = self._gate_program()
        body = re.search(
            r"def gate_latest_completed\(gate\):(.*?)\[\s*REQUIRED_GATES", program, re.DOTALL
        )
        assert body, "GATE_FAIL 程序缺 gate_latest_completed 定义"
        assert '.status == "completed"' not in body.group(1), (
            "gate_latest_completed 禁止在实例选择时按 status==completed 预过滤"
            "（#6 同构：滤掉 in_progress/queued 新实例 → 旧 success 冒充最新）"
        )

    def test_gate_selection_null_started_at_not_oldest(self) -> None:
        """排序键必须不含时间戳列（round-3 ①：id 单调性承载 #7 与 #168 双面）。

        历史：round-2 曾以「9999 哨兵或 .started_at != null 过滤」锁定 #7——
        但该结构锁两头都不可靠（OR 分支放行把 null 实例滤掉的 fail-open 实现，
        哨兵本身又是 #168 类回摆根因）。终局形态：排序键 = check-run id
        （单调分配、永非 null），时间戳列彻底退出排序。
        """
        program = self._gate_program()
        body = re.search(
            r"def gate_latest_completed\(gate\):(.*?)\[\s*REQUIRED_GATES", program, re.DOTALL
        )
        assert body, "GATE_FAIL 程序缺 gate_latest_completed 定义"
        assert ".started_at" not in body.group(1), (
            "gate_latest_completed 禁止以 started_at 为排序键（round-3 ① #168 类"
            "回摆：9999 哨兵把旧 null-cancelled 实例当最新 → 永久拒合；裸 sort_by"
            "又把 null 排最前 → #7 fail-open。check-run id 单调分配才是无 null 的"
            "时序代理）"
        )
        assert "sort_by(.id" in body.group(1), (
            "gate_latest_completed 必须按 check-run id 取最新实例（round-3 ①）"
        )

    def test_gate_verdict_requires_completed_latest(self) -> None:
        """绿判收敛点必须验证最新实例 status=completed 且 conclusion=success。"""
        program = self._gate_program()
        if_expr = re.search(r"if\s+\$run\s*==\s*null(.*?)then\s+\$gate", program, re.DOTALL)
        assert if_expr, "GATE_FAIL 门判必须显式处理 $run 为空与非绿分支"
        cond = if_expr.group(1)
        assert '.status != "completed"' in cond, (
            "门判必须验证最新实例 status=completed（仅验 conclusion 会让"
            " in_progress/queued 最新实例被判绿）"
        )
        assert '.conclusion != "success"' in cond, "门判必须要求 conclusion=success"

    def test_newer_in_progress_page_blocks_gate(self) -> None:
        """行为级：三门旧 success + droid-review 更新 in_progress 实例 → 拒合。"""
        program = self._gate_program()
        pages = [
            {
                "total_count": 4,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "substrate-gate-suite",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "name": "quality-gate",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 3,
                        "name": "droid-review",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 4,
                        "name": "droid-review",
                        "status": "in_progress",
                        "conclusion": None,
                        "started_at": "2026-10-19T00:05:00Z",
                    },
                ],
            }
        ]
        out = _run_jq_aggregation(program, pages)
        assert "droid-review" in out, (
            f"更新的 in_progress 实例必须压制旧 success（GATE_FAIL 非空拒合），实际: {out}"
        )

    def test_null_started_at_cancelled_page_blocks_gate(self) -> None:
        """行为级：started_at=null 的 completed+cancelled 新实例 → 拒合。"""
        program = self._gate_program()
        pages = [
            {
                "total_count": 4,
                "check_runs": [
                    {
                        "id": 1,
                        "name": "substrate-gate-suite",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "name": "quality-gate",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 3,
                        "name": "droid-review",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-10-19T00:00:00Z",
                    },
                    {
                        "id": 4,
                        "name": "quality-gate",
                        "status": "completed",
                        "conclusion": "cancelled",
                        "started_at": None,
                    },
                ],
            }
        ]
        out = _run_jq_aggregation(program, pages)
        assert "quality-gate" in out, (
            f"started_at=null 的取消实例必须被视为最新并拒合（#7 同构），实际: {out}"
        )

    def test_all_latest_success_page_passes_gate(self) -> None:
        """keep-green：全实例最新者 completed+success → GATE_FAIL 为空。"""
        program = self._gate_program()
        pages = [
            {
                "total_count": 3,
                "check_runs": [
                    {
                        "id": idx + 1,
                        "name": name,
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": f"2026-10-19T00:0{idx}:00Z",
                    }
                    for idx, name in enumerate(
                        ("substrate-gate-suite", "quality-gate", "droid-review")
                    )
                ],
            }
        ]
        out = _run_jq_aggregation(program, pages)
        assert out == "[]", f"三门全 success 必须照常放行（GATE_FAIL 空），实际: {out}"


class TestBypassRedScanLatestPerName:
    """旁路零红扫描 latest-per-name（round-8 sh11 P1）：与 triage 红腿语义同轴。

    两门禁语义一致性（#375 round-8）：triage 的 has_red_conclusion 已改
    latest-per-name（round-3 ②）——被取代的红残留（同检查名后随 id 更大的
    新实例）不再是红腿，绿 PR 照常 mergeable；但 action 旁路层零红扫描仍是
    any-instance：self-exclude 当前 run id 后任何历史 failure/cancelled/
    timed_out/action_required 实例都计入 FAILED → 同一被取代红残留场景
    triage 判绿、action 判红（"CI has failures ... not merging" 永久 stall，
    #168 类「合并永不发生」）。修复：FAILED 判定与涉实打印统一对齐
    group_by(.name) 内 sort_by(.id) 取最新实例——仅最新实例红才红；
    输入仍为 FILTERED_CHECKS（self-exclude 之后），四类红结论名单不变。
    """

    def test_failed_verdict_uses_latest_instance_per_name(self) -> None:
        """FAILED 判定必须按 group_by(.name) 内 sort_by(.id) 取最新实例。"""
        program = _failed_scan_program()
        assert "group_by(.name)" in program, (
            "FAILED 零红扫描必须 group_by(.name)（latest-per-name，与 triage/"
            "sentinel/check_zero_red 同语义）"
        )
        assert "sort_by(.id" in program, (
            "FAILED 零红扫描实例选择必须按 check-run id 排序（id 单调分配，"
            "started_at null 排序无解，round-3 ① 同源）"
        )

    def test_superseded_red_residue_not_failed(self) -> None:
        """行为级：被取代的红残留（同检查名 id 更大的新绿实例）→ FAILED=0。

        与 test_auto_merge_triage.py::TestRound3NoRevert 同场景：triage 判绿时
        action 旁路层必须同样放行。
        """
        checks = [
            {"id": 10, "name": "lint", "status": "completed", "conclusion": "failure"},
            {"id": 11, "name": "lint", "status": "completed", "conclusion": "success"},
            {"id": 12, "name": "ci-ok", "status": "completed", "conclusion": "success"},
            {"id": 13, "name": "docs", "status": "completed", "conclusion": "success"},
            {"id": 14, "name": "build", "status": "completed", "conclusion": "success"},
        ]
        out = _run_failed_scan(_failed_scan_program(), checks)
        assert out == "0", f"被取代的红残留不得计入 FAILED（latest-per-name），实际: {out}"

    def test_latest_red_instance_still_failed(self) -> None:
        """round-1 P1-1 不回退：最新实例红 → FAILED 仍计数（显红非静默）。"""
        checks = [
            {"id": 20, "name": "lint", "status": "completed", "conclusion": "success"},
            {"id": 21, "name": "lint", "status": "completed", "conclusion": "failure"},
        ]
        out = _run_failed_scan(_failed_scan_program(), checks)
        assert out == "1", f"最新实例红必须计 FAILED=1（红不因重排消失），实际: {out}"

    def test_failure_display_lists_latest_red_instances_only(self) -> None:
        """涉实打印与判定同轴：打印清单也只含最新实例（不再列被取代旧红）。"""
        script = _action_script()
        failed_block = script[script.index('if [ "$FAILED" -gt 0 ]') :]
        match = re.search(
            r"echo \"\$FILTERED_CHECKS\" \| jq -r '((?:[^'\\]|\\.)*)'",
            failed_block,
        )
        assert match, "FAILED 分支内必须存在涉实打印 jq（显红面）"
        print_program = match.group(1)
        assert "group_by(.name)" in print_program, (
            "涉实打印必须与 FAILED 判定同轴（latest-per-name），"
            "否则残留旧红继续出现在诊断清单误导排障"
        )

    def test_self_exclusion_retained_before_latest_selection(self) -> None:
        """自排除保持：FAILED 判定以 FILTERED_CHECKS（已剔除当前 run id）为输入。"""
        script = _action_script()
        failed_line = next(ln for ln in script.splitlines() if ln.startswith("FAILED=$(echo"))
        assert '"$FILTERED_CHECKS"' in failed_line, (
            "FAILED 判定必须以 FILTERED_CHECKS（self-exclude 后）为输入"
        )
        assert "GITHUB_RUN_ID" in script, "自排除必须锚定当前 run id"
        assert "/runs/\\($run_id)" in script, "details_url 自排除守卫（边界防前缀误匹配）必须保留"

    def test_red_conclusion_set_unchanged(self) -> None:
        """四类红结论名单不变：failure/cancelled/timed_out/action_required。"""
        program = _failed_scan_program()
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            assert conclusion in program, f"零红扫描红结论名单缺少 {conclusion}"


class TestErrorBodyInjectionRedGreen:
    """行为级红绿：403 错误体注入聚合管道——修复前必红，修复后绿。"""

    @pytest.mark.parametrize("marker", ["COLLECTED_COUNT", "ALL_CHECKS"])
    def test_error_body_never_counts_as_check_run(self, marker: str) -> None:
        """单页 403 错误体 → 聚合结果必须零元素（修复前：错误体被计为 1 条）。"""
        program = _extract_jq_program(marker, _action_script())
        out = _run_jq_aggregation(program, [_http_error_body()])
        if marker == "COLLECTED_COUNT":
            assert out == "0", f"403 错误体不得计入聚合长度，实际: {out}"
        else:
            parsed = _run_jq_aggregation(program, [_http_error_body()])
            assert parsed in ("[]", ""), f"403 错误体不得成为 check 实体，实际: {parsed}"

    def test_error_page_with_real_checks_mixed(self) -> None:
        """错误页 + 真实数据页混合（--paginate 实况）：只保留真实 check。"""
        program = _extract_jq_program("ALL_CHECKS", _action_script())
        pages = [
            _http_error_body(),
            {
                "total_count": 2,
                "check_runs": [
                    _check_run(101, status="completed", conclusion="success"),
                    _check_run(102, status="in_progress", conclusion=None),
                ],
            },
        ]
        out = _run_jq_aggregation(program, pages)
        assert '"id":101' in out.replace(" ", ""), "真实 check 101 必须保留"
        assert '"id":102' in out.replace(" ", ""), "真实 check 102 必须保留"
        assert "403" not in out, "错误体不得混入聚合结果"
        assert "Resource not accessible" not in out, "错误 message 不得混入聚合结果"

    def test_valid_aggregation_unchanged(self) -> None:
        """正常 check_runs 页聚合语义不变：completed 成功照常保留。"""
        program = _extract_jq_program("ALL_CHECKS", _action_script())
        pages = [
            {
                "total_count": 1,
                "check_runs": [_check_run(201, status="completed", conclusion="success")],
            }
        ]
        out = _run_jq_aggregation(program, pages)
        assert '"id":201' in out.replace(" ", ""), "正常 check 不得被误删"
        assert '"status":"completed"' in out.replace(" ", "")

    def test_incomplete_count_zero_for_error_only(self) -> None:
        """端到端语义：错误体-only 输入下 INCOMPLETE 计数必为 0（修复前恒 1 死循环）。"""
        collect_program = _extract_jq_program("ALL_CHECKS", _action_script())
        all_checks = _run_jq_aggregation(collect_program, [_http_error_body()])
        incomplete = subprocess.run(
            ["jq", '[.[] | select(.status != "completed")] | length'],
            input=all_checks,
            capture_output=True,
            text=True,
            check=True,
        )
        assert incomplete.stdout.strip() == "0", (
            "403 错误体-only 场景 INCOMPLETE 必须为 0"
            f"（错误体伪装成 incomplete 的死循环曾致合并永不发生），实际: "
            f"{incomplete.stdout.strip()}"
        )


class TestWriteLegSemanticsPreserved:
    """写腿语义不变（防修复漂移）。"""

    def test_squash_delete_branch_merge_command_unchanged(self) -> None:
        script = _action_script()
        assert "--squash" in script and "--delete-branch" in script

    def test_merge_race_guard_unchanged(self) -> None:
        """合并失败竞态守卫保持：re-check state=MERGED 降级成功，否则 exit 1。"""
        script = _action_script()
        assert "PR already merged (race condition)" in script
        assert re.search(r"\bexit 1\b", script)

    def test_zero_red_scan_conclusions_retained(self) -> None:
        """零红扫描四类非绿结论拦截语义不变。"""
        script = _action_script()
        for conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            assert conclusion in script, f"零红扫描缺少 {conclusion} 判定"


class TestFailLoudPrViewRead:
    """首个 gh 调用（pr view 读腿）fail-loud 契约（VAL-L-001，2026-10-13）。

    旧形态 `PR_INFO=$(gh pr view ... 2>&1)` 把 stderr 吞进命令替换变量，
    set -e 在赋值处静默 exit 1——DISPATCH_TOKEN PAT 缺仓授权事件 3/3 连败
    零输出实证（memory run 38352942738 / 38355163542 / 38356323173：
    Gate premise 回显后 ~450-500ms 静默死，零诊断线索）。本类锁定修复形态：
    stderr 落盘、非零退出 ::error::（含失败命令与原因）+ exit 1；
    成功路径结构严格不变。
    """

    def _pr_view_call_line_index(self, lines: list[str]) -> int:
        for i, line in enumerate(lines):
            if "gh pr view" in line and "state,mergeable,headRefOid" in line:
                return i
        pytest.fail("脚本中必须存在首个 gh pr view 读调用（state,mergeable,headRefOid）")

    def _pr_view_to_state_window(self) -> str:
        lines = _action_script().splitlines()
        idx = self._pr_view_call_line_index(lines)
        state_idx = next((i for i, ln in enumerate(lines) if ln.startswith("STATE=")), None)
        assert state_idx is not None and state_idx > idx, "STATE 提取必须发生在 pr view 调用之后"
        return "\n".join(lines[idx : state_idx + 1])

    def test_pr_view_stderr_captured_to_file(self) -> None:
        """pr view 读腿 stderr 必须落盘（禁 2>&1 吞进命令替换变量）。"""
        lines = _action_script().splitlines()
        idx = self._pr_view_call_line_index(lines)
        call_line = lines[idx]
        assert "2>&1" not in call_line, (
            "pr view 读腿禁止 2>&1——stderr 吞进变量后 set -e 静默死"
            "（VAL-L-001 修复前形态，3/3 连败零输出实证）"
        )
        assert '2>"$' in call_line, 'pr view 读腿必须存在 stderr 落盘重定向（2>"$..."）'

    def test_pr_view_stderr_file_cleaned(self) -> None:
        """stderr 落盘文件必须纳入 EXIT trap 清理（对齐 check-runs 读腿卫生）。"""
        script = _action_script()
        assert "PR_VIEW_STDERR_FILE" in script, "stderr 落盘文件变量必须存在"
        trap_line = next((ln for ln in script.splitlines() if ln.startswith("trap ")), "")
        assert "PR_VIEW_STDERR_FILE" in trap_line, "EXIT trap 必须清理 pr view stderr 文件"

    def test_pr_view_failure_fails_loud(self) -> None:
        """pr view 非零退出必须 ::error:: + exit 1，禁止 set -e 静默死。"""
        window = self._pr_view_to_state_window()
        assert "::error::" in window, "pr view 失败分支必须打 ::error:: 注解"
        assert re.search(r"\bexit 1\b", window), "pr view 失败分支必须显式 exit 1（fail-loud）"

    def test_pr_view_error_names_failed_command(self) -> None:
        """::error:: 行本身必须含失败命令字面（gh pr view）——连败零线索的对立面。"""
        window = self._pr_view_to_state_window()
        error_lines = [ln for ln in window.splitlines() if "::error::" in ln]
        assert error_lines, "pr view 失败分支必须存在 ::error:: 行"
        assert any("gh pr view" in ln for ln in error_lines), (
            f"::error:: 行必须点名失败命令（gh pr view），实际: {error_lines}"
        )

    def test_mergeable_requery_fails_loud(self) -> None:
        """UNKNOWN 分支的 mergeable 重查询失败也必须显式报错（禁 set -e 静默死）。"""
        lines = _action_script().splitlines()
        requery_indices = [
            i for i, ln in enumerate(lines) if "gh pr view" in ln and "--json mergeable" in ln
        ]
        assert requery_indices, "必须存在 mergeable 重查询调用（UNKNOWN 分支）"
        for idx in requery_indices:
            window = "\n".join(lines[idx : idx + 6])
            assert "::error::" in window, (
                f"mergeable 重查询（L{idx + 1}）失败路径必须显式 ::error::——禁 set -e 静默死"
            )


class TestPrViewFailLoudBehavior:
    """行为级红绿（VAL-L-001）：真实 bash 执行 composite 脚本 + mock gh。

    失败路径：pr view 非零退出（复刻 DISPATCH_TOKEN 无仓授权事件的 HTTP 404
    形态）→ 修复前静默死（Gate premise 回显后零输出 exit 1），修复后
    ::error:: + 失败命令 + stderr 原因可见。成功路径：全绿 OPEN PR →
    squash 合并，逃生舱 label 路径 → 跳过——行为与修复前一致。
    """

    FAIL_GH_MOCK = """#!/bin/bash
# VAL-L-001 失败路径 mock：pr view 非零退出（404 形态，stderr 是唯一原因载体）
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "view" ]; then
  echo "gh: Could not resolve to a PullRequest for the URL (HTTP 404)" >&2
  exit 1
fi
echo "unexpected gh call: $*" >&2
exit 99
"""

    HAPPY_GH_MOCK = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"state":"OPEN","mergeable":"MERGEABLE","headRefOid":"abc123def456","labels":[]}'
    ;;
  api*)
    echo '{"total_count":4,"check_runs":[{"id":101,"name":"ci","status":"completed","conclusion":"success","details_url":"https://github.com/hdot123/infraro-core/actions/runs/999999","app":{"slug":"github-actions"}},{"id":102,"name":"substrate-gate-suite","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"},{"id":103,"name":"quality-gate","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"},{"id":104,"name":"droid-review","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"}]}'
    ;;
  "pr merge"*)
    echo "squash merged"
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""

    ESCAPE_GH_MOCK = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"state":"OPEN","mergeable":"MERGEABLE","headRefOid":"abc123def456","labels":[{"name":"do-not-merge"}]}'
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""

    def _run_composite(self, gh_mock: str) -> tuple[int, str, str]:
        """以 mock gh 真实 bash 执行 composite run 脚本（env 面与 step 注入等价）。"""
        with tempfile.TemporaryDirectory() as tmp:
            mock_bin = Path(tmp) / "bin"
            mock_bin.mkdir()
            gh_path = mock_bin / "gh"
            gh_path.write_text(gh_mock, encoding="utf-8")
            gh_path.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "PR_NUMBER": "4242",
                "REPO": "hdot123/infraro-core",
                "GITHUB_RUN_ID": "42",
                "GH_TOKEN": "pat-mock-value",
                "GH_READ_TOKEN": "read-mock-value",
            }
            result = subprocess.run(
                ["bash", "-c", _action_script()],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        return result.returncode, result.stdout, result.stderr

    def test_failure_path_is_loud(self) -> None:
        """pr view 失败：exit 1 + ::error:: + 失败命令 + stderr 原因全部可见。"""
        rc, out, _ = self._run_composite(self.FAIL_GH_MOCK)
        assert rc == 1, f"失败路径必须 exit 1（与修复前一致），实际 rc={rc}\n{out}"
        assert "::error::" in out, (
            f"失败路径必须打 ::error::（修复前静默死零输出），实际输出:\n{out}"
        )
        assert "gh pr view" in out, f"错误输出必须点名失败命令（gh pr view），实际输出:\n{out}"
        assert "HTTP 404" in out, (
            f"错误输出必须含失败原因（mock gh 的 stderr 内容），实际输出:\n{out}"
        )

    def test_happy_path_merge_unchanged(self) -> None:
        """成功路径行为不变：OPEN+全绿+MERGEABLE → squash 合并成功回显。"""
        rc, out, _ = self._run_composite(self.HAPPY_GH_MOCK)
        assert rc == 0, f"成功路径必须 exit 0，实际 rc={rc}\n{out}"
        assert "Merging PR #4242" in out, f"成功路径合并回显缺失，实际输出:\n{out}"
        assert "merged successfully" in out, f"成功回显缺失，实际输出:\n{out}"

    def test_do_not_merge_label_skip_unchanged(self) -> None:
        """逃生舱路径行为不变：do-not-merge label 命中 → 跳过回显 + exit 0。"""
        rc, out, _ = self._run_composite(self.ESCAPE_GH_MOCK)
        assert rc == 0, f"逃生舱跳过路径必须 exit 0，实际 rc={rc}\n{out}"
        assert "do-not-merge label present" in out, f"跳过回显缺失，实际输出:\n{out}"
        assert "Merging" not in out, "逃生舱命中后不得进入合并分支"

    def test_state_missing_still_skips_not_fails(self) -> None:
        """API 成功但响应不含 state（异常体）→ 仍走 'Could not get PR info' skip
        分支（exit 0），不得被 fail-loud 误伤——失败语义只覆盖非零退出。"""
        weird_mock = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"oops":true}'
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""
        rc, out, _ = self._run_composite(weird_mock)
        assert rc == 0, f"state 缺失 skip 分支必须保持 exit 0，实际 rc={rc}\n{out}"
        assert "Could not get PR info" in out, f"skip 回显缺失，实际输出:\n{out}"


class TestExitTrapReferentialIntegrity:
    """EXIT trap 引用完整性（scrutiny r1 blocking，0bf64cb 引入）。

    根因：`set -euo pipefail` 下 trap 引用了晚于守卫退出点才赋值的变量
    （CHECK_RUNS_FILE/CHECK_RUNS_STDERR_FILE 在三门绿判之后才 mktemp），
    三门拒合路径 `exit 0` 触发 trap 展开时 unbound variable → 最终退出码 1：
    优雅跳过变红腿，且毒化 failed check run 挡住后续 sweep（零红扫描只
    自排除当前 run id）。两层回归锁：
    - 结构层：trap 单引号体内引用的每个 $VAR 都必须在 trap 行之前赋值；
    - 行为层：真实 bash 执行 composite 脚本，三门拒合（skipped/缺失/红腿）
      必须 exit 0 且零 unbound 噪音；读腿失败路径必须 exit 1 且诊断输出
      不被 trap 噪音污染。
    """

    THREE_GATES_ALL_SKIPPED_MOCK = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"state":"OPEN","mergeable":"MERGEABLE","headRefOid":"abc123def456","labels":[]}'
    ;;
  api*)
    echo '{"total_count":1,"check_runs":[{"id":201,"name":"unrelated-workflow","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"}]}'
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""

    THREE_GATES_MIXED_MOCK = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"state":"OPEN","mergeable":"MERGEABLE","headRefOid":"abc123def456","labels":[]}'
    ;;
  api*)
    echo '{"total_count":4,"check_runs":[{"id":301,"name":"substrate-gate-suite","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"},{"id":302,"name":"quality-gate","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"},{"id":303,"name":"droid-review","status":"completed","conclusion":"skipped","started_at":"2026-10-19T00:00:00Z"},{"id":304,"name":"ci","status":"completed","conclusion":"success","started_at":"2026-10-19T00:00:00Z"}]}'
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""

    API_READ_FAILURE_MOCK = """#!/bin/bash
case "${1:-} ${2:-}" in
  "pr view")
    echo '{"state":"OPEN","mergeable":"MERGEABLE","headRefOid":"abc123def456","labels":[]}'
    ;;
  api*)
    echo "gh: Resource not accessible by personal access token (HTTP 403)" >&2
    exit 1
    ;;
  *)
    echo "unexpected gh call: $*" >&2
    exit 99
    ;;
esac
"""

    def _run_composite(self, gh_mock: str) -> tuple[int, str, str]:
        """与 TestPrViewFailLoudBehavior 同款：mock gh 真实 bash 执行 composite 脚本。"""
        with tempfile.TemporaryDirectory() as tmp:
            mock_bin = Path(tmp) / "bin"
            mock_bin.mkdir()
            gh_path = mock_bin / "gh"
            gh_path.write_text(gh_mock, encoding="utf-8")
            gh_path.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{mock_bin}:{os.environ['PATH']}",
                "PR_NUMBER": "4242",
                "REPO": "hdot123/infraro-core",
                "GITHUB_RUN_ID": "42",
                "GH_TOKEN": "pat-mock-value",
                "GH_READ_TOKEN": "read-mock-value",
            }
            result = subprocess.run(
                ["bash", "-c", _action_script()],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        return result.returncode, result.stdout, result.stderr

    def test_trap_references_assigned_before_install(self) -> None:
        """结构层：trap 单引号体内引用的每个 $VAR 必须在 trap 行之前赋值
        （set -u 下晚赋值变量在任何早期退出路径的 trap 展开中必 unbound）。"""
        script = _action_script()
        lines = script.splitlines()
        trap_lines = [ln for ln in lines if ln.lstrip().startswith("trap ")]
        assert trap_lines, "composite 脚本必须存在 EXIT trap（临时文件清理卫生）"
        for trap_line in trap_lines:
            trap_idx = lines.index(trap_line)
            inner = trap_line[trap_line.index("'") + 1 : trap_line.rindex("'")]
            referenced = sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", inner)))
            assigned_before = {
                m.group(1)
                for ln in lines[:trap_idx]
                if (m := re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=", ln))
            }
            unbound = [v for v in referenced if v not in assigned_before]
            assert not unbound, (
                f"EXIT trap 引用了晚于 trap 装载才赋值的变量 {unbound}——set -u 下"
                f"早期退出路径会被 trap 改写退出码（scrutiny r1 blocking），实际 trap 行: {trap_line}"
            )

    def test_three_gate_rejection_path_exits_zero_when_gates_missing(self) -> None:
        """行为层（修复前红）：check-runs 无任何三门报文 → 三门拒合 → 必须
        exit 0（优雅跳过交下一轮 sweep），不得被 trap unbound 改写成 1。"""
        rc, out, err = self._run_composite(self.THREE_GATES_ALL_SKIPPED_MOCK)
        combined = out + err
        assert rc == 0, f"三门拒合路径（required 面零报文）必须 exit 0，实际 rc={rc}\n{combined}"
        assert "REST three-gate verdict failed" in out, f"三门拒合回显缺失，实际输出:\n{combined}"
        assert "unbound variable" not in combined, (
            f"退出路径不得有 unbound variable 噪音，实际输出:\n{combined}"
        )
        assert "Merging" not in out, "三门拒合后不得进入合并分支"

    def test_three_gate_rejection_path_exits_zero_when_gate_skipped(self) -> None:
        """行为层（修复前红）：三门 SKIPPED（#372 假绿同款形态）→ 拒合 →
        exit 0，退出码不得被 trap 劫持。"""
        rc, out, err = self._run_composite(self.THREE_GATES_MIXED_MOCK)
        combined = out + err
        assert rc == 0, f"三门 SKIPPED 拒合路径必须 exit 0，实际 rc={rc}\n{combined}"
        assert "droid-review" in out and "REST three-gate verdict failed" in out, (
            f"SKIPPED 门必须被点名拒合，实际输出:\n{combined}"
        )
        assert "unbound variable" not in combined, (
            f"退出路径不得有 unbound variable 噪音，实际输出:\n{combined}"
        )

    def test_gates_read_failure_path_diagnosis_clean(self) -> None:
        """读腿失败路径保持 fail-loud exit 1，::error:: 诊断输出完整可见，
        不被 trap unbound 噪音截断。"""
        rc, out, err = self._run_composite(self.API_READ_FAILURE_MOCK)
        combined = out + err
        assert rc == 1, f"读腿失败必须保持 exit 1（fail-loud），实际 rc={rc}\n{combined}"
        assert "::error::three-gate check-runs read failed" in out, (
            f"读腿失败诊断缺失，实际输出:\n{combined}"
        )
        assert "HTTP 403" in out, f"失败原因（stderr）必须可见，实际输出:\n{combined}"
        assert "unbound variable" not in combined, (
            f"fail-loud 路径不得被 trap 噪音污染，实际输出:\n{combined}"
        )
