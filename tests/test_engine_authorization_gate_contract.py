"""引擎授权门契约测试（m6-engine-authorization-gate）。

两面覆盖：

A. 入口守卫 authorization-gate job（引擎管线入口三载体 + 消费仓 branch-cleanup 模板）
   - 在场性：job 存在、下游 job 经 needs 受门、最小只读面 actions:read、秒级
   - 语义锚点：ENGINE_CONSUMERS / authorized / 引擎仓豁免 / fail-loud 文案
   - 行为：直接执行 workflow 内联 run 脚本（PATH 前置 gh stub）——授权仓
     pass、未授权 fail、缺 variable fail、读取异常 fail-closed
   - 防漂移：四载体 run 脚本去缩进后逐字节一致（改一处必须四处同改）

B. 每周 drift 审计 engine-consumer-audit.yml
   - 触发（weekly schedule + dispatch）/ 顶层只读权限 / 串行并发
   - 扫描面：候选仓发现、引擎引用全文匹配、variable 对照、不可达优雅降级
   - 幂等：固定标题指纹 + list/comment/create/close 分支（stub 行为断言）

静态锚点（INFRA-715 先例）：治理基线字面量在本文件常量与 workflow 内联脚本
双处钉死——改基线必须两处同改，否则本套测试红。

凭证现实：本套测试全离线（gh stub + 本地 bash），CI 内无凭证也全量执行。
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.security, pytest.mark.schema]

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# ── 治理基线锚点（与 workflow 内联脚本双处钉死）──────────────────────────
GATE_JOB_KEY = "authorization-gate"
GATE_JOB_NAME = "authorization-gate"
VARIABLE_NAME = "ENGINE_CONSUMERS"
AUTHORIZED_TOKEN = "authorized"
EXEMPT_REPO = "hdot123/infraro-core"
FAIL_MESSAGE = "未授权消费引擎，走授权流程"
AUDIT_ALERT_TITLE = "Engine consumer audit: unauthorized engine references"

GATE_CARRIERS: dict[str, tuple[str, ...]] = {
    ".github/workflows/auto-merge-pipeline.yml": ("resolve",),
    ".github/workflows/droid-review-shards.yml": ("setup",),
    ".github/workflows/branch-cleanup.yml": ("cleanup",),
}
CONSUMER_TEMPLATE = Path("docs/onboarding/templates/branch-cleanup.thin-caller.yml")
ALL_GATE_FILES = tuple(GATE_CARRIERS) + (str(CONSUMER_TEMPLATE),)

AUDIT_WORKFLOW = Path(".github/workflows/engine-consumer-audit.yml")
ONBOARDING_DOC = Path("docs/onboarding/consumer-onboarding.md")


# ── helpers ────────────────────────────────────────────────────────────────


def _path(rel: str) -> Path:
    return REPO_ROOT / rel


def _load(rel: str) -> dict[str, Any]:
    doc = yaml.safe_load(_path(rel).read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{rel} 必须是 YAML mapping"
    return doc


def _gate_job(rel: str) -> dict[str, Any]:
    jobs = _load(rel).get("jobs") or {}
    assert GATE_JOB_KEY in jobs, f"{rel} 缺少入口守卫 job {GATE_JOB_KEY}"
    job = jobs[GATE_JOB_KEY]
    assert isinstance(job, dict), f"{rel} 的 {GATE_JOB_KEY} 必须是 mapping"
    return job


def _gate_run(rel: str) -> str:
    steps = _gate_job(rel).get("steps") or []
    assert len(steps) == 1, f"{rel} 的守卫 job 应恰好一个 step（秒级、只读）"
    run = steps[0].get("run")
    assert isinstance(run, str) and run.strip(), f"{rel} 守卫 step 必须有 run 脚本"
    return run


def _audit_steps() -> dict[str, dict[str, Any]]:
    steps = _load(str(AUDIT_WORKFLOW))["jobs"]["audit"]["steps"]
    return {str(step.get("name")): step for step in steps}


def _audit_run(name: str) -> str:
    step = _audit_steps()[name]
    run = step.get("run")
    assert isinstance(run, str) and run.strip(), f"审计 step {name} 必须有 run 脚本"
    return run


def _normalized(script: str) -> str:
    return textwrap.dedent(script).strip() + "\n"


def _rel_text(rel: str) -> str:
    return _path(rel).read_text(encoding="utf-8")


def _base_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "github_output.txt"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            "STUB_CALLS": str(tmp_path / "stub-calls.log"),
        }
    )
    return env


def _run_bash(script: str, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    script_path = tmp_path / "extracted.sh"
    script_path.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["bash", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )


GATE_STUB_GH = """#!/usr/bin/env bash
# 最小 gh stub：只回 repo variable 端点，按 STUB_MODE 分支；调用落 STUB_CALLS。
if [ "${1:-}" != "api" ]; then
  echo "stub gh: unexpected argv: $*" >&2
  exit 3
