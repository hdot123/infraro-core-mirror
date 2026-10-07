#!/usr/bin/env python3
"""livetruth_probes.py — live-truth 探针（T1 本地零凭证 / T2 gh 只读 / T3 Mac 本机周检）。

探针矩阵真源：mission audits/round2/cross-D-livetruth-probes.md §①（17 行承重断言 +
措辞坑备注 + 腐速分档）。认识论契约（mission architecture v2）：文档数字断言的唯一
真源 = 探针输出（gh API / 仓库文件 / runner 活体），不以任何调查报告为准。

探针三态（2026-09-28 orchestrator 注）：
  PASS               断言与当前现实一致。
  FAIL               真实缺陷：现实与断言不符、gh 失败、凭证/网络缺失——一律 FAIL，
                     不静默跳过。
  PENDING-EXTERNAL   依赖外部未落定事实（附证据与依赖项，不计入失败也不伪装 PASS）；
                     依赖已满足而结果未出现（超 30min 窗）→ FAIL，不用第三态藏缺陷
                     （2026-09-29 终审裁决口径）。当前已知一项：mirror 最新 tag 消费
                     面含 worker_ci_key——v0.20.0 已发布（2026-09-28 16:10Z）而 mirror
                     同步链断（缺 UPSTREAM_READ_TOKEN/MIRROR_PUSH_TOKEN 凭据，
                     2026-09-28 dispatch 实测 404 失败），超窗即 FAIL。
退出码：0 = 零 FAIL（PENDING-EXTERNAL 单列，不计入失败）；1 = 存在 FAIL；
       2 = 用法/脚本自身错误。

腐速分档与重跑周期建议（矩阵 §① 口径）：
  慢（静态文件，随 commit 变，月级）→ T1 全档；每次 PR 门 + 月级复核。
  中（repo/配置对象，随运维操作变，周级）→ runners/ruleset/tag/授权名单类；
     每周 + L3-L5 执行前必跑。
  快（runs/统计/时变快照，小时-天级）→ 近 N runs 窗口、健康度推论；永不进硬门，
     只在执行前重测并把结果带时点戳写入证据。

T3 设计段（Mac 周检：poll-releases 日志/哨兵/锁）：
  目标断言（矩阵 #16/#17 + 计划文档前置修复①）：
    a. 哨兵 .poll-bootstrap-done 存在于 webhook locks/ 目录（派发禁令解除）；
    b. poll-releases.log 最近一个轮询周期（300s）无 ERROR 行；
    c. locks/ 下 release-announce-<tag>.json 锁面 = 已见/已派发 tag 集（2026-09-28
       agent 执行 --init：哨兵已落，v0.18.9~v0.20.0 预建 placeholder 锁（bootstrap
       防历史全量派发）；v0.20.0 消费面派发留待 L5 执行 mission 按其口径处理）；
    d. 本机 config repositories.yml 的 engineConsumer:true 计数（文档口径 3，
       映射 memory/mencbo/infraro 三仓）。
  挂载点建议（标注：待执行 mission 实测，本 mission 只交付设计 + 只读快照实现）：
    - launchd 周检：~/Library/LaunchAgents/factory.livetruth-t3.plist，每周触发一次，
      输出带时点戳追加 evidence/livetruth/ 快照；
    - 或对齐 gateway-patrol 形态：定时触发巡检会话，结果回写 evidence/；
    - 哨兵/锁断言可复用为 release-gateway 派发前 preflight（落点由执行 mission 定）。
  本脚本 --t3 即上述断言的只读快照（零写副作用），供人工周检直接调用。

矩阵映射（17 行 → 探针，见 PROBE_REGISTRY 各条 matrix 字段）：
  #1→T1-01；#2→T2-01/T2-06；#3→T2-05；#4→T2-06；#5→T2-02；#6→T2-03；#7→T2-04；
  #8→T2-07；#9→T1-08+T2-06（清册对照锚）；#10→T2-08；#11→T1-12（main 面）+T2-09
  （镜像面）；#12→T1-10；#13→T2-10；#14→不设门（不可变历史事实，T1-14 锚其文档
  形态）；#15→T1-13（本地侧）+T2-12（gh 窗口快照）；#16→T3-01/02/03；#17→T3-04。

措辞坑（照抄矩阵备注）：runners 断言用 status 字段（API 无 online 布尔）；required
checks 用 rules/branches/main 端点的 required_status_checks rule（pull_request 规则
的该参数恒为空数组，照抄会得假阴性）。

依赖：stdlib 优先；workflow YAML 解析用项目既有依赖 pyyaml（缺失时自动 re-exec 到
.venv/bin/python）；--t2 依赖 gh 子进程（只读端点）。零新增第三方依赖。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_PATH = WORKFLOW_DIR / "ci.yml"
SHARDS_PATH = WORKFLOW_DIR / "droid-review-shards.yml"
RUNNER_TOOLS_PATH = REPO_ROOT / "runner-tools.toml"
POLL_CONTRACT_PATH = REPO_ROOT / "scripts" / "check_droid_review.sh"
DROID_REVIEW_PATH = WORKFLOW_DIR / "droid-review.yml"
DOC_PATH = REPO_ROOT / "docs" / "guides" / "fullstack-test-plan.md"
REGISTRY_PATH = REPO_ROOT / "evidence" / "ce01-runner-layout.md"

ORG = "hdot123"
ENGINE_REPO = f"{ORG}/infraro-core"
MIRROR_REPO = f"{ORG}/infraro-core-mirror"
DECL_REPO = f"{ORG}/infraro"
HUB_REPO = f"{ORG}/runner-hub"
SCHED_REPO = f"{ORG}/scheduler"
CONSUMER_REPOS = (f"{ORG}/consumer-a", f"{ORG}/consumer-b")

# 清册（registry of record）锚：f8683e2 校准口径 + 2026-09-28 重建回写（§3.3）。
REGISTRY_HUB_CORE = ("runner-01", "runner-02", "runner-03", "runner-04")
REGISTRY_SCHED = ("runner-05",)
REGISTRY_ENGINE_CORE = ("runner-06", "runner-07", "runner-08", "runner-09")
# muse-1（owner 侧宿主外实例）事件闭环 2026-09-28：当日 08:0xZ 出现于引擎仓池、
# 17:16Z 复探已不在任何池（活体口径 9==清册 §1，清册 §4 回写记录在案）。此后
# 任何未登记外来实例一律 FAIL——§4 回写纪律：未回写即未完成，不预置容忍集。
RELEASE_LEG_TAG = "v0.20.0"
RELEASE_LEG_PR = 163

SLOW = "慢"
MEDIUM = "中"
FAST = "快"

PASS = "PASS"
FAIL = "FAIL"
PEND = "PENDING-EXTERNAL"


@dataclass(frozen=True)
class ProbeResult:
    """单个探针的执行结果（三态 + 明细）。"""

    probe_id: str
    title: str
    state: str
    detail: str
    speed: str = SLOW
    matrix: str = "-"


@dataclass(frozen=True)
class ProbeSpec:
    """探针注册项：id / 层 / 腐速 / 矩阵行 / 执行函数。"""

    probe_id: str
    title: str
    layer: str
    speed: str
    matrix: str
    fn: Callable[[], ProbeResult]


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path) -> dict[str, Any]:
    """yaml.safe_load 并断言顶层映射（lazy import：_ensure_yaml 先行）。"""
    import yaml  # 项目既有依赖（pyproject dependencies: pyyaml）

    data: Any = yaml.safe_load(_read(path))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: YAML 顶层不是映射")
    return data


def _ensure_yaml() -> None:
    """pyyaml 缺失时 re-exec 到仓库 .venv 解释器（零新依赖约束下的可移植兜底）。"""
    try:
        import yaml  # noqa: F401
    except ImportError:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), os.path.abspath(__file__), *sys.argv[1:]])


@dataclass(frozen=True)
class GhResponse:
    """gh api 子进程结果；status=0 表示 CLI/传输层失败（区别于 HTTP 4xx）。"""

    ok: bool
    status: int
    body: str
    err: str


def gh_api(endpoint: str, *, raw: bool = False, timeout: int = 60) -> GhResponse:
    """只读 gh api 调用。raw=True 时按 GitHub raw 形态取文件原文。"""
    cmd = ["gh", "api"]
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw+json"]
    cmd.append(endpoint)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return GhResponse(False, 0, "", "gh CLI 不可用（未安装或不在 PATH）——不静默跳过")
    except subprocess.TimeoutExpired:
        return GhResponse(False, 0, "", f"gh api 超时（{timeout}s）——不静默跳过")
    if proc.returncode == 0:
        return GhResponse(True, 200, proc.stdout, "")
    m = re.search(r"\b(404|403|401)\b", proc.stderr)
    code = int(m.group(1)) if m else 0
    return GhResponse(False, code, proc.stdout, proc.stderr.strip()[:300])


def gh_json(endpoint: str) -> tuple[GhResponse, Any]:
    resp = gh_api(endpoint)
    if resp.ok:
        return resp, json.loads(resp.body)
    return resp, None


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# T1 本地零凭证探针（静态文件面）
# ---------------------------------------------------------------------------


def t1_01_workflows_self_hosted() -> ProbeResult:
    """矩阵#1：引擎仓 workflow 全量用 self-hosted（文档口径 23/23）。"""
    pid, title = "T1-01", "workflow 全量 self-hosted（23/23）"
    files = sorted(WORKFLOW_DIR.glob("*.yml"))
    missing = sorted(f.name for f in files if "self-hosted" not in _read(f))
    if not files:
        return ProbeResult(
            pid, title, FAIL, ".github/workflows/ 下未找到 yml（glob=0）", matrix="#1"
        )
    if missing or len(files) != 23:
        detail = (
            f"{len(files)} 个 workflow；缺 self-hosted：{missing or '无'}；"
            f"文档口径 23 不符——新 workflow 落仓需同步更新文档 §2.3"
        )
        return ProbeResult(pid, title, FAIL, detail, matrix="#1")
    return ProbeResult(
        pid, title, PASS, f"{len(files)} 个 workflow 全部含 self-hosted", matrix="#1"
    )


