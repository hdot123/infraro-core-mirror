"""F3 有界退出 + 哨兵 + 心跳 契约测试（PLAN v2.1 §3-F3，VAL-CI-104/105）。

四个面：
1. run_shard.sh 文本契约：stdout 直写禁 tee（tee 下 $! 是 tee 的 PID）/ setsid
   进程组隔离（禁 kill -- -$$，非交互 bash 下会杀脚本自身）/ 哨兵常量与默认值
   （软超时 25min、turn 上限 60）/ 137 重试分支的哨兵守卫 / prompt 预算由
   SHARD_SOFT_TIMEOUT_SEC 渲染 / 哨兵-恢复强制失败优先序。
2. e2e（VAL-CI-104 Evidence）：
   - 吊死 fake-droid → soft_timeout 遗书（哨兵先行终止序列 + 137 守卫生效 + 心跳落盘）
   - turn 爆炸 → turn_limit 分类（判定先于 exit_137）
   - 正常收敛 → 零干预零回归
   - 哨兵-恢复 → findings 不采信仍按哨兵分类（强制失败优先）
   - kill 隔离 → run_shard.sh 自身退出码非 SIGTERM 系，分类/遗书正常落盘
3. 分类器哨兵指纹行为级测试在 tests/test_run_shard_error_classification.py。
4. artifact 白名单（VAL-CI-105）：shard-heartbeat.log / shard-terminated.reason 入
   droid-review-shards.yml Upload findings path（前缀契约 droid-review-debug- 不变）。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.shard_sandbox_helpers import init_two_commit_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine" / "droid_review"
ENGINE_RUN_SHARD = ENGINE_DIR / "run_shard.sh"
CI_RUN_SHARD = REPO_ROOT / "scripts" / "droid_review" / "run_shard.sh"
CI_PUBLISH_FINDINGS = REPO_ROOT / "scripts" / "droid_review" / "publish_findings.py"
WORKFLOW_SHARDS = REPO_ROOT / ".github" / "workflows" / "droid-review-shards.yml"
WORKFLOW_SHELL = REPO_ROOT / ".github" / "workflows" / "droid-review.yml"

RUN_ID = "888000"
SHARD_ID = "0"

STDOUT_FINDINGS = {
    "shard_id": 0,
    "findings": [
        {"severity": "P2", "file": "a.py", "line": 3, "message": "正常收敛发现"},
    ],
}

# e2e 时间面：软超时 3s + 采样 1s + 宽限 1s → 越界终止全程 < 10s
E2E_BOUNDS = {
    "SHARD_SOFT_TIMEOUT_SEC": "3",
    "SHARD_HEARTBEAT_INTERVAL_SEC": "1",
    "SHARD_KILL_GRACE_SEC": "1",
}

BOUNDED_FAKE_DROID_PY = r"""#!/usr/bin/env python3
import json, os, sys, time, uuid, signal

with open(os.environ["FAKE_DROID_CONFIG"]) as f:
    cfg = json.load(f)

args = sys.argv[1:]
if "--version" in args:
    print("droid 0.201.0")
    sys.exit(0)

n = 0
if os.path.exists(cfg["calls_file"]):
    n = int(open(cfg["calls_file"]).read().strip() or "0")
n += 1
with open(cfg["calls_file"], "w") as f:
    f.write(str(n))

attempt = cfg["attempt"]
mode = attempt["mode"]
cwd = "."
for i, a in enumerate(args):
    if a == "--cwd":
        cwd = args[i + 1]
        break

sdir = os.path.join(cfg["sessions_dir"], cwd.replace("/", "-"))
os.makedirs(sdir, exist_ok=True)
marker = "droid-review-shard-marker:%s-%s" % (cfg["run_id"], cfg["shard_id"])
user_line = json.dumps({"type": "message", "message": {"role": "user", "content": [
    {"type": "text", "text": "Prompt containing " + marker}]}})
assistant_line = json.dumps({"type": "message", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "turn"}]}})


def write_session_file(lines):
    path = os.path.join(sdir, str(uuid.uuid4()) + ".jsonl")
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")
    return path


def write_sentinel():
    if attempt.get("write_sentinel"):
        with open(cfg["sentinel_path"], "w") as f:
            f.write(attempt.get("sentinel_reason", "soft_timeout") + "\n")