fi
if [ -n "${STUB_CALLS:-}" ]; then
  echo "api $*" >>"${STUB_CALLS}"
fi
case "${STUB_MODE}" in
  authorized) echo '{"name":"ENGINE_CONSUMERS","value":"authorized"}'; exit 0 ;;
  spaced) echo '{"name":"ENGINE_CONSUMERS","value":" prod , AUTHORIZED "}'; exit 0 ;;
  other) echo '{"name":"ENGINE_CONSUMERS","value":"no"}'; exit 0 ;;
  empty) echo '{"name":"ENGINE_CONSUMERS","value":""}'; exit 0 ;;
  missing) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
  forbidden) echo '{"message":"Resource not accessible by integration","status":"403"}' >&2; exit 1 ;;
  error) echo "gh: Server Error (HTTP 500)" >&2; exit 1 ;;
  *) echo "stub gh: unknown STUB_MODE ${STUB_MODE}" >&2; exit 3 ;;
esac
"""


AUDIT_STUB_GH = '''#!/usr/bin/env python3
"""gh stub：离线驱动 engine-consumer-audit 三个 step（scenario JSON 驱动）。"""
import json
import os
import sys

scenario = json.load(open(os.environ["STUB_SCENARIO"]))
calls_log = os.environ.get("STUB_CALLS")
args = sys.argv[1:]
REPOS = {r["full_name"]: r for r in scenario.get("repos", [])}


def log(line):
    if calls_log:
        with open(calls_log, "a", encoding="utf-8") as fh:
            fh.write(line + "\\n")


def out(text):
    sys.stdout.write(text if text.endswith("\\n") else text + "\\n")


def parse(argv):
    positional, flags = [], {}
    i = 0
    while i < len(argv):
        item = argv[i]
        if item in ("-H", "--header", "-f", "-X", "--method", "--jq", "--json",
                    "--label", "--search", "--body", "--state", "--title", "--reason"):
            flags[item] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif item.startswith("-"):
            i += 1
        else:
            positional.append(item)
            i += 1
    return positional, flags


positional, flags = parse(args)
mode = positional[0] if positional else ""

if mode == "api":
    endpoint = positional[1] if len(positional) > 1 else ""
    log("api " + endpoint)
    if endpoint.startswith("users/") or endpoint.startswith("user/repos"):
        channel = "public" if endpoint.startswith("users/") else "token"
        rows = [
            name + "\\t" + repo.get("branch", "main")
            for name, repo in REPOS.items()
            if channel in repo.get("discover_via", ["public", "token"])
        ]
        out("\\n".join(rows))
        sys.exit(0)
    if endpoint.endswith("/actions/variables/ENGINE_CONSUMERS"):
        repo = REPOS[endpoint[len("repos/"):-len("/actions/variables/ENGINE_CONSUMERS")]]
        if repo.get("variable_error"):
            print('{"message":"Resource not accessible by integration","status":"403"}', file=sys.stderr)
            sys.exit(1)
        if repo.get("variable") is None:
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        out(repo["variable"] if flags.get("--jq") == ".value"
            else json.dumps({"name": "ENGINE_CONSUMERS", "value": repo["variable"]}))
        sys.exit(0)
    if "/git/trees/" in endpoint:
        repo = REPOS[endpoint[len("repos/"):].split("/git/trees/")[0]]
        if repo.get("unreachable"):
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        out("\\n".join(repo.get("workflow_files", [])))
        sys.exit(0)
    if "/contents/" in endpoint:
        head, path = endpoint[len("repos/"):].split("/contents/", 1)
        out(REPOS[head].get("files", {}).get(path, ""))
        sys.exit(0)
    if endpoint.startswith("repos/") and endpoint.count("/") == 2:
        repo = REPOS.get(endpoint[len("repos/"):])
        if repo is None or repo.get("unreachable"):
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        out(repo.get("visibility", "private") if flags.get("--jq") == ".visibility"
            else json.dumps({"visibility": repo.get("visibility", "private")}))
        sys.exit(0)
    print("stub: unmapped api endpoint " + endpoint, file=sys.stderr)
    sys.exit(3)

if mode == "issue":
    sub = positional[1]
    if sub == "list":
        log("issue list")
        number = scenario.get("existing_issue")
        out(str(number) if number else "")
        sys.exit(0)
    if sub == "create":
        log("issue create")
        out("https://github.com/hdot123/infraro-core/issues/999")
        sys.exit(0)
    if sub == "comment":
        log("issue comment " + positional[2])
        sys.exit(0)
    if sub == "close":
        log("issue close " + positional[2])
        sys.exit(0)
    print("stub: unmapped issue subcommand " + sub, file=sys.stderr)
    sys.exit(3)

print("stub: unmapped mode " + mode, file=sys.stderr)
sys.exit(3)
'''


def _write_exec(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _stub_env(
    tmp_path: Path, stub_source: str, scenario: dict[str, Any] | None = None
) -> dict[str, str]:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir(exist_ok=True)
    _write_exec(stub_dir / "gh", stub_source)
    env = _base_env(tmp_path)
    env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"
    if scenario is not None:
        scenario_path = tmp_path / "scenario.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        env["STUB_SCENARIO"] = str(scenario_path)
    return env


def _audit_env(env: dict[str, str]) -> dict[str, str]:
    env.update(
        {
            "GH_TOKEN": "stub",
            "OWNER": "hdot123",
            "ENGINE_REPO": EXEMPT_REPO,
            "VARIABLE_NAME": VARIABLE_NAME,
            "AUTHORIZED_TOKEN": AUTHORIZED_TOKEN,
            "ALERT_LABEL": "automation",
            "ALERT_TITLE": AUDIT_ALERT_TITLE,
        }
    )
    return env


def _audit_scenario(existing_issue: int | None = None) -> dict[str, Any]:
    engine_yml = ".github/workflows/auto-merge.yml"
    return {
        "existing_issue": existing_issue,
        "repos": [
            {
                "full_name": EXEMPT_REPO,
                "branch": "main",
                "visibility": "public",
                "discover_via": ["public", "token"],
                "variable": AUTHORIZED_TOKEN,
                "workflow_files": [engine_yml],
                "files": {engine_yml: "jobs:\n  gate:\n    uses: hdot123/infraro-core/x@v1\n"},
            },
            {
                "full_name": "hdot123/infraro",
                "branch": "main",
                "visibility": "public",
                "discover_via": ["public", "token"],
                "variable": None,
                "workflow_files": [engine_yml],
                "files": {engine_yml: "uses: hdot123/infraro-core/x@v0.18.7\n"},
            },
            {
                "full_name": "hdot123/consumer-a",
                "branch": "main",
                "visibility": "private",
                "discover_via": ["token"],
                "variable": AUTHORIZED_TOKEN,
                "workflow_files": [engine_yml],
                "files": {engine_yml: "uses: hdot123/infraro-core/x@v0.18.7\n"},
            },
            {
                "full_name": "hdot123/private-outside-token",
                "branch": "main",
                "visibility": "private",
                "discover_via": ["token"],
                "unreachable": True,
            },
            {
                "full_name": "hdot123/var-unreadable",
                "branch": "main",
                "visibility": "private",
                "discover_via": ["token"],
                "variable": None,
                "variable_error": True,
                "workflow_files": [engine_yml],
                "files": {engine_yml: "uses: hdot123/infraro-core/x@v0.18.7\n"},
            },
        ],
    }


# ── A. 入口守卫：在场性 ────────────────────────────────────────────────────


class TestGateJobPresence:
    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_job_present_and_self_contained(self, rel: str) -> None:
        """守卫 job 在场：唯一 step、ubuntu-latest、秒级超时（不延长管线）。"""
        job = _gate_job(rel)
        assert job.get("name") == GATE_JOB_NAME, f"{rel} 守卫 job 显示名漂移"
        assert job.get("runs-on") == "ubuntu-latest", f"{rel} 守卫 job 必须 GitHub-hosted（普适）"
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and timeout <= 5, (
            f"{rel} 守卫 job 必须秒级有界（timeout-minutes<=5），实际 {timeout}"
        )
        steps = job.get("steps") or []
        assert len(steps) == 1, f"{rel} 守卫 job 必须单 step（只读判定，无副作用）"
        assert "uses" not in steps[0], f"{rel} 守卫 step 不得引用远程 action（零外部依赖）"

    @pytest.mark.parametrize(("rel", "gated_jobs"), sorted(GATE_CARRIERS.items()))
    def test_downstream_jobs_gated_by_needs(self, rel: str, gated_jobs: tuple[str, ...]) -> None:
        """入口 job 必须 needs 守卫——未授权时下游不启动（fail-closed 门）。"""
        jobs = _load(rel)["jobs"]
        for job_key in gated_jobs:
            assert job_key in jobs, f"{rel} 缺少下游 job {job_key}"
            needs = jobs[job_key].get("needs")
            needs_list = [needs] if isinstance(needs, str) else list(needs or [])
            assert GATE_JOB_KEY in needs_list, (
                f"{rel} 的 {job_key} 必须 needs {GATE_JOB_KEY}（未授权 fail-closed）"
            )

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_job_minimal_read_only_surface(self, rel: str) -> None:
        """凭证面最小化：仅 actions:read（读 repo variable），零 write、零 PAT。"""
        job = _gate_job(rel)
        assert job.get("permissions") == {"actions": "read"}, (
            f"{rel} 守卫 job permissions 必须精确 {{'actions': 'read'}}（实测最小面），"
            f"实际 {job.get('permissions')}"
        )
        script = _gate_run(rel)
        assert "secrets." not in script, f"{rel} 守卫脚本不得消费 secrets（GITHUB_TOKEN 只读即可）"
        gate_job_yaml = yaml.safe_dump(_gate_job(rel), allow_unicode=True)
        assert "secrets." not in gate_job_yaml, f"{rel} 守卫 job 不得引用 secrets 上下文"

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_reads_event_repository_full_name(self, rel: str) -> None:
        """repo 全名读 github.event.repository.full_name；授权值优先取 vars 上下文。"""
        step_env = _gate_job(rel)["steps"][0].get("env") or {}
        repo_expr = str(step_env.get("REPO", ""))
        assert "github.event.repository.full_name" in repo_expr, (
            f"{rel} 守卫 REPO env 必须读 github.event.repository.full_name，实际 {repo_expr!r}"
        )
        assert "github.repository" in repo_expr, f"{rel} 守卫 REPO env 缺少 github.repository 兜底"
        assert step_env.get("GH_TOKEN") == "${{ github.token }}", (
            f"{rel} 守卫必须用 github.token（GITHUB_TOKEN），不消费 PAT"
        )
        assert step_env.get("VARS_VALUE") == f"${{{{ vars.{VARIABLE_NAME} || '' }}}}", (
            f"{rel} 守卫必须注入 vars 上下文值（零权限路径）且带 || '' 兜底"
            f"（variable 允许缺席，见命名契约 vars 存在性测试），实际 {step_env.get('VARS_VALUE')!r}"
        )


# ── A. 入口守卫：语义锚点与防漂移 ─────────────────────────────────────────


class TestGateSemantics:
    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_script_carries_governance_anchors(self, rel: str) -> None:
        """治理基线字面量：variable 名 / authorized 令牌 / 引擎仓豁免 / fail-loud 文案。"""
        script = _gate_run(rel)
        assert "actions/variables/${VARIABLE_NAME}" in script, f"{rel} 必须读 repo variable 端点"
        assert f'VARIABLE_NAME="${{ENGINE_AUTHORIZATION_VARIABLE:-{VARIABLE_NAME}}}"' in script
        assert f"ENGINE_AUTHORIZATION_TOKEN:-{AUTHORIZED_TOKEN}" in script
        assert f"ENGINE_AUTHORIZATION_EXEMPT_REPOS:-{EXEMPT_REPO}" in script
        assert f'ALERT="{FAIL_MESSAGE}"' in script, f"{rel} 缺 fail-loud 告警文案锚点"
        assert "exit 1" in script and "exit 0" in script

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_prefers_vars_context_with_api_fallback(self, rel: str) -> None:
        """判定顺序：vars 上下文（零权限）优先，gh api 回退（GITHUB_TOKEN 实测 403）。"""
        script = _gate_run(rel)
        assert 'VARS_VALUE="${VARS_VALUE:-}"' in script, f"{rel} 必须读取 vars 上下文注入值"
        assert 'matches_token "${VARS_VALUE}"' in script, f"{rel} 必须先判 vars 值"
        assert "@vars" in script and "@api" in script, f"{rel} 必须区分 vars / api 两条判定来源"
        vars_pos = script.index('matches_token "${VARS_VALUE}"')
        api_pos = script.index('gh api "repos/')
        assert vars_pos < api_pos, f"{rel} vars 判定必须早于 api 回退（最短路径零 API 调用）"

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_script_fails_closed_on_read_errors(self, rel: str) -> None:
        """读取异常（非 404）也必须 fail-closed，不静默放行。"""
        script = _gate_run(rel)
        assert 'grep -q "404"' in script, f"{rel} 必须区分「variable 缺席(404)」与「读取异常」"
        assert "授权状态不可读" in script, f"{rel} 读取异常路径必须显式报错"

    def test_all_carrier_scripts_byte_identical_after_dedent(self) -> None:
        """四载体守卫脚本去缩进后逐字节一致——单一语义，改一处必须四处同改。"""
        normalized = {rel: _normalized(_gate_run(rel)) for rel in ALL_GATE_FILES}
        unique = set(normalized.values())
        assert len(unique) == 1, "守卫脚本多形态漂移（四载体必须逐字节一致）：" + "; ".join(
            f"{rel}={len(text)} chars" for rel, text in normalized.items()
        )


# ── A. 入口守卫：行为（离线执行内联脚本）─────────────────────────────────


_GATE_SCENARIOS = {
    # scenario: (repo, vars_value, stub_mode, expected_rc, needle)
    "exempt_engine_repo": (EXEMPT_REPO, "", "missing", 0, "豁免"),
    "vars_authorized": ("hdot123/consumer-a", AUTHORIZED_TOKEN, "missing", 0, "@vars"),
    "vars_authorized_spaced_case": (
        "hdot123/consumer-a",
        " prod , AUTHORIZED ",
        "missing",
        0,
        "@vars",
    ),
    "api_fallback_authorized": ("hdot123/consumer-a", "", "authorized", 0, "@api"),
    "unauthorized_other_value": ("hdot123/consumer-a", "", "other", 1, FAIL_MESSAGE),
    "unauthorized_empty_value": ("hdot123/consumer-a", "", "empty", 1, FAIL_MESSAGE),
    "unauthorized_variable_missing": ("hdot123/consumer-a", "", "missing", 1, FAIL_MESSAGE),
    "unauthorized_vars_other_value": ("hdot123/consumer-a", "no", "missing", 1, FAIL_MESSAGE),
    "fail_closed_on_api_403": ("hdot123/consumer-a", "", "forbidden", 1, FAIL_MESSAGE),
    "fail_closed_on_read_error": ("hdot123/consumer-a", "", "error", 1, FAIL_MESSAGE),
}


class TestGateBehavior:
    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    @pytest.mark.parametrize(("scenario", "expected"), sorted(_GATE_SCENARIOS.items()))
    def test_gate_decision_matrix(
        self,
        rel: str,
        scenario: str,
        expected: tuple[str, str, str, int, str],
        tmp_path: Path,
    ) -> None:
        """逐载体执行内联守卫脚本：vars/api 授权 pass、未授权与异常 fail-loud。"""
        repo, vars_value, stub_mode, expected_rc, needle = expected
        env = _stub_env(tmp_path, GATE_STUB_GH)
        env.update({"STUB_MODE": stub_mode, "REPO": repo, "VARS_VALUE": vars_value})
        result = _run_bash(_gate_run(rel), tmp_path, env)
        output = result.stdout + result.stderr
        assert result.returncode == expected_rc, (
            f"{rel} 场景 {scenario} 期望 rc={expected_rc}，实际 {result.returncode}；输出：{output}"
        )
        assert needle in output, f"{rel} 场景 {scenario} 输出缺少 {needle!r}：{output}"

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_vars_path_makes_zero_api_calls(self, rel: str, tmp_path: Path) -> None:
        """vars 命中即短路：零 API 调用、零权限依赖（最小面实证）。"""
        env = _stub_env(tmp_path, GATE_STUB_GH)
        env.update(
            {"STUB_MODE": "forbidden", "REPO": "hdot123/consumer-a", "VARS_VALUE": AUTHORIZED_TOKEN}
        )
        result = _run_bash(_gate_run(rel), tmp_path, env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (tmp_path / "stub-calls.log").exists(), (
            f"{rel} vars 命中时不得发起 API 调用（stub 被调用即违规）"
        )

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_failure_is_loud_and_actionable(self, rel: str, tmp_path: Path) -> None:
        """未授权失败必须是 ::error:: 注解 + 指向授权流程（不静默）。"""
        env = _stub_env(tmp_path, GATE_STUB_GH)
        env.update({"STUB_MODE": "missing", "REPO": "hdot123/consumer-a", "VARS_VALUE": ""})
        result = _run_bash(_gate_run(rel), tmp_path, env)
        assert result.returncode == 1
        assert "::error::" in result.stdout, f"{rel} 必须用 ::error:: 注解暴露失败"
        assert "ENGINE_CONSUMERS" in result.stdout, f"{rel} 必须指明缺的 variable 名"
        assert "repo variable" in result.stdout, f"{rel} 必须指明这是 repo variable 面"

    @pytest.mark.parametrize("rel", ALL_GATE_FILES)
    def test_gate_exempt_repo_skips_variable_lookup(self, rel: str, tmp_path: Path) -> None:
        """引擎仓豁免必须在任何判定/API 调用前短路。"""
        env = _stub_env(tmp_path, GATE_STUB_GH)
        env.update({"STUB_MODE": "authorized", "REPO": EXEMPT_REPO, "VARS_VALUE": ""})
        result = _run_bash(_gate_run(rel), tmp_path, env)
        assert result.returncode == 0
        assert "豁免" in result.stdout, f"{rel} 引擎仓豁免路径未短路"
        assert not (tmp_path / "stub-calls.log").exists(), f"{rel} 豁免路径不得发起 API 调用"


# ── B. 审计 workflow：结构与幂等契约 ──────────────────────────────────────


class TestAuditWorkflowStructure:
    def test_audit_workflow_exists_with_weekly_schedule_and_dispatch(self) -> None:
        """触发面：weekly schedule + workflow_dispatch（人工可重跑取证）。"""
        doc = _load(str(AUDIT_WORKFLOW))
        triggers = doc.get("on") or doc.get(True) or {}
        assert "workflow_dispatch" in triggers, "审计 workflow 必须支持手动 dispatch"
        schedule = triggers.get("schedule")
        assert isinstance(schedule, list) and len(schedule) == 1, "审计 schedule 必须单条（weekly）"
        cron = str(schedule[0].get("cron", ""))
        assert cron == "0 2 * * 1", f"审计必须每周一执行（weekly），实际 cron={cron!r}"

    def test_audit_permissions_minimal(self) -> None:
        """权限面：顶层只读基线；issues:write 仅下放审计 job。"""
        doc = _load(str(AUDIT_WORKFLOW))
        assert doc.get("permissions") == {"contents": "read"}, (
            f"审计顶层必须 contents:read 基线，实际 {doc.get('permissions')}"
        )
        job = doc["jobs"]["audit"]
        assert job.get("permissions") == {"contents": "read", "issues": "write"}, (
            f"审计 job 只应多开 issues:write，实际 {job.get('permissions')}"
        )
        assert doc.get("concurrency", {}).get("group") == "engine-consumer-audit"
        assert doc["concurrency"].get("cancel-in-progress") is False

    def test_audit_scan_surface_complete(self) -> None:
        """扫描面：候选仓发现（公开 + token 可见）、引擎引用匹配、variable 对照。"""
        script = _audit_run("Scan account for engine references and authorization")
        assert "users/${OWNER}/repos" in script, "缺少账号公开仓发现"
        assert "user/repos" in script, "缺少 token 可见仓发现（私有仓覆盖）"
        assert "select(.fork == false and .archived == false)" in script, "必须过滤 fork / archived"
        assert "git/trees/${branch}?recursive=1" in script, "必须走默认分支 workflow 树扫描"
        assert 'grep -n "${ENGINE_REPO}"' in script, "必须全文匹配引擎仓引用"
        assert "actions/variables/${VARIABLE_NAME}" in script, "必须回读各仓 ENGINE_CONSUMERS"
        assert 'var_state="unknown"' in script, "非 404 读错误必须记 unknown（防误报）"
        assert 'if [ "${refs}" -gt 0 ] && [ "${var_state}" = "unauthorized" ]' in script, (
            "违规判定必须只认确认未授权（unknown 不计违规）"
        )
        assert 'if [ "${full}" = "${ENGINE_REPO}" ]' in script, "引擎仓自身必须豁免"
        assert "不可达" in script, "必须优雅降级记录不可达仓（token 范围）"
        assert "::error::候选仓发现结果为 0" in script, "候选仓空集必须 fail（防静默空审计）"

    def test_audit_report_published_to_step_summary(self) -> None:
        """报告双载体：日志 + GITHUB_STEP_SUMMARY（首跑 success 有产出）。"""
        publish = _audit_run("Publish audit report")
        assert "GITHUB_STEP_SUMMARY" in publish, "报告必须写 step summary"
        assert "report.md" in publish, "报告必须来自 scan 步产物"

    def test_audit_issue_alert_is_idempotent(self) -> None:
        """幂等 Issue：固定标题指纹 + list 查重 → comment 追加 / 仅新建一次。"""
        script = _audit_run("Open or update drift alert (idempotent)")
        assert AUDIT_ALERT_TITLE == str(
            _load(str(AUDIT_WORKFLOW))["jobs"]["audit"]["env"]["ALERT_TITLE"]
        )
        assert 'in:title \\"${ALERT_TITLE}\\"' in script, (
            "必须按标题指纹查重（budget-guard 幂等模式）"
        )
        assert 'gh issue comment "${EXISTING}"' in script, "已存在同指纹 issue 时必须追加评论"
        assert "gh issue create" in script, "无同指纹 issue 时才新建"
        assert "VIOLATION_COUNT" in script, "必须按违规计数分支（零违规不重复开单）"


# ── B. 审计行为（离线执行三个 step）──────────────────────────────────────


class TestAuditBehavior:
    def _scan(
        self, tmp_path: Path, scenario: dict[str, Any]
    ) -> tuple[subprocess.CompletedProcess, str]:
        env = _audit_env(_stub_env(tmp_path, AUDIT_STUB_GH, scenario))
        scan = _audit_run("Scan account for engine references and authorization")
        result = _run_bash(scan, tmp_path, env)
        report_path = tmp_path / "engine-consumer-audit" / "report.md"
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        outputs = (tmp_path / "github_output.txt").read_text(encoding="utf-8")
        return result, report + "\n##OUTPUTS##\n" + outputs

    def test_scan_classifies_and_reports(self, tmp_path: Path) -> None:
        """分类正确：授权 pass / 未授权引用 violation / 变量不可读 unknown / 豁免 / 不可达。"""
        result, report = self._scan(tmp_path, _audit_scenario())
        assert result.returncode == 0, result.stdout + result.stderr
        assert "engine consumer audit complete" in result.stdout
        assert "violations=1" in report, f"应仅 infraro 计违规（不可读仓不计）：{report}"
        assert "candidates=5" in report, report
        assert "变量不可读：1" in report, report
        assert "引擎仓自身（exempt）" in report, report
        assert "不可达（token 范围外，不计判定）" in report, report
        assert "unknown（变量不可读，不计违规）" in report, report
        assert "VIOLATION 未授权引用" in report, report

    def test_scan_violation_details_exclude_authorized_repos(self, tmp_path: Path) -> None:
        """违规明细只列违规仓（授权仓与不可读仓的引用行不得混入）。"""
        _, report = self._scan(tmp_path, _audit_scenario())
        details = report.split("### 违规明细", 1)[1]
        assert "hdot123/infraro" in details, details
        assert "hdot123/consumer-a" not in details, f"授权仓不得出现在违规明细：{details}"
        assert "hdot123/var-unreadable" not in details, f"不可读仓不得出现在违规明细：{details}"

    def _alert(
        self, tmp_path: Path, scenario: dict[str, Any], violation_count: int
    ) -> tuple[subprocess.CompletedProcess, str]:
        env = _audit_env(_stub_env(tmp_path, AUDIT_STUB_GH, scenario))
        env["VIOLATION_COUNT"] = str(violation_count)
        (tmp_path / "engine-consumer-audit").mkdir(exist_ok=True)
        (tmp_path / "engine-consumer-audit" / "report.md").write_text(
            "## 审计报告\n", encoding="utf-8"
        )
        script = _audit_run("Open or update drift alert (idempotent)")
        result = _run_bash(script, tmp_path, env)
        calls_path = tmp_path / "stub-calls.log"
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        return result, calls

    def test_alert_opens_issue_on_violation(self, tmp_path: Path) -> None:
        """违规 + 无同指纹 issue → 新建告警。"""
        result, calls = self._alert(tmp_path, _audit_scenario(), violation_count=2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "issue create" in calls, calls
        assert "issue comment" not in calls, calls

    def test_alert_comments_instead_of_duplicating(self, tmp_path: Path) -> None:
        """违规 + 已有同指纹 issue → 只追加评论（幂等，不重复开单）。"""
        result, calls = self._alert(tmp_path, _audit_scenario(existing_issue=42), violation_count=2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "issue comment 42" in calls, calls
        assert "issue create" not in calls, calls

    def test_alert_closes_on_recovery(self, tmp_path: Path) -> None:
        """零违规 + 既有告警 → 评论并关闭（漂移恢复生命周期）。"""
        result, calls = self._alert(tmp_path, _audit_scenario(existing_issue=42), violation_count=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "issue comment 42" in calls and "issue close 42" in calls, calls
        assert "issue create" not in calls, calls

    def test_alert_noop_when_clean_without_issue(self, tmp_path: Path) -> None:
        """零违规 + 无既有告警 → 零动作（幂等 no-op）。"""
        result, calls = self._alert(tmp_path, _audit_scenario(), violation_count=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "issue create" not in calls and "issue comment" not in calls, calls


# ── C. 设计文档段落（授权门三件套 + 授权四步）────────────────────────────


class TestAuthorizationGateDocContract:
    def test_onboarding_doc_documents_authorization_gate(self) -> None:
        """接入指南必须记载授权门三件套与授权四步（消费仓施工依据）。"""
        content = _path(str(ONBOARDING_DOC)).read_text(encoding="utf-8")
        assert VARIABLE_NAME in content, "文档必须声明 ENGINE_CONSUMERS variable 三件套"
        assert "PAT" in content and "runner" in content, "文档必须覆盖 PAT 范围与 runner 注册"
        for step in ("variable", "PAT", "runner"):
            assert step in content, f"授权四步缺少 {step}"
        assert "6 阶段" in content or "六阶段" in content, "文档必须指向 6 阶段验收"