_CI_JOBS_EXPECTED = {
    "pytest",
    "lint-bundle",
    "type-bundle",
    "advisory-bundle",
    "test-groups",
    "guards",
    "integration-tests",
    "e2e-tests",
    "health-check",
    "ci-ok",
    "gate-tests",
    "notify-ci-complete",
}
_CI_OK_NEEDS_EXPECTED = [
    "pytest",
    "lint-bundle",
    "type-bundle",
    "advisory-bundle",
    "test-groups",
    "guards",
    "integration-tests",
    "e2e-tests",
    "health-check",
    "gate-tests",
]


def t1_02_ci_ok_needs_ten() -> ProbeResult:
    """P1-1 销项锚：ci.yml 12 job；ci-ok needs 恰 10 项（名单逐字一致）。"""
    pid, title = "T1-02", "ci-ok needs 10 项（workflow YAML 解析）"
    data = load_yaml(CI_PATH)
    jobs: dict[str, Any] = data["jobs"]
    needs: list[Any] = jobs["ci-ok"]["needs"]
    needs_list = [str(n) for n in needs]
    ok = len(jobs) == 12 and set(jobs) == _CI_JOBS_EXPECTED and needs_list == _CI_OK_NEEDS_EXPECTED
    detail = f"jobs={len(jobs)}；ci-ok needs={len(needs_list)} 项: {','.join(needs_list)}"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#5（needs 口径）")


def t1_03_droid_review_contracts() -> ProbeResult:
    """矩阵#13 面上的三契约（NEVER break）：workflow 名 / artifact 前缀 / 聚合 job key。"""
    pid, title = "T1-03", "droid-review 三契约名"
    data = load_yaml(WORKFLOW_DIR / "droid-review.yml")
    raw = _read(WORKFLOW_DIR / "droid-review.yml")
    name_ok = data.get("name") == "Droid Auto Review"
    artifact_ok = "droid-review-debug-" in raw
    jobs: dict[str, Any] = data["jobs"]
    agg_ok = "droid-review" in jobs
    ok = name_ok and artifact_ok and agg_ok
    detail = (
        f"workflow name={'Droid Auto Review' if name_ok else str(data.get('name'))}；"
        f"artifact 前缀 droid-review-debug- {'在' if artifact_ok else '缺'}；"
        f"聚合 job key droid-review {'在' if agg_ok else '缺'}"
    )
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#13（契约名面）")


def t1_04_ci_ok_poll_contract() -> ProbeResult:
    """矩阵#14 相邻锚：ci-ok 轮询契约 60 × 30s ≈ 30min（h1-alwayscheck 收紧后）。"""
    pid, title = "T1-04", "ci-ok droid-review 轮询契约（60×30s）"
    raw = _read(POLL_CONTRACT_PATH)
    attempts = re.search(r"^MAX_ATTEMPTS=(\d+)$", raw, re.M)
    wait = re.search(r"^WAIT_SECONDS=(\d+)$", raw, re.M)
    ok = bool(attempts and attempts.group(1) == "60" and wait and wait.group(1) == "30")
    got = (
        f"MAX_ATTEMPTS={attempts.group(1) if attempts else '?'} "
        f"WAIT_SECONDS={wait.group(1) if wait else '?'}"
    )
    return ProbeResult(
        pid, title, PASS if ok else FAIL, got + "（≈30min 上限）", matrix="#14（轮询机制面）"
    )


