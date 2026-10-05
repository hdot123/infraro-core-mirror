"""路径 G 演化守卫（owner 2026-10-04 裁定 a，VAL-GATE-020）。

trigger-ci-droid.sh 的 spawn_fallback 与 trigger-error-droid.sh（PostHog→exec）
在自动修复开枪前读 pending-ci context / 事件上下文：

  阳性（context 含 r1-evolution 标记）→ 跳过执行 + PostHog/日志告警
    （不自动修复；修补唯一入口=人工重打 r1-cleared 过 admitDispatch 闸门）
  阴性（context 不含标记）/ 文件缺失 / JSON 损坏 / 不可判定 → 照常执行
    （owner 普通 CI 自愈零改动；阳性识别才拦截，杜绝误伤）

语义基线：fail-open 与 mac-channel-separation（#247）的 fail-closed 方向相反——
通道分离是「演化卡到达 Mac=不变量破坏」的封条；本守卫只拦阳性证据充分的
自动修复执行，证据不足一律放行。

测试四层：
  T1 守卫库三态行为断言（lib/evolution-guard.sh 直接 source 调用）
  T2 trigger-ci-droid.sh fallback 全链路（stub Sessions API，ECHO_DROID 干跑）
  T3 trigger-error-droid.sh 事件路径（守卫前置豁免/放行）
  T4 结构性不变量（守卫先于开枪、MANIFEST 注册、bash 语法/shellcheck 门）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from tests.stub_api_helpers import run_stub_api_server

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_DIR = REPO_ROOT / "webhook-scripts"
CI_SCRIPT_PATH = WEBHOOK_DIR / "trigger-ci-droid.sh"
ERROR_SCRIPT_PATH = WEBHOOK_DIR / "trigger-error-droid.sh"
GUARD_LIB_PATH = WEBHOOK_DIR / "lib" / "evolution-guard.sh"
MANIFEST_PATH = WEBHOOK_DIR / "MANIFEST.sh"

GUARD_LIB_RELATIVE = "webhook-scripts/lib/evolution-guard.sh"


# ============================================================================
# T1: 守卫库三态行为断言（lib/evolution-guard.sh 直接 source 调用）
# ============================================================================


def _run_guard_lib(script: str, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """source lib/evolution-guard.sh 后执行调用片段，返回进程结果。

    POSTHOG_DRY_RUN=1 隔离外发；POSTHOG_API_KEY 清空（测试机无 key，双保险）。
    """
    env = os.environ.copy()
    env["POSTHOG_DRY_RUN"] = "1"
    env.pop("POSTHOG_API_KEY", None)
    # T1 与 T2/T3 同口径：钉解释器，防外层 PYTHON_BIN 污染导致 fail-open 假绿
    env["PYTHON_BIN"] = sys.executable
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", "-c", f'source "{GUARD_LIB_PATH}"\n{script}'],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _write_pending(locks_dir: Path, pr_number: int, data: dict | str) -> Path:
    """写入 pending-ci 测试文件（data 为 dict 时序列化为 JSON，str 时原样写入）。"""
    path = locks_dir / f"pending-ci-{pr_number}.json"
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")
    return path


_GUARD_PROBE = '_evolution_guard_run "$PF"\necho "result=$EVOLUTION_GUARD_RESULT"\n'


class TestGuardLibThreeStates:
    """三态核心：阳性拦截 / 阴性放行 / 文件缺失放行（fail-open 语义）。"""

    def test_positive_context_blocks(self, tmp_path):
        """阳性：context 含 r1-evolution → halt_evolution（跳过执行）。"""
        pf = _write_pending(tmp_path, 95001, {"pr_number": "95001", "context": "r1-evolution"})
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert result.returncode == 0, result.stderr
        assert "result=halt_evolution" in result.stdout

    def test_positive_context_marker_inside_larger_text(self, tmp_path):
        """阳性：标记嵌在更大上下文文本中（注册模板形态）→ halt_evolution。"""
        pf = _write_pending(
            tmp_path,
            95002,
            {
                "pr_number": "95002",
                "context": "R1 dispatched evolution repair; marker=r1-evolution",
            },
        )
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=halt_evolution" in result.stdout

    def test_positive_context_whitespace_and_casefold(self, tmp_path):
        """阳性：标记首尾空白/大小写变体（trim+casefold）。"""
        pf = _write_pending(tmp_path, 95003, {"context": "  R1-Evolution  "})
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=halt_evolution" in result.stdout

    def test_negative_context_proceeds(self, tmp_path):
        """阴性：context 不含标记（普通 CI 自愈）→ proceed。"""
        pf = _write_pending(
            tmp_path, 95004, {"pr_number": "95004", "context": "agent fix flaky test"}
        )
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=proceed" in result.stdout

    def test_context_key_absent_proceeds(self, tmp_path):
        """阴性：M2/M5 旧 schema 无 context 键 → proceed（零行为变化）。"""
        pf = _write_pending(tmp_path, 95005, {"pr_number": "95005", "cwd": "/test/repo"})
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=proceed" in result.stdout

    def test_missing_file_proceeds(self, tmp_path):
        """文件缺失（fallback 无 pending 路径）→ proceed，照常执行。"""
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(tmp_path / "pending-ci-99999.json")})
        assert result.returncode == 0, result.stderr
        assert "result=proceed" in result.stdout

    def test_corrupted_json_proceeds(self, tmp_path):
        """JSON 损坏（不可判定）→ proceed（fail-open，绝不误伤普通自愈）。"""
        pf = _write_pending(tmp_path, 95006, "{not-valid-json")
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=proceed" in result.stdout

    def test_empty_object_proceeds(self, tmp_path):
        """空对象（无 context 字段）→ proceed。"""
        pf = _write_pending(tmp_path, 95007, {})
        result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
        assert "result=proceed" in result.stdout

    def test_context_non_string_proceeds(self, tmp_path):
        """context 字段为非字符串（数字/数组/null，字段变异）→ proceed。"""
        for idx, bad in enumerate([123, ["r1-evolution"], None], start=8):
            pf = _write_pending(tmp_path, 95000 + idx, {"context": bad})
            result = _run_guard_lib(_GUARD_PROBE, {"PF": str(pf)})
            assert "result=proceed" in result.stdout, f"context={bad!r} 应放行"

    def test_empty_file_path_argument_proceeds(self):
        """调用方传空文件路径（无可判定输入）→ proceed。"""
        result = _run_guard_lib('_evolution_guard_run ""\necho "result=$EVOLUTION_GUARD_RESULT"\n')
        assert result.returncode == 0, result.stderr
        assert "result=proceed" in result.stdout


class TestGuardLibEventContext:
    """事件上下文注入面（trigger-error-droid.sh：PostHog 事件无 pending-ci 文件）。"""

    def test_env_context_positive_blocks(self):
        """EVOLUTION_GUARD_CONTEXT 阳性 → halt_evolution。"""
        result = _run_guard_lib(
            _GUARD_PROBE,
            {"PF": "", "EVOLUTION_GUARD_CONTEXT": "r1-evolution repair chain"},
        )
        assert "result=halt_evolution" in result.stdout

    def test_env_context_negative_proceeds(self):
        """EVOLUTION_GUARD_CONTEXT 阴性 → proceed。"""
        result = _run_guard_lib(
            _GUARD_PROBE,
            {"PF": "", "EVOLUTION_GUARD_CONTEXT": "posthog error: TypeError in worker"},
        )
        assert "result=proceed" in result.stdout

    def test_env_positive_wins_over_negative_file(self, tmp_path):
        """env 阳性 + 文件阴性 → halt（env 是直接证据面）。"""
        pf = _write_pending(tmp_path, 95012, {"context": "normal ci self-heal"})
        result = _run_guard_lib(
            _GUARD_PROBE, {"PF": str(pf), "EVOLUTION_GUARD_CONTEXT": "r1-evolution"}
        )
        assert "result=halt_evolution" in result.stdout

    def test_env_negative_file_positive_still_halts(self, tmp_path):
        """OR 语义（round-2 P2 修复）：env 阴性 + 文件阳性 → halt。

        原 env 绝对优先语义下该组合 fail-open；process-global env 泄漏
        （error-droid 面注入的值残留到 CI hook）会静默关掉文件面检测。
        """
        pf = _write_pending(tmp_path, 95014, {"context": "r1-evolution dispatch"})
        result = _run_guard_lib(
            _GUARD_PROBE, {"PF": str(pf), "EVOLUTION_GUARD_CONTEXT": "posthog TypeError"}
        )
        assert "result=halt_evolution" in result.stdout, (
            "env 阴性 + 文件阳性被 fail-open 放行（OR 语义未生效）"
        )


class TestGuardLibAlert:
    """告警输出：日志行 + PostHog 独立事件名（dry-run 回显断言）。"""

    def test_alert_emits_named_event_and_log_line(self, tmp_path):
        pf = _write_pending(tmp_path, 95013, {"context": "r1-evolution"})
        # log 由调用方提供（生产=tee 双写）；测试 stub 到 stderr 供断言
        script = (
            'log() { echo "[stub-log] $*" >&2; }\n'
            '_evolution_guard_run "$PF"\n'
            '_evolution_guard_alert "ci_fallback_evolution_blocked" "95013"\n'
        )
        result = _run_guard_lib(script, {"PF": str(pf)})
        assert result.returncode == 0, result.stderr
        # PostHog dry-run 回显（stdout）
        assert "ci_fallback_evolution_blocked" in result.stdout
        # 告警日志行（stderr）：含不自动修复与修补入口措辞
        stderr = result.stderr
        assert "EVOLUTION-GUARD" in stderr
        assert "不自动修复" in stderr
        assert "r1-cleared" in stderr


# ============================================================================
# T2: trigger-ci-droid.sh fallback 全链路（stub Sessions API + ECHO_DROID 干跑）
# ============================================================================


class GuardStubSessionsHandler(BaseHTTPRequestHandler):
    """probe GET 恒 200；注入 POST 恒 410 → 走 4xx fallback 路径（守卫所在）。"""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if "/api/v0/sessions/" in self.path and not self.path.endswith("/messages"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"id":"stub","status":"active"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path.endswith("/messages"):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            self.send_response(410)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Gone"}')
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def guard_stub_server():
    yield from run_stub_api_server(GuardStubSessionsHandler)


@pytest.fixture
def guard_env(guard_stub_server, tmp_path):
    """trigger-ci-droid.sh 测试环境（WEBHOOK_BASE 隔离 + ECHO_DROID 干跑）。"""
    env = os.environ.copy()
    env["FACTORY_API_BASE"] = f"{guard_stub_server}/api/v0"

    webhook_base = tmp_path / "webhook"
    (webhook_base / "locks").mkdir(parents=True)
    (webhook_base / "logs").mkdir(parents=True)
    env["WEBHOOK_BASE"] = str(webhook_base)
    env["LOCK_DIR"] = str(webhook_base / "locks")
    env["LOG_DIR"] = str(webhook_base / "logs")

    # 测试假 token（仅满足 ^fk- 前缀校验；stub 不验证真实性）
    env["FACTORY_TOKEN"] = "fk-" + "test-stub-" + "not-real"
    env["ECHO_DROID"] = "1"
    env["POSTHOG_DRY_RUN"] = "1"
    env.pop("POSTHOG_API_KEY", None)
    env["MAX_RETRIES"] = "2"
    env["RETRY_DELAY"] = "1"
    env["PYTHON_BIN"] = sys.executable
    # 与 test_p0a_guard.py 同口径：干跑 P0-A 守卫，避免真实 gh pr view
    # 打到 live 仓库（网络/限流依赖，CI 会 flake）
    env["P0A_DRY_RUN"] = "1"
    flock_path = shutil.which("flock")
    if flock_path:
        env["FLOCK_BIN"] = flock_path
    return env


def _guard_combined_log(webhook_base: Path, result: subprocess.CompletedProcess) -> str:
    out = [result.stdout, result.stderr]
    log_dir = webhook_base / "logs"
    if log_dir.exists():
        for p in sorted(log_dir.glob("*.log")):
            try:
                out.append(p.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(out)


def _run_ci_trigger(env: dict, pr_number: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(CI_SCRIPT_PATH), str(pr_number), "guard-branch", "guardsha0001", "failed"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestTriggerCiFallbackGuard:
    """spawn_fallback 三态全链路：守卫在开枪前读 pending-ci context。"""

    def test_positive_context_skips_fallback(self, guard_env, tmp_path):
        """阳性：context=r1-evolution → 零 droid exec 启动 + 告警。"""
        pr_number = 95101
        (Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json").write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-4xx-evolution-guard",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "r1-evolution",
                }
            )
        )
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" not in log_text, (
            "阳性 context 仍启动了 fallback droid exec（守卫未拦截）"
        )
        assert "EVOLUTION-GUARD" in log_text, "缺少演化守卫告警日志"
        assert "ci_fallback_evolution_blocked" in log_text, "缺少 PostHog 告警事件（dry-run 回显）"

    def test_negative_context_fallback_proceeds(self, guard_env, tmp_path):
        """阴性：context 不含标记 → fallback 照常开枪（ECHO 落痕）。"""
        pr_number = 95102
        (Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json").write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-4xx-normal-guard",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "agent fix CI failure",
                }
            )
        )
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" in log_text, "阴性 context 未走 fallback（普通 CI 自愈被误伤）"
        assert "EVOLUTION-GUARD" not in log_text, "阴性 context 不应触发演化告警"

    def test_missing_context_field_fallback_proceeds(self, guard_env, tmp_path):
        """阴性：旧 schema 无 context 键 → fallback 照常（零行为变化）。"""
        pr_number = 95103
        (Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json").write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-4xx-nocontext-guard",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cwd": "/test/repo",
                    "source": "session",
                }
            )
        )
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" in log_text, "无 context 字段未走 fallback（零行为变化被破坏）"
        assert "EVOLUTION-GUARD" not in log_text

    def test_missing_pending_file_fallback_proceeds(self, guard_env, tmp_path):
        """文件缺失：无 pending-ci 文件的 fallback 路径 → 照常开枪。"""
        pr_number = 95104  # 故意不创建 pending 文件
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" in log_text, "文件缺失路径未走 fallback（不可判定必须放行）"
        assert "EVOLUTION-GUARD" not in log_text

    def test_expired_pending_positive_context_skips_fallback(self, guard_env, tmp_path):
        """P1 回归：过期 pending-ci（>2h）+ context=r1-evolution → 不开枪。

        修复前：expired 分支先 rm pending 文件再 spawn_fallback，守卫读到
        的是不存在的文件 → fail-open 照常开枪（round-2 P1 实证）。
        修复后：rm 前先判定，阳性告警退出，文件保留。
        """
        pr_number = 95105
        expired_ts = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pending = Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json"
        pending.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-expired-evolution-guard",
                    "created_at": expired_ts,
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "r1-evolution",
                }
            )
        )
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" not in log_text, (
            "过期阳性 context 仍启动了 fallback droid exec（expired 路径守卫绕过回归）"
        )
        assert "EVOLUTION-GUARD" in log_text, "缺少演化守卫告警日志"
        assert "ci_fallback_evolution_blocked" in log_text, "缺少 PostHog 告警事件（dry-run 回显）"
        # 阳性时保留文件（证据留存 + 2h 过期兜底仍可重判；删除会让后续每次
        # webhook 重试走 missing-pending 路径无守卫派发）
        assert pending.exists(), "阳性拦截不应删除 pending-ci 文件（证据被销毁）"

    def test_expired_pending_negative_context_fallback_proceeds(self, guard_env, tmp_path):
        """过期 pending-ci（>2h）+ 阴性 context → 照常开枪（原 expired 语义不变）。"""
        pr_number = 95106
        expired_ts = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pending = Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json"
        pending.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-expired-normal-guard",
                    "created_at": expired_ts,
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "agent fix CI failure",
                }
            )
        )
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" in log_text, "过期阴性 context 未走 fallback（原语义被破坏）"
        assert "EVOLUTION-GUARD" not in log_text

    def test_corrupted_pending_positive_env_context_quarantine_no_dispatch(
        self, guard_env, tmp_path
    ):
        """阳性损坏：隔离路径必须在 mv 之前读 context——阳性只隔离不派发。

        彻底不可解析的 JSON 无法携带 context（文件面守卫 fail-open proceed），
        事件上下文是唯一证据面（OR 语义下 env 阳性即 halt）。
        """
        pr_number = 95109
        (Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json").write_text(
            "not-valid-json{{{", encoding="utf-8"
        )
        env = dict(guard_env)
        env["EVOLUTION_GUARD_CONTEXT"] = "r1-evolution"
        result = _run_ci_trigger(env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        quarantine = list(
            Path(guard_env["LOCK_DIR"]).glob(f"pending-ci-{pr_number}.json.corrupted.*")
        )
        assert len(quarantine) == 1, "损坏文件应照常隔离"
        assert "EVOLUTION-GUARD" in log_text, "损坏路径阳性未触发演化守卫"
        assert "[ECHO_DROID]" not in log_text, "阳性损坏路径仍派发了 fallback droid exec"

    def test_halt_clears_false_dispatch_audit_and_lock(self, guard_env, tmp_path):
        """审计保真：halt 时清除调用方预写的 fallback_dispatched_at 与 60min 去重锁。

        真实 4xx 序列：注入 410 → 写 fallback 锁+审计 → spawn_fallback → 守卫
        halt → 清理两者（修复前：审计声称派发过、锁误挡合法派发 60min）。
        """
        pr_number = 95107
        pending_path = Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json"
        pending_path.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-4xx-evolution-audit",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "r1-evolution",
                }
            )
        )
        fallback_lock = Path(guard_env["LOCK_DIR"]) / f"ci-fallback-{pr_number}.lock"
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "EVOLUTION-GUARD" in log_text, "4xx 阳性未触发演化守卫"
        assert "[ECHO_DROID]" not in log_text, "阳性 4xx 仍派发了 fallback droid exec"
        assert "Client error (4xx)" in log_text, "测试前置未走到 4xx 注入失败路径"
        remaining = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "fallback_dispatched_at" not in remaining, "halt 后仍残留虚假派发审计字段"
        assert "fallback_reason" not in remaining, "halt 后仍残留虚假派发原因"
        assert not fallback_lock.exists(), "halt 后未清除 fallback 去重锁（合法派发被误挡 60min）"

    def test_negative_context_fallback_proceeds_after_precheck(self, guard_env, tmp_path):
        """阴性回归：预检面改造后阴性照常走 fallback（含审计+锁保留）。"""
        pr_number = 95108
        pending_path = Path(guard_env["LOCK_DIR"]) / f"pending-ci-{pr_number}.json"
        pending_path.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "session_id": "test-4xx-normal-precheck",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "cwd": "/test/repo",
                    "source": "session",
                    "context": "agent fix flaky CI test",
                }
            )
        )
        fallback_lock = Path(guard_env["LOCK_DIR"]) / f"ci-fallback-{pr_number}.lock"
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "[ECHO_DROID]" in log_text, "阴性 context 未走 fallback（普通自愈被误伤）"
        assert "EVOLUTION-GUARD" not in log_text
        remaining = json.loads(pending_path.read_text(encoding="utf-8"))
        assert remaining.get("fallback_dispatched_at"), "阴性路径审计标记被误删"
        assert fallback_lock.exists(), "阴性路径 fallback 去重锁被误删"


# ============================================================================
# T3: trigger-error-droid.sh 事件路径（守卫在 droid exec 异步启动之前）
# ============================================================================


def _run_error_trigger(env: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ERROR_SCRIPT_PATH), "TestError", "GET", "memory.error", "3", "2026-10-04"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )


def _error_env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    webhook_base = tmp_path / "webhook"
    (webhook_base / "locks").mkdir(parents=True)
    (webhook_base / "logs").mkdir(parents=True)
    env["WEBHOOK_BASE"] = str(webhook_base)
    env["LOCK_DIR"] = str(webhook_base / "locks")
    env["LOG_DIR"] = str(webhook_base / "logs")
    env["POSTHOG_DRY_RUN"] = "1"
    env.pop("POSTHOG_API_KEY", None)
    env["ECHO_DROID"] = "1"  # 干跑：droid exec 零真实启动

    # 路由封闭化：route_repo 三策略依赖主机 repositories.yml 或 ~/infraro-core
    # （CI runner 两者皆无，脚本会在守卫前退出），指向 tmp 路由与 tmp 仓库目录。
    repo_path = tmp_path / "routed-repo"
    repo_path.mkdir()
    env["REPO_CONFIG"] = str(tmp_path / "repositories.yml")
    Path(env["REPO_CONFIG"]).write_text(
        "teams:\n"
        "  test:\n"
        "    repos:\n"
        "      - repoKey: memory\n"
        f"        repoPath: {repo_path}\n"
        "        githubRepo: hdot123/infraro-core\n",
        encoding="utf-8",
    )
    env["PYTHON_BIN"] = sys.executable
    flock_path = shutil.which("flock")
    if flock_path:
        env["FLOCK_BIN"] = flock_path
    return env


def _error_combined_log(tmp_path: Path, result: subprocess.CompletedProcess) -> str:
    out = [result.stdout, result.stderr]
    log_dir = tmp_path / "webhook" / "logs"
    if log_dir.exists():
        for p in sorted(log_dir.glob("*.log")):
            try:
                out.append(p.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(out)


def _wait_for(condition, timeout: float = 20.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


class TestTriggerErrorDroidGuard:
    """trigger-error-droid.sh：事件上下文阳性 → 跳过 error-gateway 派发。"""

    def test_positive_event_context_blocks_dispatch(self, tmp_path):
        """阳性：EVOLUTION_GUARD_CONTEXT 含标记 → 零 async droid exec + 告警。"""
        env = _error_env(tmp_path)
        env["EVOLUTION_GUARD_CONTEXT"] = "r1-evolution error followup"
        result = _run_error_trigger(env, tmp_path)
        assert result.returncode == 0, result.stderr[-2000:]

        time.sleep(2)  # 守卫在后台化之前拦截；短暂等待确认无异步启动
        log_text = _error_combined_log(tmp_path, result)
        assert "async droid exec started" not in log_text, "阳性事件上下文仍派发了 error-gateway"
        assert "EVOLUTION-GUARD" in log_text, "缺少演化守卫告警日志"
        assert "evolution_error_event_blocked" in log_text + result.stdout, (
            "缺少 PostHog 告警事件（dry-run 回显）"
        )

    def test_no_context_proceeds_to_dispatch(self, tmp_path):
        """不可判定（无事件上下文 env）→ 照常派发（零行为变化）。"""
        env = _error_env(tmp_path)
        result = _run_error_trigger(env, tmp_path)
        assert result.returncode == 0, result.stderr[-2000:]

        reached = _wait_for(
            lambda: "async droid exec started" in _error_combined_log(tmp_path, result)
        )
        log_text = _error_combined_log(tmp_path, result)
        assert reached, f"无守卫信号未走原路径（普通错误处理被误伤）。日志：\n{log_text[-1500:]}"
        assert "EVOLUTION-GUARD" not in log_text

    def test_negative_event_context_proceeds(self, tmp_path):
        """阴性：事件上下文不含标记 → 照常派发。"""
        env = _error_env(tmp_path)
        env["EVOLUTION_GUARD_CONTEXT"] = "posthog TypeError heartbeat"
        result = _run_error_trigger(env, tmp_path)
        assert result.returncode == 0, result.stderr[-2000:]

        reached = _wait_for(
            lambda: "async droid exec started" in _error_combined_log(tmp_path, result)
        )
        log_text = _error_combined_log(tmp_path, result)
        assert reached, f"阴性事件上下文未走原路径。日志：\n{log_text[-1500:]}"
        assert "EVOLUTION-GUARD" not in log_text


# ============================================================================
# T4: 结构性不变量（守卫先于开枪、MANIFEST 注册、语法/shellcheck 门）
# ============================================================================


class TestStructuralInvariants:
    """grep 核验：守卫调用点必须位于 p0a 守卫与 droid exec 开枪点之前。"""

    def test_ci_guard_precedes_p0a_and_droid_exec(self):
        text = CI_SCRIPT_PATH.read_text(encoding="utf-8")
        guard_pos = text.find("_evolution_guard_run")
        p0a_pos = text.find("_p0a_guard_run")
        droid_pos = text.find("with_timeout 3600")
        assert guard_pos != -1, "trigger-ci-droid.sh 未接入路径 G 演化守卫"
        assert p0a_pos != -1 and droid_pos != -1
        assert guard_pos < p0a_pos, "演化守卫必须位于 P0-A 守卫之前（最具体拦截最先）"
        assert guard_pos < droid_pos, "演化守卫必须位于 fallback droid exec 之前"

    def test_error_guard_precedes_droid_exec(self):
        text = ERROR_SCRIPT_PATH.read_text(encoding="utf-8")
        guard_pos = text.find("_evolution_guard_run")
        droid_pos = text.find("with_timeout 1800")
        assert guard_pos != -1, "trigger-error-droid.sh 未接入路径 G 演化守卫"
        assert droid_pos != -1
        assert guard_pos < droid_pos, "演化守卫必须位于异步 droid exec 启动之前"

    def test_guard_lib_has_no_droid_exec(self):
        """守卫库本身不得启动任何执行器（守卫只判定+告警）。"""
        lib_text = GUARD_LIB_PATH.read_text(encoding="utf-8")
        non_comment = "\n".join(
            ln for ln in lib_text.splitlines() if ln.strip() and not ln.strip().startswith("#")
        )
        assert "droid" not in non_comment, "守卫库函数体出现 droid 执行调用"

    def test_manifest_registers_guard_lib(self):
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert (
            f'"{GUARD_LIB_RELATIVE.split("/")[-2]}/{GUARD_LIB_RELATIVE.split("/")[-1]}"'.replace(
                "webhook-scripts/", ""
            )
            in manifest
            or '"lib/evolution-guard.sh"' in manifest
        ), "MANIFEST.sh MANAGED_LIB_FILES 必须注册 lib/evolution-guard.sh（随 sync 部署生产副本）"

    def test_bash_syntax_valid(self):
        for path in (CI_SCRIPT_PATH, ERROR_SCRIPT_PATH, GUARD_LIB_PATH):
            proc = subprocess.run(
                ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
            )
            assert proc.returncode == 0, f"{path.name} 语法错误：{proc.stderr}"

    def test_shellcheck_clean(self):
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck 不可用（CI 由 lint-bundle 覆盖）")
        for path in (CI_SCRIPT_PATH, ERROR_SCRIPT_PATH, GUARD_LIB_PATH):
            proc = subprocess.run(
                ["shellcheck", "-S", "warning", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert proc.returncode == 0, f"{path.name} shellcheck 告警：\n{proc.stdout[-2000:]}"

    def test_shellcheck_ci_gate_includes_guard_lib(self):
        """CI lint 门同款严格度（shellcheck -x 无 -S 截断）必须全绿。

        ci.yml 的 Shell lint 步骤对全部 *.sh 跑 shellcheck -x（info 级也 fail）。
        SC2317 这类 info 告警在本测试用 -S warning 下不可见（SC2317 是 info 级，
        高于阈值被截掉），必须用 CI 同款参数才能在本地红出来。
        """
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck 不可用（CI 由 lint-bundle 覆盖）")
        proc = subprocess.run(
            ["shellcheck", "-x", str(GUARD_LIB_PATH)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"evolution-guard.sh 未过 CI lint 门（shellcheck -x）：\n{proc.stdout[-2000:]}"
        )
