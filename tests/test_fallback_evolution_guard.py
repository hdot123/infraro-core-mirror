"""路径 G 演化守卫（owner 2026-10-04 裁定 a，VAL-GATE-020）。

trigger-ci-droid.sh 的 fallback 记录点与 trigger-error-droid.sh（PostHog→exec）
在自动修复执行前读 pending-ci context / 事件上下文：
（M1-D v3.2.2 C3：trigger-ci-droid.sh 侧 fallback 已通知化——守卫保留在记录点
之前，但「开枪」面只剩 trigger-error-droid.sh 一处；Mac 腿零执行体。）

  阳性（context 含 r1-evolution 标记）→ 跳过执行 + PostHog/日志告警
    （不自动修复；修补唯一入口=人工重打 r1-cleared 过 admitDispatch 闸门）
  阴性（context 不含标记）/ 文件缺失 / JSON 损坏 / 不可判定 → 照常执行
    （owner 普通 CI 自愈零改动；阳性识别才拦截，杜绝误伤）

语义基线：fail-open 与 mac-channel-separation（#247）的 fail-closed 方向相反——
通道分离是「演化卡到达 Mac=不变量破坏」的封条；本守卫只拦阳性证据充分的
自动修复执行，证据不足一律放行。

测试四层：
  T1 守卫库三态行为断言（lib/evolution-guard.sh 直接 source 调用）
  T2 trigger-ci-droid.sh fallback 全链路（stub Sessions API；M1-D 起纯记录，
     ECHO_DROID 仅为 error-droid 路径干跑保留）
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
WATCHDOG_SCRIPT_PATH = WEBHOOK_DIR / "ci-timeout-watchdog.sh"
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
# T2: trigger-ci-droid.sh fallback 全链路（stub Sessions API；M1-D 起纯记录）
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
    """trigger-ci-droid.sh 测试环境（WEBHOOK_BASE 隔离；ECHO_DROID 仅 error-droid 干跑保留）。"""
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
    """fallback 三态全链路：守卫在记录点前读 pending-ci context（M1-D 通知化后仍拦截阳性）。"""

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
        assert "FALLBACK: notify-suppressed" not in log_text, (
            "阳性 context 仍走到了 fallback 记录点（守卫未拦截）"
        )
        assert "EVOLUTION-GUARD" in log_text, "缺少演化守卫告警日志"
        assert "ci_fallback_evolution_blocked" in log_text, "缺少 PostHog 告警事件（dry-run 回显）"

    def test_negative_context_fallback_proceeds(self, guard_env, tmp_path):
        """阴性：context 不含标记 → fallback 照常记录 notify-suppressed。"""
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
        assert "FALLBACK: notify-suppressed" in log_text, (
            "阴性 context 未走 fallback 记录（普通 CI 通知链被误伤）"
        )
        assert "EVOLUTION-GUARD" not in log_text, "阴性 context 不应触发演化告警"

    def test_missing_context_field_fallback_proceeds(self, guard_env, tmp_path):
        """阴性：旧 schema 无 context 键 → fallback 照常记录（零行为变化）。"""
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
        assert "FALLBACK: notify-suppressed" in log_text, (
            "无 context 字段未走 fallback 记录（零行为变化被破坏）"
        )
        assert "EVOLUTION-GUARD" not in log_text

    def test_missing_pending_file_fallback_proceeds(self, guard_env, tmp_path):
        """文件缺失：无 pending-ci 文件的 fallback 路径 → 照常记录。"""
        pr_number = 95104  # 故意不创建 pending 文件
        result = _run_ci_trigger(guard_env, pr_number)
        assert result.returncode == 0, result.stderr[-2000:]

        log_text = _guard_combined_log(Path(guard_env["WEBHOOK_BASE"]), result)
        assert "FALLBACK: notify-suppressed" in log_text, (
            "文件缺失路径未走 fallback 记录（不可判定必须放行）"
        )
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
        assert "FALLBACK: notify-suppressed" not in log_text, (
            "过期阳性 context 仍走到了 fallback 记录点（expired 路径守卫绕过回归）"
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
        assert "FALLBACK: notify-suppressed" in log_text, (
            "过期阴性 context 未走 fallback 记录（原语义被破坏）"
        )
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
        assert "FALLBACK: notify-suppressed" not in log_text, "阳性损坏路径仍走到了 fallback 记录点"

    def test_halt_clears_false_dispatch_audit_and_lock(self, guard_env, tmp_path):
        """审计保真：halt 时清除 60min 去重锁（M1-D 通知化后无预写派发审计可清）。

        真实 4xx 序列：注入 410 → 写 fallback 锁 → notify_fallback → 守卫
        halt → 清锁（修复前：锁误挡合法判定 60min）。派发审计字段
        （fallback_dispatched_at/reason）已随执行体一并删除——Mac 腿不再
        预写「派发过」的假审计，halt 后 pending 文件保持原样。
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
        assert "FALLBACK: notify-suppressed" not in log_text, "阳性 4xx 仍走到了 fallback 记录点"
        assert "Client error (4xx)" in log_text, "测试前置未走到 4xx 注入失败路径"
        remaining = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "fallback_dispatched_at" not in remaining, "halt 后仍残留虚假派发审计字段"
        assert "fallback_reason" not in remaining, "halt 后仍残留虚假派发原因"
        assert not fallback_lock.exists(), "halt 后未清除 fallback 去重锁（合法判定被误挡 60min）"

    def test_negative_context_fallback_proceeds_after_precheck(self, guard_env, tmp_path):
        """阴性回归：预检面改造后阴性照常走 fallback 记录（锁保留）。

        M1-D 通知化：fallback_dispatched_at/reason 审计字段已随执行体删除，
        阴性路径的观测面 = notify-suppressed 记录 + 去重锁保留。
        """
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
        assert "FALLBACK: notify-suppressed" in log_text, (
            "阴性 context 未走 fallback 记录（普通自愈被误伤）"
        )
        assert "EVOLUTION-GUARD" not in log_text
        remaining = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "fallback_dispatched_at" not in remaining, "通知化后不得再写派发审计字段"
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
# T3b: watchdog 派发面（INFRA-1302——Phase A/B 补齐 trigger-ci/error 之外的
# 第三个 fallback 开枪点；语义与 VAL-GATE-020 同款：阳性不开枪、保留 pending
# 文件防 5min 重扫绕过，阴性/不可判定放行）
# ============================================================================