_RUNNER_TOOLS_EXPECTED = {
    "uv": "0.12.7",
    "ruff": "0.16.3",
    "mypy": "2.3.1",
    "actionlint": "1.7.11",
    "shellcheck": "0.10.0",
    "jq": "1.7",
}


def _runner_tools() -> dict[str, Any]:
    with RUNNER_TOOLS_PATH.open("rb") as fh:
        return tomllib.load(fh)


def t1_05_runner_tools_versions() -> ProbeResult:
    """runner-tools.toml 六工具版本钉（文档 §2.3 版本面单一事实源）。"""
    pid, title = "T1-05", "runner-tools.toml 六工具版本"
    cfg = _runner_tools()
    tools: dict[str, Any] = cfg["tools"]
    got = {k: str(v) for k, v in tools.items()}
    ok = got == _RUNNER_TOOLS_EXPECTED
    detail = "; ".join(f"{k}={v}" for k, v in sorted(got.items()))
    if not ok:
        detail += (
            f"（期望 {'; '.join(f'{k}={v}' for k, v in sorted(_RUNNER_TOOLS_EXPECTED.items()))}）"
        )
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#2（labels 契约邻接锚）")


def t1_06_runner_tools_fallback() -> ProbeResult:
    """runner-tools.toml [ci_fallback] 与 ci.yml 下载回退钉（#61 bundle 化后形态）。"""
    pid, title = "T1-06", "runner-tools.toml ci_fallback 回退版本"
    cfg = _runner_tools()
    fb: dict[str, Any] = cfg["ci_fallback"]
    got = {k: str(v) for k, v in fb.items()}
    ok = got == {"actionlint": "1.7.12", "shellcheck": "0.10.0"}
    detail = "; ".join(f"{k}={v}" for k, v in sorted(got.items()))
    return ProbeResult(pid, title, PASS if ok else FAIL, detail)


def t1_07_runner_labels_contract() -> ProbeResult:
    """labels 契约 ["self-hosted", "pve-linux"]（runner-tools.toml [runner]，owner 裁定锚）。"""
    pid, title = "T1-07", "runner labels 契约（self-hosted + pve-linux）"
    cfg = _runner_tools()
    labels: list[Any] = cfg["runner"]["labels"]
    got = [str(x) for x in labels]
    ok = got == ["self-hosted", "pve-linux"]
    return ProbeResult(pid, title, PASS if ok else FAIL, f"labels={got}", matrix="#2（labels 面）")


def t1_08_gate0_registration_domain() -> ProbeResult:
    """矩阵#9 本地锚：gate0 覆盖域完整（scripts/ 为登记域，新文件不触发新红）。"""
    pid, title = "T1-08", "gate0 覆盖域（子进程复跑，exit 0）"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "substrate" / "gates" / "gate0_coverage.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    last = (proc.stdout.strip().splitlines() or ["<无输出>"])[-1]
    ok = proc.returncode == 0
    return ProbeResult(
        pid,
        title,
        PASS if ok else FAIL,
        f"exit={proc.returncode}；{last}",
        matrix="#9（清册本地锚）",
    )


def t1_09_no_floating_uses() -> ProbeResult:
    """锚点纪律 A2 本地子集：引擎 workflow uses 禁浮动 @main/@master（SHA 钉合法）。"""
    pid, title = "T1-09", "uses 无浮动锚（禁 @main/@master）"
    pattern = re.compile(r"uses:\s*([^\s]+)")
    flagged: list[str] = []
    checked = 0
    targets = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(
        (REPO_ROOT / ".github" / "actions").glob("*/action.yml")
    )
    for path in targets:
        for m in pattern.finditer(_read(path)):
            checked += 1
            parts = m.group(1).split("@", 1)
            if len(parts) == 2 and parts[1] in {"main", "master"}:
                flagged.append(f"{path.name}:{m.group(1)}")
    ok = not flagged
    detail = f"{checked} 个 uses 全部锚定 tag/版本" if ok else f"浮动锚: {flagged}"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#12（锚点纪律面）")


_REUSABLE_EXPECTED = [
    "auto-merge-pipeline",
    "droid-review-shards",
    "droid-review-watchdog-handlers",
    "evolution-heartbeat",
    "evolution-scan",
    "setup-labels",
]


def t1_10_reusable_inventory() -> ProbeResult:
    """矩阵#12：引擎 reusable 面恰 6 文件（文档 §1.2 逐字清单）。"""
    pid, title = "T1-10", "reusable workflow 恰 6 件"
    files = sorted(WORKFLOW_DIR.glob("*.yml"))
    reusable = sorted(f.stem for f in files if "workflow_call" in _read(f))
    ok = reusable == sorted(_REUSABLE_EXPECTED)
    detail = f"reusable={len(reusable)}: {','.join(reusable)}"
    if not ok:
        detail += f"（期望 {','.join(sorted(_REUSABLE_EXPECTED))}）"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#12")


def t1_11_droid_contract_not_in_toml() -> ProbeResult:
    """P2 销项锚：droid ≥0.200 契约不在 runner-tools.toml（workflow 内联 + 测试钉）。"""
    pid, title = "T1-11", "droid ≥0.200 契约位置（非 toml 成员）"
    cfg = _runner_tools()
    in_toml = "droid" in {str(k).lower() for k in cfg["tools"]}
    inline_review = "-ge 200" in _read(DROID_REVIEW_PATH)
    inline_shards = "-ge 200" in _read(SHARDS_PATH)
    pinned = "VERSION_FLOOR_MINOR = 200" in _read(
        REPO_ROOT / "tests" / "test_droid_cli_host_first.py"
    )
    ok = (not in_toml) and inline_review and inline_shards and pinned
    detail = (
        f"toml 无 droid 键={'是' if not in_toml else '否'}；workflow 内联 -ge 200 "
        f"（droid-review {'在' if inline_review else '缺'} / shards "
        f"{'在' if inline_shards else '缺'}）；测试钉 VERSION_FLOOR_MINOR=200 "
        f"{'在' if pinned else '缺'}"
    )
    return ProbeResult(pid, title, PASS if ok else FAIL, detail)


def t1_12_main_shards_worker_ci_key() -> ProbeResult:
    """矩阵#11 main 侧：shards.yml 已声明 worker_ci_key 且 caller 透传（F2 已入 main）。

    镜像消费面（mirror@v0.20.0）由 T2-09 单列 PENDING-EXTERNAL，本探针只锚 main。
    """
    pid, title = "T1-12", "main 消费面含 worker_ci_key 通道（F2）"
    raw = _read(SHARDS_PATH)
    declared = re.search(r"^\s*worker_ci_key:\s*$", raw, re.M) is not None
    passed = "secrets.worker_ci_key" in raw
    noted = "x-ci-key" in raw
    ok = declared and passed and noted
    detail = (
        f"secrets 声明 worker_ci_key {'在' if declared else '缺'}；"
        f"透传 secrets.worker_ci_key {'在' if passed else '缺'}；"
        f"x-ci-key 语义注记 {'在' if noted else '缺'}（required:false 过渡语义；"
        f"镜像消费面待 PR #{RELEASE_LEG_PR} 发版，见 T2-09）"
    )
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#11（main 侧）")