if mode == "hang":
    # 完整会话（可恢复形态）后吊死：忽略 SIGTERM——只有 SIGKILL 能终结（exit 137）
    path = write_session_file([
        json.dumps({"type": "session_start", "id": str(uuid.uuid4()), "cwd": cwd}),
        user_line,
    ])
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(300)
    sys.exit(0)

if mode == "exit137":
    sys.exit(137)

if mode == "exit137_then_hang":
    # 第 1 次 exec 137 快死（完成期竞态形态），重试 attempt 吊死——
    # P1 发现1 e2e：重试路径必须被软超时终止（裸 wait = 打穿 job timeout）
    if n == 1:
        sys.exit(137)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(300)
    sys.exit(0)

if mode == "exit137_then_success":
    # 第 1 次 exec 137 空 stdout，重试收敛——P3 发现4/5 e2e：残留哨兵不得
    # 压制重试、残留 stderr 不得跨 attempt 累积
    if n == 1:
        sys.exit(137)
    write_sentinel()
    print(json.dumps(cfg["stdout_findings"]))
    sys.exit(0)

if mode == "turn_spam":
    # assistant 行快速膨胀（R1 探针：jsonl 实时逐事件落盘）→ 心跳 turn 计数超限
    lines = [
        json.dumps({"type": "session_start", "id": str(uuid.uuid4()), "cwd": cwd}),
        user_line,
    ]
    path = write_session_file(lines)
    with open(path, "a") as f:
        for i in range(attempt.get("spam_lines", 300)):
            f.write(assistant_line + "\n")
            f.flush()
            time.sleep(attempt.get("spam_interval", 0.01))
    sys.exit(0)

if mode == "success":
    write_sentinel()
    if attempt.get("write_session"):
        findings = json.dumps(cfg["stdout_findings"], indent=2)
        text = "Review result:\n```json\n" + findings + "\n```\n"
        write_session_file([
            json.dumps({"type": "session_start", "id": str(uuid.uuid4()), "cwd": cwd}),
            user_line,
            json.dumps({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "text", "text": text}]}}),
        ])
    print(json.dumps(cfg["stdout_findings"]))
    sys.exit(0)