def _watchdog_env(tmp_path: Path) -> dict:
    """ci-timeout-watchdog.sh 测试环境（LOCKS_DIR 隔离 + ECHO_DROID 干跑）。

    与 test_ci_timeout_watchdog.py 的 _make_test_env 同口径。
    """
    env = os.environ.copy()
    locks = tmp_path / "locks"
    locks.mkdir(exist_ok=True)
    env["LOCKS_DIR"] = str(locks)
    env["ECHO_DROID"] = "1"
    env["POSTHOG_DRY_RUN"] = "1"
    env.pop("POSTHOG_API_KEY", None)
    env["PYTHON_BIN"] = sys.executable
    # NF-2（终判轮实证）: dispatch-ledger.sh 的 WEBHOOK_BASE 回退会把
    # watchdog fixture 派发的账本记录直落生产 status/ledger/，必须沙箱隔离
    # （先例：本文件 _error_env / test_ci_timeout_watchdog._make_test_env）。
    webhook_base = tmp_path / "webhook"
    (webhook_base / "locks").mkdir(parents=True, exist_ok=True)
    env["WEBHOOK_BASE"] = str(webhook_base)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    env["LOG_DIR"] = str(log_dir)
    return env


def _write_watchdog_pending(
    locks_dir: Path,
    pr_number: int,
    *,
    context: str | None,
    injected: bool,
    age_minutes: int,
) -> Path:
    """写入 watchdog 扫描用的 pending-ci 文件（Phase A: 31min 未注入 / Phase B: 46min 已注入）。

    cwd 指向预置好 origin remote 的本地 git 仓库，使 Phase B 的
    PR_REPO 推导成功（否则 UNKNOWN 分支不到 spawn 面，测试失效）。
    """
    repo_dir = locks_dir.parent / "repo"
    if not repo_dir.exists():
        subprocess.run(["git", "init", "-q", str(repo_dir)], check=True, timeout=30)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "remote",
                "add",
                "origin",
                "https://github.com/test-owner/test-repo.git",
            ],
            check=True,
            timeout=30,
        )
    data: dict = {
        "session_id": "watchdog-guard-test",
        "pr_number": str(pr_number),
        "cwd": str(repo_dir),
        "created_at": (datetime.now(UTC) - timedelta(minutes=age_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    if injected:
        data["injected_at"] = (datetime.now(UTC) - timedelta(minutes=age_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    if context is not None:
        data["context"] = context
    path = locks_dir / f"pending-ci-{pr_number}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run_watchdog(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(WATCHDOG_SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestWatchdogPhaseAEvolutionGuard:
    """Phase A（created>30min 未注入 → fallback 开枪点）守卫三态。"""

    def test_phase_a_positive_context_halts_and_keeps_file(self, tmp_path):
        """阳性：不 spawn fallback、告警、保留 pending 文件（防重扫绕过）。"""
        env = _watchdog_env(tmp_path)
        pr_number = 95201
        pending = _write_watchdog_pending(
            Path(env["LOCKS_DIR"]),
            pr_number,
            context="r1-evolution",
            injected=False,
            age_minutes=35,
        )

        result = _run_watchdog(env)
        out = result.stdout + result.stderr

        assert "EVOLUTION-GUARD" in out, "Phase A 阳性缺少演化守卫告警日志"
        assert "watchdog_phase_a_evolution_blocked" in out, "缺少 PostHog 告警事件（dry-run 回显）"
        assert "Would run" not in out and "[ECHO_DROID]" not in out, (
            "Phase A 阳性仍启动了 fallback droid exec（守卫未拦截）"
        )
        assert "notify-suppressed" not in out, "Phase A 阳性仍走到了 fallback 记录点（守卫未拦截）"
        assert pending.exists(), "Phase A 阳性必须保留 pending 文件（删除会让下一轮重扫绕过守卫）"
        # alert-once：首警后 pending 文件写入 evolution_alerted_at 去重标记
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data.get("evolution_alerted_at"), "阳性首警后未写 evolution_alerted_at 去重标记"
        # P1 修复回归钉：EVOLUTION-GUARD 日志行经 log() 落 LOG_FILE
        # （watchdog 此前未定义 log()，阳性时 stderr 报 command not found）
        log_lines = (Path(env["LOG_DIR"]) / "ci-timeout-watchdog-posthog.log").read_text(
            encoding="utf-8"
        )
        assert "EVOLUTION-GUARD" in log_lines, "EVOLUTION-GUARD 告警未经 log() 落盘"

    def test_phase_a_positive_rescan_alerts_once(self, tmp_path):
        """alert-once：二次重扫（5min 周期）不再重复告警，文件仍保留。"""
        env = _watchdog_env(tmp_path)
        pr_number = 95211
        pending = _write_watchdog_pending(
            Path(env["LOCKS_DIR"]),
            pr_number,
            context="r1-evolution",
            injected=False,
            age_minutes=35,
        )

        first = _run_watchdog(env)
        out_first = first.stdout + first.stderr
        assert "watchdog_phase_a_evolution_blocked" in out_first
        assert "already alerted" not in out_first

        second = _run_watchdog(env)
        out_second = second.stdout + second.stderr
        # 重扫：守卫仍拦截（不 spawn），但告警去重
        assert "[ECHO_DROID]" not in out_second, "重扫阳性仍不得开枪"
        assert "notify-suppressed" not in out_second, "重扫阳性仍不得走到 fallback 记录点"
        assert "watchdog_phase_a_evolution_blocked" not in out_second, (
            "重扫重复发出 PostHog 告警（alert-once 失效）"
        )
        assert "already alerted" in out_second
        assert pending.exists(), "重扫后 pending 文件必须保留（bypass 防御不变）"

    def test_phase_a_negative_context_proceeds(self, tmp_path):
        """阴性：照常 fallback 开枪（ECHO 落痕）+ 清理文件（既有语义不回归）。"""
        env = _watchdog_env(tmp_path)
        pr_number = 95202
        pending = _write_watchdog_pending(
            Path(env["LOCKS_DIR"]),
            pr_number,
            context="agent fix CI",
            injected=False,
            age_minutes=35,
        )

        result = _run_watchdog(env)
        out = result.stdout + result.stderr

        assert "notify-suppressed" in out, "Phase A 阴性必须照常记录（fail-open 语义）"
        assert "EVOLUTION-GUARD" not in out
        assert not pending.exists(), "Phase A 阴性照常清理（既有语义不回归）"

    def test_phase_a_missing_context_field_proceeds(self, tmp_path):
        """无 context 字段（不可判定）→ 放行（普通 CI 自愈零改动）。"""
        env = _watchdog_env(tmp_path)
        pr_number = 95203
        _write_watchdog_pending(
            Path(env["LOCKS_DIR"]), pr_number, context=None, injected=False, age_minutes=35
        )

        result = _run_watchdog(env)
        out = result.stdout + result.stderr

        assert "notify-suppressed" in out, "无 context 字段必须放行（fail-open）"
        assert "EVOLUTION-GUARD" not in out


class TestWatchdogPhaseBEvolutionGuard:
    """Phase B（injected>45min 未消费 → fallback 开枪点）守卫三态。

    Phase B 需要 gh pr view 判 MERGED/OPEN：stub gh 返回 OPEN 使流程走到
    spawn 分支（守卫所在）。P0A_DRY_RUN=1 防 sentinel 查询打到 live 仓库。
    """

    @pytest.fixture
    def phase_b_env(self, tmp_path):
        env = _watchdog_env(tmp_path)
        # stub gh：pr view 返回 state=OPEN（非 MERGED/UNKNOWN → 走 spawn 分支）
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh_stub = bin_dir / "gh"
        gh_stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"pr view"* ]]; then echo \'{"state":"OPEN"}\'; exit 0; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        gh_stub.chmod(0o755)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["P0A_DRY_RUN"] = "1"
        return env

    def test_phase_b_positive_context_halts_and_keeps_file(self, phase_b_env, tmp_path):
        """阳性：不 spawn、告警、保留 pending 文件。"""
        pr_number = 95204
        pending = _write_watchdog_pending(
            Path(phase_b_env["LOCKS_DIR"]),
            pr_number,
            context="r1-evolution",
            injected=True,
            age_minutes=50,
        )

        result = _run_watchdog(phase_b_env)
        out = result.stdout + result.stderr

        assert "EVOLUTION-GUARD" in out, "Phase B 阳性缺少演化守卫告警日志"
        assert "watchdog_phase_b_evolution_blocked" in out
        assert "Would run" not in out and "[ECHO_DROID]" not in out, (
            "Phase B 阳性仍启动了 fallback droid exec（守卫未拦截）"
        )
        assert "notify-suppressed" not in out, "Phase B 阳性仍走到了 fallback 记录点（守卫未拦截）"
        assert pending.exists(), "Phase B 阳性必须保留 pending 文件（防重扫绕过）"
        # alert-once：首警后 pending 文件写入去重标记
        data = json.loads(pending.read_text(encoding="utf-8"))
        assert data.get("evolution_alerted_at"), "Phase B 阳性首警后未写去重标记"

    def test_phase_b_negative_context_proceeds(self, phase_b_env, tmp_path):
        """阴性：照常开枪 + 清理（既有语义不回归）。"""
        pr_number = 95205
        pending = _write_watchdog_pending(
            Path(phase_b_env["LOCKS_DIR"]),
            pr_number,
            context="session fix",
            injected=True,
            age_minutes=50,
        )

        result = _run_watchdog(phase_b_env)
        out = result.stdout + result.stderr

        assert "notify-suppressed" in out, "Phase B 阴性必须照常记录（fail-open 语义）"
        assert "EVOLUTION-GUARD" not in out
        assert not pending.exists()

    def test_phase_b_missing_context_field_proceeds(self, phase_b_env, tmp_path):
        """无 context 字段（不可判定）→ 放行。"""
        pr_number = 95206
        _write_watchdog_pending(
            Path(phase_b_env["LOCKS_DIR"]),
            pr_number,
            context=None,
            injected=True,
            age_minutes=50,
        )

        result = _run_watchdog(phase_b_env)
        out = result.stdout + result.stderr

        assert "notify-suppressed" in out, "无 context 字段必须放行（fail-open）"
        assert "EVOLUTION-GUARD" not in out


# ============================================================================
# T4: 结构性不变量（守卫先于开枪、MANIFEST 注册、语法/shellcheck 门）
# ============================================================================


class TestStructuralInvariants:
    """grep 核验：守卫调用点必须位于 p0a 守卫与 droid exec 开枪点之前。"""

    def test_ci_guard_precedes_p0a_and_droid_exec(self):
        text = CI_SCRIPT_PATH.read_text(encoding="utf-8")
        guard_pos = text.find("_evolution_guard_run")
        p0a_pos = text.find("_p0a_guard_run")
        record_pos = text.find("notify-suppressed")
        assert guard_pos != -1, "trigger-ci-droid.sh 未接入路径 G 演化守卫"
        assert p0a_pos != -1 and record_pos != -1
        assert guard_pos < p0a_pos, "演化守卫必须位于 P0-A 守卫之前（最具体拦截最先）"
        assert guard_pos < record_pos, "演化守卫必须位于 fallback 记录点之前"
        # M1-D（v3.2.2 C3）：Mac 腿零执行体
        assert "droid exec" not in text, "Mac 腿不得再含 droid exec 执行体"
        assert "with_timeout" not in text, "Mac 腿不得再含执行体包装器 with_timeout"

    def test_error_guard_precedes_droid_exec(self):
        text = ERROR_SCRIPT_PATH.read_text(encoding="utf-8")
        guard_pos = text.find("_evolution_guard_run")
        droid_pos = text.find("with_timeout 1800")
        assert guard_pos != -1, "trigger-error-droid.sh 未接入路径 G 演化守卫"
        assert droid_pos != -1
        assert guard_pos < droid_pos, "演化守卫必须位于异步 droid exec 启动之前"

    def test_watchdog_guard_precedes_spawn_fallback(self):
        """watchdog 两个记录点（Phase A/B）都必须先过演化守卫（M1-D 通知化后仍前置）。"""
        text = WATCHDOG_SCRIPT_PATH.read_text(encoding="utf-8")
        guard_positions = [
            i for i in range(len(text)) if text.startswith("_evolution_guard_run", i)
        ]
        record_positions = [
            i for i in range(len(text)) if text.startswith('notify_fallback "$PR_NUMBER', i)
        ]
        assert len(guard_positions) >= 2, "ci-timeout-watchdog.sh 未接入演化守卫（Phase A/B 双点）"
        assert len(record_positions) >= 2, "watchdog notify_fallback 调用点结构漂移"
        for record_pos in record_positions:
            assert any(g < record_pos for g in guard_positions), (
                "存在未过演化守卫的 notify_fallback 记录点（Phase A/B 守卫必须前置）"
            )
        # M1-D（v3.2.2 C3）：watchdog 零执行体
        assert "droid_bin" not in text and "droid exec" not in text, (
            "watchdog 不得再含 droid 执行体"
        )
        assert "nohup" not in text, "watchdog 不得再含 nohup 后台启动"

    def test_watchdog_bash_syntax_and_shellcheck(self):
        """watchdog 脚本语法 + CI 同款 shellcheck 严格度（-x 无截断）。"""
        proc = subprocess.run(
            ["bash", "-n", str(WATCHDOG_SCRIPT_PATH)], capture_output=True, text=True, timeout=30
        )
        assert proc.returncode == 0, f"ci-timeout-watchdog.sh 语法错误：{proc.stderr}"
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck 不可用（CI 由 lint-bundle 覆盖）")
        proc = subprocess.run(
            ["shellcheck", "-x", str(WATCHDOG_SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"ci-timeout-watchdog.sh 未过 CI lint 门（shellcheck -x）：\n{proc.stdout[-2000:]}"
        )

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


# ============================================================================
# NF-2（m5-final 终判轮）: 沙箱 WEBHOOK_BASE 隔离守卫——dispatch-ledger.sh
# 的 LEDGER_WEBHOOK_BASE 回退 $HOME/.factory/webhook，测试 fixture 未设
# WEBHOOK_BASE 时派发计量直落生产 status/ledger/（生产账本 7 条 fixture
# 污染记录根因）。守卫=静态断言两个 env 构造函数必须显式覆盖 WEBHOOK_BASE
# （与 INFRA-GUARD-004 的 LOG_DIR 守卫同构，防 fixture 漂移回潮）。
# ============================================================================


class TestSandboxWebhookBaseIsolation:
    def test_watchdog_env_sets_webhook_base(self, tmp_path):
        """_watchdog_env 返回的 env 必须含沙箱 WEBHOOK_BASE 且不等于生产路径。"""
        env = _watchdog_env(tmp_path)
        base = env.get("WEBHOOK_BASE")
        assert base, "_watchdog_env 缺少 WEBHOOK_BASE——dispatch-ledger.sh 会回退生产账本"
        assert Path(base) != Path.home() / ".factory/webhook", (
            "WEBHOOK_BASE 指向生产目录（fixture 漂移）"
        )
        assert Path(base).is_relative_to(tmp_path), "WEBHOOK_BASE 必须在 tmp_path 沙箱内"

    def test_ledger_writes_stay_in_sandbox(self, tmp_path):
        """行为断言：沙箱 env 下 source dispatch-ledger + ledger_claim 零生产写入。

        正向证明（INFRA-GUARD-002 同构）：同一 claim 在沙箱 WEBHOOK_BASE 下
        确实产生账本文件（写路径已触发），生产账本集合不变。
        """
        prod_ledger = Path.home() / ".factory/webhook/status/ledger"
        before = {p.name for p in prod_ledger.glob("*.json")} if prod_ledger.exists() else set()

        env = _watchdog_env(tmp_path)
        script_dir = REPO_ROOT / "webhook-scripts"
        claim = (
            f'source "{script_dir}/lib/dispatch-ledger.sh" && '
            f'ledger_claim "nf2-sandbox-proof-uuid" "ci-fallback" "INFRA-95301" '
            f'"ci fallback PR #95301" "" "ci-fallback"'
        )
        proc = subprocess.run(
            ["bash", "-c", claim],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"沙箱 claim 失败: {proc.stderr[-1000:]}"

        sandbox_ledger = Path(env["WEBHOOK_BASE"]) / "status" / "ledger"
        sandbox_records = list(sandbox_ledger.glob("nf2-sandbox-proof-uuid.json"))
        assert sandbox_records, "沙箱账本零写入（假阴性风险）：claim 未触发写路径，请检查 fixture"

        after = {p.name for p in prod_ledger.glob("*.json")} if prod_ledger.exists() else set()
        assert after == before, (
            f"NF-2: 沙箱 claim 期间生产账本集合变化 新增={sorted(after - before)} "
            f"消失={sorted(before - after)}"
        )

    def test_env_builders_static_guard(self):
        """静态守卫：两个文件的三处 env 构造函数必须显式覆盖 WEBHOOK_BASE。

        防未来重构成继承生产 WEBHOOK_BASE（INFRA-GUARD-004 同款守卫面）。
        """
        for test_file, helpers in (
            (
                REPO_ROOT / "tests" / "test_fallback_evolution_guard.py",
                ("_watchdog_env", "_error_env"),
            ),
            (
                REPO_ROOT / "tests" / "test_ci_timeout_watchdog.py",
                ("_make_test_env", "_sandbox_env_for_scripts"),
            ),
        ):
            source = test_file.read_text(encoding="utf-8")
            for helper in helpers:
                start = source.index(f"def {helper}")
                end = source.find("\n\n\n", start)
                if end == -1:
                    end = len(source)
                body = source[start:end]
                assert 'env["WEBHOOK_BASE"] = str(' in body, (
                    f"{test_file.name}::{helper} 缺少 WEBHOOK_BASE 沙箱覆盖"
                    "（NF-2 生产账本污染守卫）"
                )


# ============================================================================
# NF-4（m5-final 终批 #5）: watchdog PostHog key 密钥卫生——plist 不再承载
# 明文 POSTHOG_API_KEY；watchdog 自带 env 优先 / op-mcp 兜底的解析链
# （trigger-ci-droid.sh FACTORY_TOKEN 先例：:744-756）。守卫=静态断言
# 解析块存在（env 优先 + op_get_field 兜底 + 缺失告警）。
# ============================================================================


class TestWatchdogPosthogKeyHygiene:
    WATCHDOG_SCRIPT = WEBHOOK_DIR / "ci-timeout-watchdog.sh"

    def test_watchdog_resolves_posthog_key_from_env_or_op_mcp(self):
        """静态守卫：watchdog 必须有 env 优先/op-mcp 兜底的 key 解析块。"""
        source = self.WATCHDOG_SCRIPT.read_text(encoding="utf-8")
        assert 'POSTHOG_API_KEY="${POSTHOG_API_KEY:-}"' in source, (
            "watchdog 缺少 env 优先的 POSTHOG_API_KEY 解析"
        )
        assert "op_get_field" in source, "watchdog 缺少 op-mcp 兜底取 key"
        assert '"euar3izxbu7grsje3r7kuuxqza"' in source and '"credential"' in source, (
            "watchdog 缺少 op-mcp item/field 定位（PostHog / Project API Key / memory-core）"
        )

    def test_watchdog_runs_without_posthog_key(self, tmp_path):
        """行为断言：无 POSTHOG_API_KEY 时 watchdog 正常扫描退出（跳过上报不致命）。"""
        env = _watchdog_env(tmp_path)
        proc = subprocess.run(
            ["bash", str(self.WATCHDOG_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"无 key 时 watchdog 非零退出（key 缺失必须只降级上报面）: {proc.stderr[-800:]}"
        )
