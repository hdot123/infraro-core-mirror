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
"""

import re
import subprocess
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
        """caller 注入面不变：两载体 merge 步 env.GITHUB_TOKEN 均仍绑 DISPATCH_TOKEN。"""
        for carrier in (
            REPO_ROOT / ".github/workflows/auto-merge.yml",
            REPO_ROOT / ".github/workflows/auto-merge-pipeline.yml",
        ):
            doc = yaml.safe_load(carrier.read_text(encoding="utf-8"))
            steps = doc["jobs"]["auto-merge"]["steps"]
            merge_step = next(
                s
                for s in steps
                if re.search(r"/actions/auto-merge@[0-9a-f]{40}", str(s.get("uses", "")))
            )
            env = merge_step.get("env", {})
            expected = (
                "${{ secrets.dispatch_token }}"
                if "pipeline" in carrier.name
                else ("${{ secrets.DISPATCH_TOKEN }}")
            )
            assert env.get("GITHUB_TOKEN") == expected, (
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
        """读失败分支禁止 exit 0（旧的静默绿腿正是合并永不发生的机制）。"""
        script = _action_script()
        lines = script.splitlines()
        idx = self._read_call_line_index(lines)
        agg_idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("COLLECTED_COUNT=")),
            len(lines) - 1,
        )
        between = "\n".join(lines[idx:agg_idx])
        exit_zero = re.findall(r"\bexit 0\b", between)
        assert not exit_zero, (
            f"读步失败分支不得 exit 0（静默绿=合并永不发生），实际: {len(exit_zero)} 处"
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