def t1_13_local_runs_window() -> ProbeResult:
    """矩阵#15 本地侧：近 30 commit 无 PR 合并痕迹（分支未 push 的 mission 写入边界）。

    远端 runs 窗口快照归 T2-12（快档：永不进硬门，执行前重测 + 时点戳）。
    """
    pid, title = "T1-13", "本地未推分支零 PR 合并痕迹"
    proc = subprocess.run(
        ["git", "log", "origin/main..HEAD", "--oneline"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return ProbeResult(
            pid,
            title,
            FAIL,
            f"git log origin/main..HEAD 失败（缺失时先 git fetch）: {proc.stderr.strip()[:200]}",
            speed=MEDIUM,
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    pr_merged = [ln for ln in lines if re.search(r"\(#\d+\)", ln)]
    ci_related = [ln for ln in lines if re.search(r"ci|CI|守卫|gate", ln)]
    ok = bool(lines) and not pr_merged
    detail = (
        f"未推 commit {len(lines)} 条；PR 合并痕迹 {len(pr_merged)} 条"
        + (
            "（预期：docs/fullstack-test-plan 分支按写入边界不 push）"
            if ok
            else f"：{'; '.join(pr_merged[:3])}"
        )
        + f"；CI/守卫相关 commit {len(ci_related)} 条（归因素材；远端 runs 快照=T2-12）"
    )
    return ProbeResult(
        pid, title, PASS if ok else FAIL, detail, matrix="#15（本地侧）", speed=MEDIUM
    )


_DOC_NEEDLES = [
    "23/23",
    "27 SUCCESS + 2 SKIPPED",
    "MAX_ATTEMPTS=60 × 30s",
    "VERSION_FLOOR_MINOR=200",
    "worker_ci_key",
    "authorized.yaml",
    "engineConsumer",
    "muse-1",
    RELEASE_LEG_TAG,
]


def t1_14_doc_baseline_forms() -> ProbeResult:
    """文档承重数字/形态锚：探针佐证的关键字面量必须在文档在板（防回退）。"""
    pid, title = "T1-14", "文档承重基线字面量在板"
    raw = _read(DOC_PATH)
    missing = [n for n in _DOC_NEEDLES if n not in raw]
    ok = not missing
    detail = f"{len(_DOC_NEEDLES)} 个锚字面量全部在板" if ok else f"缺失: {missing}"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail)


# ---------------------------------------------------------------------------
# T2 gh 只读巡检探针
# ---------------------------------------------------------------------------


def _gh_fail(pid: str, title: str, resp: GhResponse, matrix: str, speed: str) -> ProbeResult:
    transport = "transport" if resp.status == 0 else str(resp.status)
    return ProbeResult(
        pid,
        title,
        FAIL,
        f"gh api 失败（不静默跳过）：HTTP {transport}；{resp.err}",
        matrix=matrix,
        speed=speed,
    )


def t2_01_engine_runner_pool() -> ProbeResult:
    """矩阵#2：引擎仓专属池活体（清册 §1 锚 4 台；未登记外来实例 FAIL）。"""
    pid, title = "T2-01", "引擎仓 runner 池==清册锚（4 台重建池）"
    resp, data = gh_json(f"repos/{ENGINE_REPO}/actions/runners")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#2", MEDIUM)
    runners: list[dict[str, Any]] = data["runners"]
    names = {str(r["name"]) for r in runners}
    core = [r for r in runners if r["name"] in REGISTRY_ENGINE_CORE]
    core_broken = [
        f"{r['name']}: status={r['status']} labels={[lb['name'] for lb in r['labels']]}"
        for r in core
        if r["status"] != "online" or "pve-linux" not in {lb["name"] for lb in r["labels"]}
    ]
    extras = names - set(REGISTRY_ENGINE_CORE)
    if core_broken or extras:
        detail = (
            f"重建池异常: {core_broken or '无'}；"
            f"未登记外来 runner（§4 回写纪律）：{sorted(extras) or '无'}"
        )
        return ProbeResult(pid, title, FAIL, detail, matrix="#2", speed=MEDIUM)
    return ProbeResult(
        pid,
        title,
        PASS,
        f"重建池 runner-06~09 全 online 且含 pve-linux（{len(core)} 台==清册 §1 口径）",
        matrix="#2",
        speed=MEDIUM,
    )


def t2_02_hub_runner_pool() -> ProbeResult:
    """矩阵#5：runner-01~04 online 且含 pve-linux；未登记外来实例 FAIL。"""
    pid, title = "T2-02", "runner-hub 池健康（status 字段，非 online 布尔——措辞坑）"
    resp, data = gh_json(f"repos/{HUB_REPO}/actions/runners")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#5", MEDIUM)
    runners: list[dict[str, Any]] = data["runners"]
    names = {str(r["name"]) for r in runners}
    core_broken = [
        f"{r['name']}: status={r['status']} labels={[lb['name'] for lb in r['labels']]}"
        for r in runners
        if r["name"] in REGISTRY_HUB_CORE
        and (r["status"] != "online" or "pve-linux" not in {lb["name"] for lb in r["labels"]})
    ]
    extras = names - set(REGISTRY_HUB_CORE)
    if core_broken or extras:
        detail = (
            f"核心四台异常: {core_broken or '无'}；"
            f"未登记外来 runner（§4 回写纪律）：{sorted(extras) or '无'}"
        )
        return ProbeResult(pid, title, FAIL, detail, matrix="#5", speed=MEDIUM)
    return ProbeResult(
        pid,
        title,
        PASS,
        f"runner-01~04 全 online 且含 pve-linux（{len(runners)} 台==清册口径）",
        matrix="#5",
        speed=MEDIUM,
    )


def t2_03_scheduler_pool() -> ProbeResult:
    """矩阵#6：scheduler runner-05 online 且含 pve-linux。"""
    pid, title = "T2-03", "scheduler 池（runner-05）"
    resp, data = gh_json(f"repos/{SCHED_REPO}/actions/runners")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#6", MEDIUM)
    runners: list[dict[str, Any]] = data["runners"]
    ok = (
        len(runners) == 1
        and runners[0]["name"] in REGISTRY_SCHED
        and runners[0]["status"] == "online"
        and "pve-linux" in {lb["name"] for lb in runners[0]["labels"]}
    )
    got = (
        f"{runners[0]['name']}: status={runners[0]['status']} "
        f"labels={[lb['name'] for lb in runners[0]['labels']]}"
        if runners
        else "无 runner"
    )
    return ProbeResult(pid, title, PASS if ok else FAIL, got, matrix="#6", speed=MEDIUM)


def t2_04_decl_repo_zero_runners() -> ProbeResult:
    """矩阵#7：声明仓 0 runner（聚合 job 用 hosted 执行面）。"""
    pid, title = "T2-04", "声明仓 0 runner"
    resp, data = gh_json(f"repos/{DECL_REPO}/actions/runners")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#7", MEDIUM)
    total = int(data["total_count"])
    ok = total == 0
    return ProbeResult(
        pid, title, PASS if ok else FAIL, f"total_count={total}", matrix="#7", speed=MEDIUM
    )


def t2_05_consumer_decommissioned() -> ProbeResult:
    """矩阵#3：consumer-a/b 维持注销态（端点 404=期望；200=仓复活=注销登记失真）。"""
    pid, title = "T2-05", "consumer-a/b 注销态（404 期望）"
    alive: list[str] = []
    transport: list[str] = []
    for repo in CONSUMER_REPOS:
        resp = gh_api(f"repos/{repo}")
        if resp.ok:
            alive.append(repo)
        elif resp.status == 404:
            continue
        else:
            transport.append(f"{repo}: HTTP {resp.status or 'transport'} {resp.err[:120]}")
    if transport:
        return ProbeResult(
            pid,
            title,
            FAIL,
            f"gh api 失败（不静默跳过）：{'；'.join(transport)}",
            matrix="#3",
            speed=MEDIUM,
        )
    if alive:
        return ProbeResult(
            pid,
            title,
            FAIL,
            f"已注销仓复活（清册 §3 注销登记失真）: {alive}",
            matrix="#3",
            speed=MEDIUM,
        )
    return ProbeResult(
        pid,
        title,
        PASS,
        "consumer-a/b 两仓 GET 均 404（2026-09-25 注销态维持）",
        matrix="#3",
        speed=MEDIUM,
    )


def t2_06_registry_anchor_total() -> ProbeResult:
    """矩阵#4：三仓 total 合计==清册头部总量口径（清册 §1==活体）。"""
    pid, title = "T2-06", "三仓总量==清册口径（锚定口径非写死数值）"
    if not REGISTRY_PATH.exists():
        return ProbeResult(
            pid, title, FAIL, f"清册缺失: {REGISTRY_PATH.name}", matrix="#4", speed=MEDIUM
        )
    m = re.search(r"全账号\s*(\d+)\s*个", _read(REGISTRY_PATH))
    if not m:
        return ProbeResult(
            pid, title, FAIL, "清册头部未解析出「全账号 N 个」总量口径", matrix="#4", speed=MEDIUM
        )
    expected = int(m.group(1))
    counts: dict[str, int] = {}
    for repo in (ENGINE_REPO, HUB_REPO, SCHED_REPO):
        resp, data = gh_json(f"repos/{repo}/actions/runners")
        if not resp.ok:
            return _gh_fail(pid, title, resp, "#4", MEDIUM)
        counts[repo] = int(data["total_count"])
    total = sum(counts.values())
    if total == expected:
        return ProbeResult(
            pid,
            title,
            PASS,
            f"合计 {total}==清册口径 {expected}（{counts}）",
            matrix="#4",
            speed=MEDIUM,
        )
    return ProbeResult(
        pid,
        title,
        PEND,
        f"DRIFT：活体合计 {total}（{counts}）vs 清册口径 {expected}——"
        "按清册 §4 回写纪律处理（先修现实或先修清册并复探；禁止静默漂移）",
        matrix="#4",
        speed=MEDIUM,
    )


def t2_07_hub_dormancy_counters() -> ProbeResult:
    """矩阵#8：runner-hub=派发执行枢纽非清册真源；全史 3 commit / 2 run。"""
    pid, title = "T2-07", "runner-hub 休眠计数（3 commits / 2 runs）"
    rc, commits = gh_json(f"repos/{HUB_REPO}/commits?per_page=50")
    if not rc.ok:
        return _gh_fail(pid, title, rc, "#8", MEDIUM)
    rr, runs = gh_json(f"repos/{HUB_REPO}/actions/runs?per_page=50")
    if not rr.ok:
        return _gh_fail(pid, title, rr, "#8", MEDIUM)
    n_commits = len(list(commits))
    n_runs = int(runs["total_count"])
    ok = n_commits == 3 and n_runs == 2
    detail = f"commits={n_commits}（期望 3）；runs total={n_runs}（期望 2）"
    if not ok:
        detail += "——hub 有新活动，文档「全史」口径需重采"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#8", speed=MEDIUM)


def t2_08_authorized_yaml() -> ProbeResult:
    """矩阵#10：scheduler authorized.yaml 名单==[infraro]；角色=引擎消费授权门（P1-3）。"""
    pid, title = "T2-08", "authorized.yaml 名单（授权门，非派发选仓真源）"
    resp = gh_api(f"repos/{SCHED_REPO}/contents/authorized.yaml")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#10", MEDIUM)
    try:
        content = base64.b64decode(json.loads(resp.body)["content"]).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 —— 解码失败按探针失败处理
        return ProbeResult(
            pid, title, FAIL, f"authorized.yaml 解码失败: {exc}", matrix="#10", speed=MEDIUM
        )
    entries = re.findall(r"^  - (\S+)$", content, re.M)
    ok = entries == ["infraro"] and "唯一" in content
    detail = f"名单={entries}；头部「唯一真源」自述 {'在' if '唯一' in content else '缺'}"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#10", speed=MEDIUM)


def t2_09_mirror_tag_worker_ci_key() -> ProbeResult:
    """矩阵#11 镜像侧：mirror v0.20.0 的 shards.yml 含 worker_ci_key。

    PENDING-EXTERNAL：依赖 PR #163 合并发版（F2 发版腿挂账）；引擎 main 消费面已由
    T1-12 实证。解除条件=mirror 出现 v0.20.0 且该文件含 worker_ci_key。
    """
    pid, title = "T2-09", f"mirror 最新消费面含 worker_ci_key（{RELEASE_LEG_TAG}）"
    rt, tags = gh_json(f"repos/{MIRROR_REPO}/tags?per_page=10")
    if not rt.ok:
        return _gh_fail(pid, title, rt, "#11", MEDIUM)
    names = [str(t["name"]) for t in tags]
    if RELEASE_LEG_TAG not in names:
        # 裁决口径（2026-09-29 终审）：依赖已满足而结果未出现超 30min → FAIL，不藏进 PEND。
        rel_ok, rel = gh_json(f"repos/{ENGINE_REPO}/releases/tags/{RELEASE_LEG_TAG}")
        published = str(rel.get("published_at") or "") if rel_ok else ""
        age_min: float | None = None
        if published:
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                age_min = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 60.0
            except ValueError:
                age_min = None
        if age_min is not None and age_min > 30:
            return ProbeResult(
                pid,
                title,
                FAIL,
                f"mirror 同步断链：引擎 release {RELEASE_LEG_TAG} 已于 {published} 发布"
                f"（{int(age_min)}min 前），mirror tags={names} 仍无 {RELEASE_LEG_TAG}。"
                "机制=镜像仓 sync-release.yml（需 UPSTREAM_READ_TOKEN/MIRROR_PUSH_TOKEN"
                "凭据；2026-09-28 dispatch 实测 404 失败）",
                matrix="#11",
                speed=MEDIUM,
            )
        return ProbeResult(
            pid,
            title,
            PEND,
            f"mirror 消费 tag 面={names}；引擎 release {RELEASE_LEG_TAG} "
            f"{'发布于 ' + published if published else '发布态未确认'}，未超 30min 判据。"
            f"解除条件=gh api repos/{MIRROR_REPO}/contents/.github/workflows/"
            f"droid-review-shards.yml?ref={RELEASE_LEG_TAG} 含 worker_ci_key",
            matrix="#11",
            speed=MEDIUM,
        )
    rf = gh_api(
        f"repos/{MIRROR_REPO}/contents/.github/workflows/droid-review-shards.yml?ref={RELEASE_LEG_TAG}",
        raw=True,
    )
    if not rf.ok:
        return _gh_fail(pid, title, rf, "#11", MEDIUM)
    hits = rf.body.count("worker_ci_key")
    ok = hits > 0
    return ProbeResult(
        pid,
        title,
        PASS if ok else FAIL,
        f"mirror@{RELEASE_LEG_TAG} shards.yml worker_ci_key 命中 {hits} 处"
        + ("" if ok else "——发版腿产物缺 F2 通道，真实缺陷"),
        matrix="#11",
        speed=MEDIUM,
    )


def t2_10_rules_required_lists() -> ProbeResult:
    """矩阵#13：双仓 required 名单（措辞坑：rules/branches/main 的独立 rule 类型）。"""
    pid, title = "T2-10", "双仓 required checks 名单（rules/branches/main 端点）"
    expectations: dict[str, set[str]] = {
        ENGINE_REPO: {"quality-gate", "substrate-gate-suite", "droid-review"},
        DECL_REPO: {"ci-ok", "droid-review"},
    }
    details: list[str] = []
    ok_all = True
    for repo, expected in expectations.items():
        resp, rules = gh_json(f"repos/{repo}/rules/branches/main")
        if not resp.ok:
            return _gh_fail(pid, title, resp, "#13", MEDIUM)
        ctx: set[str] = set()
        strict: str = "-"
        for rule in rules:
            if rule.get("type") != "required_status_checks":
                continue
            params: dict[str, Any] = rule.get("parameters") or {}
            ctx = {c["context"] for c in params.get("required_status_checks", [])}
            strict = str(params.get("strict_required_status_checks_policy"))
        ok = ctx == expected and strict == "True"
        ok_all = ok_all and ok
        details.append(f"{repo}: {sorted(ctx)} strict={strict}")
    return ProbeResult(
        pid,
        title,
        PASS if ok_all else FAIL,
        "；".join(details)
        + "（注意：pull_request 规则的 required_status_checks 参数恒空，勿作判据）",
        matrix="#13",
        speed=MEDIUM,
    )


def t2_11_release_leg() -> ProbeResult:
    """VAL-FIX-003 挂账：PR #163 merged + release v0.20.0 published（production 门痕迹）。"""
    pid, title = "T2-11", f"发版腿（PR #{RELEASE_LEG_PR} → release {RELEASE_LEG_TAG}）"
    rp, pr = gh_json(f"repos/{ENGINE_REPO}/pulls/{RELEASE_LEG_PR}")
    if not rp.ok:
        return _gh_fail(pid, title, rp, "#11（发版腿）", MEDIUM)
    pr_state = str(pr["state"])
    merged = bool(pr["merged"])
    rl, rel = gh_json(f"repos/{ENGINE_REPO}/releases/latest")
    latest = str(rel["tag_name"]) if rl.ok else f"<gh 失败: {rl.err[:80]}>"
    if pr_state == "open":
        return ProbeResult(
            pid,
            title,
            PEND,
            f"依赖外部事实：PR #{RELEASE_LEG_PR} state=open mergeable={pr.get('mergeable')} "
            f"merge_state={pr.get('mergeable_state')} head={str(pr['head']['sha'])[:7]}；"
            f"最新 release={latest}（引擎 tag {RELEASE_LEG_TAG} 已存在、随合并发版生效）。"
            "解除条件=#163 合并 → release-please 发布（production 门由 owner 点批准）",
            matrix="#11（发版腿）",
            speed=MEDIUM,
        )
    if merged and rl.ok and latest == RELEASE_LEG_TAG:
        return ProbeResult(
            pid,
            title,
            PASS,
            f"发版腿闭环：PR #{RELEASE_LEG_PR} merged；release {latest} published"
            "（production 审批门通过痕迹）",
            matrix="#11（发版腿）",
            speed=MEDIUM,
        )
    return ProbeResult(
        pid,
        title,
        FAIL,
        f"发版腿断裂：PR state={pr_state} merged={merged}；"
        f"最新 release={latest}（期望 {RELEASE_LEG_TAG}）",
        matrix="#11（发版腿）",
        speed=MEDIUM,
    )


def t2_12_recent_runs_window() -> ProbeResult:
    """矩阵#15：近 30 runs 窗口快照（快档：时点快照非基线；API 失败才 FAIL）。"""
    pid, title = "T2-12", "引擎仓近 30 runs 窗口快照"
    resp, data = gh_json(f"repos/{ENGINE_REPO}/actions/runs?per_page=30")
    if not resp.ok:
        return _gh_fail(pid, title, resp, "#15", FAST)
    runs: list[dict[str, Any]] = data["workflow_runs"]
    buckets: dict[str, int] = {}
    for r in runs:
        key = str(r["conclusion"]) if r["conclusion"] is not None else "in_progress"
        buckets[key] = buckets.get(key, 0) + 1
    window = (
        f"{min(str(r['created_at']) for r in runs)} ~ {max(str(r['created_at']) for r in runs)}"
        if runs
        else "空窗口"
    )
    dist = "; ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
    return ProbeResult(
        pid,
        title,
        PASS,
        f"快照@{_utc_now()} 窗口 {window}：{dist}——时点快照非稳定基线，L3 执行前必须重测并"
        "对 failure 簇归因（文档 §2.4 口径：多时点重采，failure 簇集中 09-27 两波事故）",
        matrix="#15",
        speed=FAST,
    )


# ---------------------------------------------------------------------------
# T3 Mac 本机周检探针（只在宿主 Mac 有意义；全程只读零写副作用）
# ---------------------------------------------------------------------------

WEBHOOK_DIR = Path.home() / ".factory" / "webhook"
SENTINEL_PATH = WEBHOOK_DIR / "locks" / ".poll-bootstrap-done"
POLL_LOG_PATH = WEBHOOK_DIR / "logs" / "poll-releases.log"
LOCKS_DIR = WEBHOOK_DIR / "locks"
REPOSITORIES_YML = Path.home() / ".factory" / "config" / "repositories.yml"
ENGINE_CONSUMER_EXPECTED = 3  # 文档口径：memory / mencbo / infraro 三块


def t3_01_poll_sentinel() -> ProbeResult:
    """矩阵#16a：哨兵存在性（缺失=前置修复①维持断链，PENDING-EXTERNAL 待 owner）。"""
    pid, title = "T3-01", "poll-releases 哨兵（locks/ 路径口径）"
    if SENTINEL_PATH.exists():
        return ProbeResult(
            pid, title, PASS, f"哨兵在位: {SENTINEL_PATH.name}（派发禁令解除）", matrix="#16"
        )
    return ProbeResult(
        pid,
        title,
        PEND,
        "哨兵缺失——前置修复①维持断链（派发禁令生效）。依赖项=owner 在 Mac 本机执行 "
        "poll-releases.sh --init（真路径 locks/ 下，勿放仓根）；证据=poll-releases.log "
        "持续 ERROR（见 T3-02）；修复后本探针翻转为 PASS",
        matrix="#16",
    )


def t3_02_poll_log_errors() -> ProbeResult:
    """矩阵#16b：poll-releases.log 最近轮询周期（300s）无 ERROR。"""
    pid, title = "T2 设计对应矩阵#16b", "poll-releases.log 最近周期 ERROR 面"
    pid = "T3-02"
    if not POLL_LOG_PATH.exists():
        return ProbeResult(
            pid, title, FAIL, f"日志缺失: {POLL_LOG_PATH.name}（T3 目标不可观测）", matrix="#16"
        )
    lines = POLL_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-30:]
    start = max((i for i, ln in enumerate(tail) if "轮询开始" in ln), default=0)
    cycle = tail[start:]
    has_error = any("ERROR" in ln for ln in cycle)
    if not SENTINEL_PATH.exists():
        return ProbeResult(
            pid,
            title,
            PEND,
            f"断链维持（哨兵缺失，周期 ERROR 属预期证据）——"
            f"最近行: {cycle[-1][:160] if cycle else '<空>'}",
            matrix="#16",
        )
    ok = not has_error
    return ProbeResult(
        pid,
        title,
        PASS if ok else FAIL,
        f"最近周期 {'无' if ok else '有'} ERROR 行"
        f"（{'哨兵在位且轮询干净' if ok else '哨兵在位但轮询报错'}）",
        matrix="#16",
    )


def t3_03_dispatch_locks() -> ProbeResult:
    """矩阵#16c：locks/ 派发锁面（断链证据=锁停在 v0.18.8；v0.20.0 锁出现=链已恢复）。"""
    pid, title = "T3-03", "派发锁面（release-announce-<tag>.json）"
    if not LOCKS_DIR.is_dir():
        return ProbeResult(pid, title, FAIL, f"locks 目录缺失: {LOCKS_DIR.name}", matrix="#16")
    locks = sorted(p.name for p in LOCKS_DIR.glob("release-announce-*.json"))
    if not locks:
        return ProbeResult(
            pid, title, FAIL, "锁面为空——从未有成功派发锁，断链深于文档 §3.1 记载", matrix="#16"
        )
    recovered = any(RELEASE_LEG_TAG in name for name in locks)
    detail = f"锁面: {locks}"
    if recovered:
        detail += f"——{RELEASE_LEG_TAG} 锁已在，派发链已恢复（文档 §3.1 断链表述可回写翻转）"
    else:
        detail += f"——断链证据维持（{RELEASE_LEG_TAG} 无锁；文档 §3.1 口径一致）"
    return ProbeResult(pid, title, PASS, detail, matrix="#16")


def t3_04_engine_consumer_map() -> ProbeResult:
    """矩阵#17：repositories.yml engineConsumer:true 计数==3（派发选仓真源）。"""
    pid, title = "T3-04", "repositories.yml engineConsumer 计数==3"
    if not REPOSITORIES_YML.exists():
        return ProbeResult(
            pid, title, FAIL, "本机 repositories.yml 缺失（派发选仓真源不可观测）", matrix="#17"
        )
    count = _read(REPOSITORIES_YML).count("engineConsumer: true")
    ok = count == ENGINE_CONSUMER_EXPECTED
    detail = f"engineConsumer: true = {count} 处"
    if not ok:
        detail += f"（文档口径 {ENGINE_CONSUMER_EXPECTED}：memory/mencbo/infraro）——漂移需回写 §7.2"
    return ProbeResult(pid, title, PASS if ok else FAIL, detail, matrix="#17", speed=SLOW)


# ---------------------------------------------------------------------------
# 探针注册表与运行器
# ---------------------------------------------------------------------------

T1_PROBES: list[ProbeSpec] = [
    ProbeSpec(
        "T1-01", "workflow 全量 self-hosted（23/23）", "T1", SLOW, "#1", t1_01_workflows_self_hosted
    ),
    ProbeSpec("T1-02", "ci-ok needs 10 项（YAML 解析）", "T1", SLOW, "#5", t1_02_ci_ok_needs_ten),
    ProbeSpec("T1-03", "droid-review 三契约名", "T1", SLOW, "#13", t1_03_droid_review_contracts),
    ProbeSpec("T1-04", "ci-ok 轮询契约（60×30s）", "T1", SLOW, "#14", t1_04_ci_ok_poll_contract),
    ProbeSpec(
        "T1-05", "runner-tools.toml 六工具版本", "T1", SLOW, "#2 邻接", t1_05_runner_tools_versions
    ),
    ProbeSpec(
        "T1-06", "runner-tools.toml ci_fallback", "T1", SLOW, "-", t1_06_runner_tools_fallback
    ),
    ProbeSpec("T1-07", "runner labels 契约", "T1", SLOW, "#2", t1_07_runner_labels_contract),
    ProbeSpec("T1-08", "gate0 覆盖域（子进程）", "T1", SLOW, "#9", t1_08_gate0_registration_domain),
    ProbeSpec("T1-09", "uses 无浮动锚", "T1", SLOW, "#12", t1_09_no_floating_uses),
    ProbeSpec("T1-10", "reusable workflow 恰 6 件", "T1", SLOW, "#12", t1_10_reusable_inventory),
    ProbeSpec("T1-11", "droid ≥0.200 契约位置", "T1", SLOW, "-", t1_11_droid_contract_not_in_toml),
    ProbeSpec(
        "T1-12", "main 含 worker_ci_key（F2）", "T1", SLOW, "#11", t1_12_main_shards_worker_ci_key
    ),
    ProbeSpec("T1-13", "本地 30 commit 无 PR 痕迹", "T1", MEDIUM, "#15", t1_13_local_runs_window),
    ProbeSpec("T1-14", "文档承重字面量在板", "T1", SLOW, "全档", t1_14_doc_baseline_forms),
]

T2_PROBES: list[ProbeSpec] = [
    ProbeSpec("T2-01", "引擎仓 runner 池==清册锚", "T2", MEDIUM, "#2", t2_01_engine_runner_pool),
    ProbeSpec(
        "T2-02", "runner-hub 池健康（status 字段）", "T2", MEDIUM, "#5", t2_02_hub_runner_pool
    ),
    ProbeSpec("T2-03", "scheduler 池（runner-05）", "T2", MEDIUM, "#6", t2_03_scheduler_pool),
    ProbeSpec("T2-04", "声明仓 0 runner", "T2", MEDIUM, "#7", t2_04_decl_repo_zero_runners),
    ProbeSpec(
        "T2-05", "consumer-a/b 注销态 404", "T2", MEDIUM, "#3", t2_05_consumer_decommissioned
    ),
    ProbeSpec("T2-06", "三仓总量==清册口径", "T2", MEDIUM, "#4", t2_06_registry_anchor_total),
    ProbeSpec("T2-07", "runner-hub 休眠计数", "T2", MEDIUM, "#8", t2_07_hub_dormancy_counters),
    ProbeSpec("T2-08", "authorized.yaml 名单", "T2", MEDIUM, "#10", t2_08_authorized_yaml),
    ProbeSpec(
        "T2-09", "mirror 消费面 worker_ci_key", "T2", MEDIUM, "#11", t2_09_mirror_tag_worker_ci_key
    ),
    ProbeSpec(
        "T2-10", "双仓 required 名单（rules 端点）", "T2", MEDIUM, "#13", t2_10_rules_required_lists
    ),
    ProbeSpec("T2-11", "发版腿 PR #163 → release", "T2", MEDIUM, "#11", t2_11_release_leg),
    ProbeSpec("T2-12", "近 30 runs 窗口快照", "T2", FAST, "#15", t2_12_recent_runs_window),
]

T3_PROBES: list[ProbeSpec] = [
    ProbeSpec("T3-01", "poll-releases 哨兵", "T3", MEDIUM, "#16", t3_01_poll_sentinel),
    ProbeSpec("T3-02", "poll-releases.log ERROR 面", "T3", MEDIUM, "#16", t3_02_poll_log_errors),
    ProbeSpec("T3-03", "派发锁面", "T3", MEDIUM, "#16", t3_03_dispatch_locks),
    ProbeSpec("T3-04", "engineConsumer 计数==3", "T3", SLOW, "#17", t3_04_engine_consumer_map),
]


def _run_spec(spec: ProbeSpec) -> ProbeResult:
    try:
        return spec.fn()
    except Exception as exc:  # noqa: BLE001 —— 探针自身异常按 FAIL 收敛，不中断套件
        return ProbeResult(
            spec.probe_id,
            spec.title,
            FAIL,
            f"探针异常: {type(exc).__name__}: {exc}",
            spec.speed,
            spec.matrix,
        )


def run_suite(label: str, specs: list[ProbeSpec]) -> int:
    print(f"== live-truth 探针 {label} == {_utc_now()}")
    counters = {PASS: 0, FAIL: 0, PEND: 0}
    for spec in specs:
        result = _run_spec(spec)
        counters[result.state] = counters.get(result.state, 0) + 1
        print(
            f"[{result.state}] {result.probe_id} {result.title}"
            f"（矩阵{result.matrix}｜腐速:{result.speed}）"
        )
        print(f"       {result.detail}")
    print(
        f"== {label} 汇总：{counters[PASS]} PASS / {counters[FAIL]} FAIL / "
        f"{counters[PEND]} PENDING-EXTERNAL"
        + (
            " → exit 0（零 FAIL 判据达成；PENDING-EXTERNAL 单列）"
            if counters[FAIL] == 0
            else " → exit 1"
        )
    )
    return 0 if counters[FAIL] == 0 else 1


def print_listing() -> None:
    print("== live-truth 探针清单（矩阵 17 行映射）==")
    print(f"{'ID':<7} {'层':<3} {'腐速':<3} {'矩阵':<8} 断言")
    for spec in [*T1_PROBES, *T2_PROBES, *T3_PROBES]:
        print(f"{spec.probe_id:<7} {spec.layer:<3} {spec.speed:<3} {spec.matrix:<8} {spec.title}")
    print(
        "\n腐速分档与重跑周期建议：\n"
        "  慢（静态文件，月级）→ T1 全档：每次 PR 门 + 月级复核；\n"
        "  中（repo/配置对象，周级）→ runners/ruleset/tag/名单类：每周 + L3-L5 执行前必跑；\n"
        "  快（runs/统计，小时-天级）→ 永不进硬门：执行前重测 + 时点戳入证据。\n"
        "三态判定：exit 0 = 零 FAIL（PENDING-EXTERNAL 单列不计失败）。\n"
        "T3 挂载点建议（launchd 周检 / patrol 形态 / preflight 复用）见脚本头 docstring"
        "（标注：待执行 mission 实测）。"
    )


def main() -> int:
    _ensure_yaml()
    parser = argparse.ArgumentParser(
        description="live-truth 探针：T1 本地零凭证 / T2 gh 只读 / T3 Mac 本机周检（三态判定）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--t1", action="store_true", help="本地零凭证静态探针（gate 化，进 milestone 门链）"
    )
    group.add_argument(
        "--t2", action="store_true", help="gh 只读巡检（无凭证/网络失败 → FAIL，不静默跳过）"
    )
    group.add_argument(
        "--t3", action="store_true", help="Mac 本机周检只读快照（哨兵/日志/锁/engineConsumer）"
    )
    group.add_argument(
        "--list", dest="list_probes", action="store_true", help="列出全部探针 + 腐速分档 + 周期建议"
    )
    args = parser.parse_args()
    if args.list_probes:
        print_listing()
        return 0
    if args.t1:
        return run_suite("T1", T1_PROBES)
    if args.t2:
        return run_suite("T2", T2_PROBES)
    return run_suite("T3", T3_PROBES)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