sys.exit(1)
"""


@pytest.fixture
def bounded_sandbox(tmp_path: Path) -> Path:
    """最小 workspace：prompt + publish_findings 副本 + head-src git diff + fake droid。"""
    ws = tmp_path / "ws"
    (ws / ".github" / "review").mkdir(parents=True)
    (ws / ".github" / "review" / "shard-review-prompt.md").write_text(
        "You are reviewing a shard. Return only valid JSON.\n", encoding="utf-8"
    )
    scripts_dir = ws / "scripts" / "droid_review"
    scripts_dir.mkdir(parents=True)
    shutil.copy(CI_PUBLISH_FINDINGS, scripts_dir / "publish_findings.py")

    head = ws / "head-src"
    head.mkdir()
    git_cfg = tmp_path / "git-config"
    git_cfg.write_text("", encoding="utf-8")
    base_sha, head_sha = init_two_commit_repo(head, git_cfg)

    bin_dir = ws / "fakebin"
    bin_dir.mkdir()
    droid = bin_dir / "droid"
    droid.write_text(BOUNDED_FAKE_DROID_PY, encoding="utf-8")
    droid.chmod(0o755)
    # macOS provenance xattr：新落盘二进制首启带 1.5-4s 一次性 provenance 检查，
    # 会让 hang 场景的 SIG_IGN 安装晚于 SIGTERM（信号在 python 启动前到达 →
    # 默认处置退出 143 而非 SIGKILL 后 137），e2e 断言失真。剥离后行为确定。
    # Linux（CI runner）无此 xattr/无此属性，尝试删除必然 ENOENT——只在
    # macOS 且命令存在时剥离；其余平台静默跳过（Linux 首启无 provenance 检查）。
    if sys.platform == "darwin" and shutil.which("xattr"):
        subprocess.run(
            ["xattr", "-d", "com.apple.provenance", str(droid)],
            capture_output=True,
        )

    (ws / "hostlog.txt").write_text("MetaError: none\n", encoding="utf-8")
    (ws / "env-file").write_text(
        "\n".join(
            [
                f"GITHUB_WORKSPACE={ws}",
                "SHARD_ID=0",
                'SHARD_FILES=["a.py"]',
                f"BASE_REF={base_sha}",
                f"HEAD_REF={head_sha}",
                f"MERGE_BASE={base_sha}",
                f"RUN_ID={RUN_ID}",
                f"DROID_LOG_FILE={ws / 'hostlog.txt'}",
                f"FAKE_DROID_CONFIG={ws / 'fake-droid-config.json'}",
                f"FACTORY_SESSIONS_DIR={ws / 'factory-sessions'}",
            ]
        ),
        encoding="utf-8",
    )
    return ws


def _sandbox_env(sandbox: Path) -> dict:
    env = os.environ.copy()
    for line in (sandbox / "env-file").read_text().splitlines():
        k, _, v = line.partition("=")
        env[k] = v
    env.update(
        {
            "PATH": f"{sandbox / 'fakebin'}:{env['PATH']}",
            "GIT_CONFIG_GLOBAL": str(sandbox.parent / "git-config"),
            "GIT_CONFIG_SYSTEM": str(sandbox.parent / "git-config"),
            "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", ""),
            "GITHUB_STEP_SUMMARY": str(sandbox / "step-summary.md"),
        }
    )
    return env


def _run_bounded(sandbox: Path, attempt: dict, extra_env: dict | None = None):
    """以给定 attempt 配置跑一遍 run_shard.sh（CI wrapper → 引擎副本）。"""
    (sandbox / "factory-sessions").mkdir(exist_ok=True)
    cfg = {
        "attempt": attempt,
        "calls_file": str(sandbox / "fake-droid-calls"),
        "sessions_dir": str(sandbox / "factory-sessions"),
        "stdout_findings": STDOUT_FINDINGS,
        "run_id": RUN_ID,
        "shard_id": SHARD_ID,
        "sentinel_path": str(sandbox / "shard-terminated.reason"),
    }
    (sandbox / "fake-droid-config.json").write_text(json.dumps(cfg), encoding="utf-8")
    env = _sandbox_env(sandbox)
    env.update(E2E_BOUNDS)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(CI_RUN_SHARD)],
        cwd=str(sandbox),
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )


def _droid_calls(sandbox: Path) -> int:
    calls = sandbox / "fake-droid-calls"
    return int(calls.read_text().strip()) if calls.exists() else 0


def _combined_output(r: subprocess.CompletedProcess) -> str:
    # 越界终止场景 SIGKILL 可能打断 droid exec 的 UTF-8 多字节序列，
    # text=True 的解码在 subprocess.run 内已发生——run 以 errors="replace" 兜底。
    return (r.stdout or "") + (r.stderr or "")


def _classification_field(sandbox: Path, field: str) -> str:
    text = (sandbox / "error-classification.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return ""


# ════════════════════════════════════════════════════════════════════════
# Part A: run_shard.sh 文本契约（防回归回退）
# ════════════════════════════════════════════════════════════════════════


class TestRunShardBoundedExitContract:
    """哨兵/心跳/直写/kill 隔离的文本级契约锁。"""

    def setup_method(self):
        self.content = ENGINE_RUN_SHARD.read_text()

    def test_01_stdout_direct_write_bans_tee(self):
        """v2.1 直写必须：tee 管道下 $! 是 tee 的 PID，心跳持 PID 前提被破坏。"""
        assert "| tee" not in self.content, "droid exec stdout 禁止 tee 管道"
        assert self.content.count('> "$DROID_OUTPUT_FILE" 2>') == 2, (
            "两处 droid exec stdout 必须 > 直写 $DROID_OUTPUT_FILE"
        )

    def test_02_kill_isolation_no_self_group_kill(self):
        """kill 隔离：禁 kill -- -$$ 形态；进程组 kill 仅在 setsid 新进程组下。"""
        code_lines = [
            line for line in self.content.splitlines() if not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "kill --" not in code, "禁用 kill -- 语法（-$$ 会杀脚本自身）"
        assert "-$$" not in code, "禁用 $$ 进程组引用（非交互 bash 下是脚本自身组）"
        assert "pgrep" not in code, "心跳直接持 $!，禁 pgrep（共享 runner 误伤面）"
        assert "setsid" in code, "exec 必须以 setsid（或等价新进程组守卫）启动"

    def test_03_sentinel_constants_and_defaults(self):
        assert 'SHARD_SOFT_TIMEOUT_SEC="${SHARD_SOFT_TIMEOUT_SEC:-2400}"' in self.content
        assert 'SHARD_MAX_TURNS="${SHARD_MAX_TURNS:-60}"' in self.content
        assert 'SENTINEL_FILE="shard-terminated.reason"' in self.content
        assert 'HEARTBEAT_LOG="shard-heartbeat.log"' in self.content

    def test_04_retry_137_guarded_by_sentinel_absence(self):
        """137 重试分支必须带「哨兵不存在」守卫：主动 kill 不进竞态重试。"""
        retry_lines = [
            line
            for line in self.content.splitlines()
            if "-eq 137" in line and line.strip().startswith("if ")
        ]
        assert retry_lines, "137 重试条件行必须存在"
        for line in retry_lines:
            assert "SENTINEL_FILE" in line, f"137 重试条件缺哨兵守卫: {line}"

    def test_05_prompt_budget_rendered_from_soft_timeout(self):
        """prompt 预算文案由 SHARD_SOFT_TIMEOUT_SEC 渲染（v2.1 随修 D），无 30min 残留。"""
        assert "maximum of 30 minutes" not in self.content
        assert "at most ${BUDGET_MINUTES} minutes" in self.content
        budget_line = next(line for line in self.content.splitlines() if "BUDGET_MINUTES=$" in line)
        assert "SHARD_SOFT_TIMEOUT_SEC" in budget_line, "BUDGET_MINUTES 必须源自软超时"

    def test_05b_budget_minutes_ceil_no_zero_render(self):
        """发现6（PRRT...HufO）：BUDGET_MINUTES 禁止地板除——地板除在软超时 <60s 时
        渲染「maximum of 0 minutes」（E2E 3s 软超时即复现）；必须向上取整（+59 ceil
        惯用法）并兜底最低 1 分钟。预算渲染滞后不得成为模型被 SIGKILL 的边界解释。"""
        budget_line = next(line for line in self.content.splitlines() if "BUDGET_MINUTES=$" in line)
        assert "+ 59" in budget_line, "BUDGET_MINUTES 必须向上取整（ceil 惯用法 + 59）"
        assert "BUDGET_MINUTES=1" in self.content, "必须兜底最低 1 分钟"
        for literal in ("maximum of 0 minutes", "maximum of 1 minutes"):
            assert literal not in self.content

    def test_10_stale_state_preclean_before_first_exec(self):
        """发现4+5（PRRT...HuUE / HuZt）：首次 exec 前必须清残留运行态——
        ① 哨兵（脏 workspace 的遗留 shard-terminated.reason 会误触发强制失败
        bail 并压制合法 137 重试）；② 心跳日志与 stderr 残留必须截断（跨 run/
        attempt 残留污染心跳轨迹与分类器内容指纹）。清空块必须先于首次 exec。"""
        assert ': > "$DROID_OUTPUT_FILE"' in self.content
        assert 'rm -f "$SENTINEL_FILE"' in self.content, "哨兵残留必须在首次 exec 前清除"
        clean_idx = self.content.index('rm -f "$SENTINEL_FILE"')
        assert ': > "$HEARTBEAT_LOG"' in self.content, "心跳日志必须按 run 截断"
        hb_idx = self.content.index(': > "$HEARTBEAT_LOG"')
        assert ': > "shard-exec-error.log"' in self.content, "stderr 残留必须在 exec 前截断"
        err_idx = self.content.index(': > "shard-exec-error.log"')
        first_exec_idx = self.content.index("droid exec \\")
        assert clean_idx < hb_idx < err_idx < first_exec_idx, "清残留必须先于首次 droid exec"
        # 两条 exec 路径的 stderr 重定向保留（重试 2>> 追加保两次 attempt 诊断）
        assert self.content.count("shard-exec-error.log &") == 2

    def test_11_retry_path_wrapped_in_heartbeat(self):
        """发现1（PRRT...tHuDf）：137 重试路径必须与首次同面受控——后台化 +
        heartbeat_loop（软超时/turn 上限）+ 哨兵先行终止序列，不得裸 wait 打穿
        45min job timeout。文本契约锁 + e2e（test_e2e_06）行为锁。"""
        hb_idx = self.content.index('heartbeat_loop "$DROID_PID" "$DROID_EXEC_START"')
        retry_idx = self.content.index("-eq 137")
        assert retry_idx > hb_idx, "137 重试块必须在首次 heartbeat_loop 之后"
        assert self.content.count('heartbeat_loop "$DROID_PID"') >= 2, (
            "首次 + 重试两条路径都必须受 heartbeat_loop 监控"
        )

    def test_12_turns_count_assistant_lines(self):
        """发现2（PRRT...tHuI_）：turn 计数必须是 assistant 角色行计数（与心跳
        文档、recover_shard_findings.sh 的 jsonl 解析口径一致），非 lines-2——
        头部事件数漂移与 tool_use/meta 行都会让固定减法失真。"""
        assert "lines - 2" not in self.content, "lines-2 固定减法已废除"
        assert 'grep -c \'"role": *"assistant"\'' in self.content

    def test_06_sentinel_forced_failure_precedes_recovery(self):
        """哨兵-恢复强制失败优先：哨兵 bail 块必须在会话恢复调用之前。"""
        assert "不采信" in self.content, "哨兵在时 findings 必须不采信"
        bail_idx = self.content.index("不采信")
        recovery_idx = self.content.index('bash "$RECOVER_HELPER"')
        assert bail_idx < recovery_idx, "哨兵强制失败必须先于会话恢复（恢复产物不采信）"

    def test_07_heartbeat_dual_write_contract(self):
        """心跳双落：独立文件 + job log 单行摘要（VAL-CI-105 行格式）。"""
        assert '>> "$HEARTBEAT_LOG"' in self.content
        assert '"::[heartbeat] shard=$SHARD_ID' in self.content
        assert "heartbeat=stale" in self.content, "jsonl 缺失/停涨必须打 stale 标记"

    def test_08_termination_sequence_order(self):
        """终止序列顺序：哨兵先行 → SIGTERM → 宽限 → SIGKILL（同一函数内）。"""
        idx_sentinel = self.content.index('> "$SENTINEL_FILE"')
        idx_term = self.content.index("kill -TERM")
        idx_kill = self.content.index("kill -KILL")
        assert idx_sentinel < idx_term < idx_kill, "终止序列必须哨兵先行"

    def test_09_droid_exec_literal_count_two(self):
        """VAL-CI-107 锚（本文件内重复锁定）：恰好两处 droid exec 调用。"""
        assert self.content.count("droid exec \\") == 2


# ════════════════════════════════════════════════════════════════════════
# Part B: e2e（fake droid，VAL-CI-104 Evidence）
# ════════════════════════════════════════════════════════════════════════


class TestBoundedExitE2E:
    """吊死 / turn 爆炸 / 正常收敛 / 哨兵-恢复 / kill 隔离 五场景 e2e。"""

    def test_e2e_01_hang_droid_soft_timeout_testament(self, bounded_sandbox):
        """吊死 → 软超时：哨兵 soft_timeout + 终止序列 + 137 守卫 + 心跳双落 + 遗书。"""
        ws = bounded_sandbox
        r = _run_bounded(ws, {"mode": "hang"})
        assert r.returncode != 0, "越界终止必须失败出口"
        combined = r.stdout + r.stderr

        sentinel = ws / "shard-terminated.reason"
        assert sentinel.exists(), "哨兵必须落盘"
        assert sentinel.read_text().strip() == "soft_timeout"

        # 终止序列顺序：哨兵先写 → SIGTERM（→ SIGKILL，fake droid 忽略 TERM）
        assert "sentinel written" in combined
        assert combined.index("sentinel written") < combined.index("SIGTERM")

        # 137 守卫：SIGKILL 后 shell 侧观察到 137 + stdout 空，但哨兵在 → 不重试
        assert _droid_calls(ws) == 1, "主动 kill 不得进入 137 竞态重试"
        assert "retrying once" not in combined

        # 分类：soft_timeout（先于 exit_137）；真实退出码照记不伪造
        assert _classification_field(ws, "error_class") == "soft_timeout"
        assert _classification_field(ws, "droid_exit") == "137"
        assert _classification_field(ws, "sentinel") == "soft_timeout"

        # 遗书增补字段（④数据校准）
        assert _classification_field(ws, "elapsed_sec").isdigit()
        assert _classification_field(ws, "soft_timeout_sec") == "3"
        assert _classification_field(ws, "max_turns") == "60"

        # 心跳双落
        hb = ws / "shard-heartbeat.log"
        assert hb.exists() and hb.read_text().count("elapsed=") >= 1
        assert "::[heartbeat] shard=0 elapsed=" in r.stdout, "job log 单行摘要必须存在"

    def test_e2e_02_turn_explosion_turn_limit(self, bounded_sandbox):
        """turn 爆炸（jsonl 快速膨胀）→ turn_limit 分类，判定先于 exit_137。"""
        ws = bounded_sandbox
        r = _run_bounded(
            ws,
            {"mode": "turn_spam", "spam_lines": 300, "spam_interval": 0.01},
            extra_env={"SHARD_MAX_TURNS": "5", "SHARD_SOFT_TIMEOUT_SEC": "1500"},
        )
        assert r.returncode != 0
        sentinel = ws / "shard-terminated.reason"
        assert sentinel.exists()
        assert sentinel.read_text().strip() == "turn_limit"
        assert _classification_field(ws, "error_class") == "turn_limit"
        assert _droid_calls(ws) == 1
        # findings 不采信：turn_limit 出口不产 findings 文件
        assert not (ws / "findings-shard-0.json").exists()

    def test_e2e_03_normal_convergence_zero_regression(self, bounded_sandbox):
        """正常收敛 → 心跳零干预、findings 正常产出、无哨兵（零行为回归）。"""
        ws = bounded_sandbox
        r = _run_bounded(
            ws,
            {"mode": "success"},
            extra_env={"SHARD_SOFT_TIMEOUT_SEC": "120"},
        )
        assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
        out = json.loads((ws / "findings-shard-0.json").read_text())
        assert out == STDOUT_FINDINGS
        assert not (ws / "shard-terminated.reason").exists(), "正常收敛不得写哨兵"
        assert _droid_calls(ws) == 1
        # 心跳照常采样（双落），prompt 预算由软超时渲染（120s → 2 minutes，
        # 发现3/6 修后口径：at most 软预算 + hard cutoff 同值）
        assert (ws / "shard-heartbeat.log").read_text().count("elapsed=") >= 1
        assert "::[heartbeat] shard=0 elapsed=" in r.stdout
        prompt = (ws / "prompt.md").read_text()
        assert "at most 2 minutes" in prompt
        assert "hard cutoff at 2 minutes" in prompt

    def test_e2e_04_sentinel_recovery_findings_not_trusted(self, bounded_sandbox):
        """哨兵在 + droid 碰巧收敛（stdout + 可恢复会话）→ findings 不采信，仍按哨兵分类。"""
        ws = bounded_sandbox
        r = _run_bounded(
            ws,
            {
                "mode": "success",
                "write_sentinel": True,
                "sentinel_reason": "turn_limit",
                "write_session": True,
            },
        )
        assert r.returncode != 0, "哨兵在 = 强制失败优先（防越界假绿）"
        assert not (ws / "findings-shard-0.json").exists(), "恢复/产出的 findings 不得采信"
        assert _classification_field(ws, "error_class") == "turn_limit"
        assert _classification_field(ws, "sentinel") == "turn_limit"
        combined = r.stdout + r.stderr
        assert "session jsonl recovery" not in combined, "哨兵 bail 必须先于会话恢复"

    def test_e2e_05_kill_isolation_script_survives(self, bounded_sandbox):
        """kill 隔离：软超时终止后 run_shard.sh 自身退出码非 SIGTERM 系，分类/遗书正常走完。"""
        ws = bounded_sandbox
        r = _run_bounded(ws, {"mode": "hang"})
        assert r.returncode not in (130, 137, 143), (
            f"脚本自身退出码被 SIGTERM/SIGKILL 波及: {r.returncode}"
        )
        assert r.returncode == 1, "失败出口统一 exit 1（fail-closed 形态不变）"
        assert (ws / "error-classification.txt").exists(), "分类遗书必须正常落盘"
        assert (ws / "droid-host-log-redacted.txt").exists(), "脱敏采集必须照常执行"
        assert "error_class=soft_timeout" in (r.stdout + r.stderr)

    def test_e2e_06_retry_hang_killed_by_soft_timeout(self, bounded_sandbox):
        """发现1（P1）：137 重试路径纳入软超时监控——首 exec 137 空 stdout → 重试
        attempt 吊死 → heartbeat_loop 按软超时写哨兵+终止，不打穿 job timeout。"""
        ws = bounded_sandbox
        r = _run_bounded(ws, {"mode": "exit137_then_hang"})
        assert r.returncode != 0, "重试吊死必须走失败出口"
        combined = r.stdout + r.stderr
        assert "retrying once" in combined, "137 竞态重试必须先发生"
        sentinel = ws / "shard-terminated.reason"
        assert sentinel.exists(), "重试 attempt 越界必须写哨兵"
        assert sentinel.read_text().strip() == "soft_timeout", (
            "重试 attempt 吊死由软超时终止（非裸 wait 挂死）"
        )
        assert _droid_calls(ws) == 2, "恰好一次 137 竞态重试"
        assert _classification_field(ws, "error_class") == "soft_timeout", (
            "哨兵先行：重试终止按 soft_timeout 分类（非 exit_137）"
        )
        assert _classification_field(ws, "droid_exit") == "137"
        assert _classification_field(ws, "sentinel") == "soft_timeout"
        hb = ws / "shard-heartbeat.log"
        assert hb.exists(), "重试路径心跳必须双落"
        assert "reason=soft_timeout" in hb.read_text(), "终止事件必须落心跳日志"

    def test_e2e_07_stale_sentinel_and_stderr_not_polluting_retry(self, bounded_sandbox):
        """发现4+5（P3）：残留哨兵不得压制合法 137 重试；首次 stderr 截断——
        残留 429 行不得进入本次分类指纹（rate_limit_429 误判）。"""
        ws = bounded_sandbox
        (ws / "shard-terminated.reason").write_text("soft_timeout\n", encoding="utf-8")
        stale_stderr = (
            '{"error": "quota exceeded"} (HTTP 429)\nMetaError: stale line from previous run\n'
        )
        (ws / "shard-exec-error.log").write_text(stale_stderr, encoding="utf-8")
        stale_hb = "epoch=1 elapsed=99999s turns=999999 bytes=0 heartbeat=live\n"
        (ws / "shard-heartbeat.log").write_text(stale_hb, encoding="utf-8")
        r = _run_bounded(ws, {"mode": "exit137_then_success"})
        assert r.returncode == 0, (
            f"残留哨兵不得压制 137 竞态重试与正常收敛:\n{r.stdout}\n{r.stderr}"
        )
        assert _droid_calls(ws) == 2, "残留哨兵清除后 137 重试必须发生"
        assert "retrying once" in (r.stdout + r.stderr)
        out = json.loads((ws / "findings-shard-0.json").read_text())
        assert out == STDOUT_FINDINGS
        err_log = (ws / "shard-exec-error.log").read_text()
        assert "stale line from previous run" not in err_log, "首次 exec stderr 必须截断"
        hb = (ws / "shard-heartbeat.log").read_text()
        assert "elapsed=99999s" not in hb, "心跳日志必须按 run 截断"


# ════════════════════════════════════════════════════════════════════════
# Part C: artifact 白名单（VAL-CI-105）
# ════════════════════════════════════════════════════════════════════════


def _upload_step(workflow_path: Path) -> dict:
    data = yaml.safe_load(workflow_path.read_text())
    review = data["jobs"]["review-shard"]
    for step in review["steps"]:
        if str(step.get("uses", "")).startswith("actions/upload-artifact"):
            return step
    raise AssertionError(f"upload-artifact step not found in {workflow_path.name}")


class TestHeartbeatArtifactWhitelist:
    """shard-heartbeat.log / shard-terminated.reason 进 Upload findings path 白名单。"""

    def test_01_heartbeat_log_in_whitelist(self):
        paths = _upload_step(WORKFLOW_SHARDS)["with"]["path"]
        assert "shard-heartbeat.log" in paths

    def test_02_sentinel_in_whitelist(self):
        """哨兵与遗书同目录落盘并随既有白名单上传（不新增 path 面）。"""
        paths = _upload_step(WORKFLOW_SHARDS)["with"]["path"]
        assert "shard-terminated.reason" in paths

    def test_03_prefix_contract_unchanged(self):
        name = _upload_step(WORKFLOW_SHARDS)["with"]["name"]
        assert name.startswith("droid-review-debug-"), "watchdog quota-sweep 前缀契约不变"

    def test_04_shell_budget_layer_documented(self):
        """droid-review.yml 预算分层注释面记录 F3 软超时（触发壳文档落点）。"""
        raw = WORKFLOW_SHELL.read_text()
        assert "SHARD_SOFT_TIMEOUT_SEC" in raw
        assert "SHARD_MAX_TURNS" in raw
