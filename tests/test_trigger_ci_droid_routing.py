"""F1-routing: trigger-ci-droid.sh 分流路由测试

覆盖 VAL-REG-005 ~ VAL-REG-010：
- VAL-REG-005: scanner 来源静默清理（闭端口 + unset token 双变体）
- VAL-REG-006: scanner 路径零 PostHog 事件
- VAL-REG-007: 交叉校验不符 → 保守路径（保留文件 + anomaly 事件 + 无 fallback）
- VAL-REG-008: 缺 source 字段 → 默认 session + 记录恰一次
- VAL-REG-009: source=session 到达既有注入路径
- VAL-REG-010: 过期 scanner 文件同样静默清理

测试隔离：使用 ECHO_DROID=1 避免真实 droid exec，POSTHOG_DRY_RUN=1 避免真实事件。
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from tests.pending_ci_helpers import create_pending_file as _create_pending_file
from tests.stub_api_helpers import run_stub_api_server

REPO_ROOT = Path(__file__).parent.parent
TRIGGER_SCRIPT = REPO_ROOT / "webhook-scripts" / "trigger-ci-droid.sh"


# === M2 F6 (rt-verify-no-rebind): 发起者绑定路由测试基建 ===
# ROUTING_STATE 由 stub handler 记录每个会话收到的探活(GET)与注入(POST)，
# 供「第二候选会话零 POST」核心反错投断言使用。
ROUTING_STATE: dict = {"probes": [], "posts": [], "dead_sessions": set()}


class RoutingProbeStubHandler(BaseHTTPRequestHandler):
    """Stub Sessions API：记录探活/注入目标，按 dead_sessions 控制探活 404"""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if "/api/v0/sessions/" in self.path and not self.path.endswith("/messages"):
            session_id = self.path.split("/")[-1]
            ROUTING_STATE["probes"].append(session_id)
            if session_id in ROUTING_STATE["dead_sessions"]:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"detail": "Session does not exist", "status": 404}).encode()
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": session_id, "status": "active"}).encode())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if "/api/v0/sessions/" in self.path and self.path.endswith("/messages"):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            session_id = self.path.split("/")[-2]
            ROUTING_STATE["posts"].append(session_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "messageId": f"msg-{session_id}-{len(ROUTING_STATE['posts'])}",
                        "status": "queued",
                    }
                ).encode()
            )
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def routing_stub_server():
    yield from run_stub_api_server(RoutingProbeStubHandler)


@pytest.fixture
def routing_env(routing_stub_server, tmp_path):
    """完整 API 面：stub Sessions API + 假 token（M2 F6 路由语义测试）"""
    env = _make_env(tmp_path)
    env["FACTORY_API_BASE"] = f"{routing_stub_server}/api/v0"
    # 动态拼假值满足 ^fk- 前缀校验；stub 不验证真实性，禁止源码出现完整 token 字面量
    env["FACTORY_TOKEN"] = "fk-" + "test-stub-" + "not-real"
    env["MAX_RETRIES"] = "2"
    env["RETRY_DELAY"] = "1"
    ROUTING_STATE["probes"].clear()
    ROUTING_STATE["posts"].clear()
    ROUTING_STATE["dead_sessions"].clear()
    return env


def _index_entry(
    session_id: str,
    cwd: str = "/test/repo",
    *,
    mtime: float | None = None,
    role: str | None = "orchestrator",
    calling: str | None = None,
) -> dict:
    """构造 sessions-index 条目；role=None 表示条目无 mission-session 标签"""
    entry: dict = {
        "sessionId": session_id,
        "cwd": cwd,
        "mtime": time.time() if mtime is None else mtime,
        "callingSessionId": calling,
    }
    if role is not None:
        entry["tags"] = [{"name": "mission-session", "metadata": {"role": role}}]
    else:
        entry["tags"] = []
    return entry


def _write_sessions_index(tmp_path: Path, entries: list[dict]) -> None:
    index = tmp_path / "sessions-index.json"
    index.write_text(json.dumps({"entries": entries}))


def _run_trigger(env: dict, pr_number: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _make_env(tmp_path: Path, home_override: Path | None = None) -> dict:
    """构造测试环境变量"""
    env = os.environ.copy()

    # 隔离 webhook 目录
    webhook_base = tmp_path / "webhook"
    webhook_base.mkdir(exist_ok=True)
    env["WEBHOOK_BASE"] = str(webhook_base)

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir(exist_ok=True)
    env["LOCK_DIR"] = str(locks_dir)

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    env["LOG_DIR"] = str(log_dir)

    # 避免真实 API 调用
    env["FACTORY_API_BASE"] = "http://127.0.0.1:1"  # 闭端口
    if "FACTORY_TOKEN" in env:
        del env["FACTORY_TOKEN"]  # unset token

    # 避免真实 droid exec
    env["ECHO_DROID"] = "1"

    # 避免真实 PostHog 事件
    env["POSTHOG_DRY_RUN"] = "1"

    # 跨平台 Python
    env["PYTHON_BIN"] = sys.executable

    # HOME 隔离
    if home_override:
        env["HOME"] = str(home_override)

    return env


def _install_fake_gh_scanner(tmp_path: Path, env: dict) -> None:
    """Install a fake gh in PATH that returns scanner identity for cross-validation."""
    fake_gh_dir = tmp_path / "fake_gh_bin"
    fake_gh_dir.mkdir(exist_ok=True)
    fake_gh = fake_gh_dir / "gh"
    fake_gh.write_text(
        '#!/bin/bash\necho \'{"author": {"login": "evolution-scanner[bot]"}, "labels": [{"name": "evolution-found"}]}\'\n'
    )
    fake_gh.chmod(0o755)
    env["PATH"] = f"{fake_gh_dir}:{env.get('PATH', '')}"


def _install_fake_gh_human(tmp_path: Path, env: dict) -> None:
    """Install a fake gh in PATH that returns human identity (mismatch)."""
    fake_gh_dir = tmp_path / "fake_gh_human"
    fake_gh_dir.mkdir(exist_ok=True)
    fake_gh = fake_gh_dir / "gh"
    fake_gh.write_text('#!/bin/bash\necho \'{"author": {"login": "human-user"}, "labels": []}\'\n')
    fake_gh.chmod(0o755)
    env["PATH"] = f"{fake_gh_dir}:{env.get('PATH', '')}"


class TestVAL_REG_005_Scanner_Silent_Cleanup:
    """VAL-REG-005: scanner 来源静默清理（闭端口 + unset token 双变体）"""

    def test_scanner_silent_cleanup_closed_port(self, tmp_path):
        """FACTORY_API_BASE=http://127.0.0.1:1（闭端口）下 scanner 来源静默清理"""
        env = _make_env(tmp_path)
        env["FACTORY_API_BASE"] = "http://127.0.0.1:1"  # 闭端口
        _install_fake_gh_scanner(tmp_path, env)  # gh 返回 scanner 身份

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4250
        pending_file = _create_pending_file(locks_dir, pr_number, source="scanner")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：脚本成功退出
        assert result.returncode == 0, f"Script should exit 0, got {result.returncode}"

        # 验证：pending 文件被删除
        assert not pending_file.exists(), "Pending file should be deleted for scanner source"

        # 验证：无 fallback spawn（日志不含 "spawn_fallback" 函数调用）
        combined_output = result.stdout + result.stderr
        assert "spawn_fallback" not in combined_output, "Should not spawn fallback for scanner"

    def test_scanner_silent_cleanup_unset_token(self, tmp_path):
        """FACTORY_TOKEN unset 下 scanner 来源静默清理"""
        env = _make_env(tmp_path)
        if "FACTORY_TOKEN" in env:
            del env["FACTORY_TOKEN"]  # unset token
        _install_fake_gh_scanner(tmp_path, env)  # gh 返回 scanner 身份

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4251
        pending_file = _create_pending_file(locks_dir, pr_number, source="scanner")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：脚本成功退出
        assert result.returncode == 0, f"Script should exit 0, got {result.returncode}"

        # 验证：pending 文件被删除
        assert not pending_file.exists(), "Pending file should be deleted for scanner source"


class TestVAL_REG_006_Scanner_Zero_PostHog:
    """VAL-REG-006: scanner 路径零 PostHog 事件"""

    def test_scanner_zero_posthog_noise(self, tmp_path):
        """scanner 来源路径不触发任何 PostHog 事件（POSTHOG_DRY_RUN 日志无 ci_ 前缀事件）"""
        env = _make_env(tmp_path)
        _install_fake_gh_scanner(tmp_path, env)  # gh 返回 scanner 身份

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4252
        _create_pending_file(locks_dir, pr_number, source="scanner")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：日志中无 PostHog 事件发送记录
        combined_output = result.stdout + result.stderr
        # PostHog dry-run 模式会打印 "[DRY-RUN] Would send PostHog event: <event_name>"
        assert "Would send PostHog event" not in combined_output, (
            "Scanner path should not trigger any PostHog events"
        )

        # 验证：无 ci_ 前缀事件（保守路径的 anomaly 事件除外，但 scanner 通过时不应触发）
        ci_events = [
            line for line in combined_output.split("\n") if "ci_" in line and "PostHog" in line
        ]
        assert len(ci_events) == 0, (
            f"Scanner path should not trigger ci_* events, found: {ci_events}"
        )


class TestVAL_REG_007_Scanner_Cross_Validation_Mismatch:
    """VAL-REG-007: 交叉校验不符 → 保守路径"""

    def test_scanner_cross_validation_mismatch(self, tmp_path):
        """交叉校验不符：文件保留 + anomaly 记录 + 无 fallback"""
        env = _make_env(tmp_path)

        # 注入假 gh 命令，返回非 scanner 身份的 PR 元数据
        fake_gh_dir = tmp_path / "fake_gh"
        fake_gh_dir.mkdir()
        fake_gh = fake_gh_dir / "gh"
        fake_gh.write_text("""#!/bin/bash
# 模拟 gh pr view 返回非 scanner 身份的 PR
echo '{"author": {"login": "human-user"}, "labels": []}'
""")
        fake_gh.chmod(0o755)

        # 将假 gh 加入 PATH
        env["PATH"] = f"{fake_gh_dir}:{env.get('PATH', '')}"

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4253
        pending_file = _create_pending_file(locks_dir, pr_number, source="scanner")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：脚本成功退出
        assert result.returncode == 0, f"Script should exit 0, got {result.returncode}"

        # 验证：文件保留（保守路径不删除）
        assert pending_file.exists(), "Pending file should be kept on cross-validation mismatch"

        # 验证：anomaly 事件被记录（日志包含 ci_scanner_source_mismatch）
        combined_output = result.stdout + result.stderr
        assert (
            "ci_scanner_source_mismatch" in combined_output
            or "cross-validation failed" in combined_output.lower()
        ), "Should log anomaly event on cross-validation mismatch"

        # 验证：无 fallback spawn
        assert "spawn_fallback" not in combined_output, "Should not spawn fallback on mismatch"

    def test_scanner_cross_validation_gh_unavailable(self, tmp_path):
        """gh 不可用时走保守路径（D2 裁决）：保留文件 + anomaly 事件 + 无 fallback"""
        env = _make_env(tmp_path)

        # 保留基本命令但排除 gh：使用系统路径但创建空 gh 覆盖
        # 不能清空 PATH，否则 dirname/date 等命令也找不到
        empty_gh_dir = tmp_path / "empty_gh"
        empty_gh_dir.mkdir()
        # 将空目录放在 PATH 前面，但不覆盖整个 PATH
        env["PATH"] = f"{empty_gh_dir}:{env.get('PATH', '/usr/bin:/bin')}"
        # 确保没有 gh 命令可用（空目录里没有 gh）

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4254
        pending_file = _create_pending_file(locks_dir, pr_number, source="scanner")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：脚本成功退出
        assert result.returncode == 0, f"Script should exit 0, got {result.returncode}"

        # 验证：文件保留（D2 裁决：保守路径，不删除）
        # 理由：session 文件误标 scanner + gh 恰好不可用时，
        # 静默清理会删除 session PR 救援通道，违背不变量 2
        assert pending_file.exists(), (
            "Pending file should be kept when gh unavailable (conservative path D2)"
        )

        # 验证：anomaly 事件被记录
        combined_output = result.stdout + result.stderr
        assert (
            "ci_scanner_source_unverifiable" in combined_output
            or "cross-validation unexecutable" in combined_output.lower()
        ), "Should log anomaly event when gh unavailable (conservative path)"

        # 验证：无 fallback spawn
        assert "spawn_fallback" not in combined_output, (
            "Should not spawn fallback when gh unavailable"
        )


class TestVAL_REG_008_Missing_Source_Default_Session:
    """VAL-REG-008: 缺 source 字段 → 默认 session + 记录恰一次"""

    def test_missing_source_defaulted_session(self, tmp_path):
        """缺 source 字段的 legacy 文件被默认到 session + 日志记录恰一次"""
        env = _make_env(tmp_path)

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4255
        # 创建不含 source 字段的 legacy 文件
        _create_pending_file(locks_dir, pr_number, source=None)

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：日志记录 default 信息
        combined_output = result.stdout + result.stderr
        default_logs = [
            line
            for line in combined_output.split("\n")
            if "unknown-source defaulted to session" in line
        ]
        assert len(default_logs) == 1, (
            f"Should log 'unknown-source defaulted to session' exactly once, found {len(default_logs)} times"
        )

        # 验证：继续走既有 session 路径（不删除文件，尝试注入）
        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        # 由于闭端口，注入会失败，但文件应保留（不进入 scanner 清理路径）
        assert pending_file.exists(), "Legacy file should not be deleted by scanner routing"


class TestVAL_REG_009_Session_Source_Reaches_Injection:
    """VAL-REG-009: source=session 到达既有注入路径"""

    def test_session_source_reaches_injection_path(self, tmp_path):
        """source=session 文件到达既有注入路径（不进入 scanner 清理）"""
        env = _make_env(tmp_path)

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4256
        pending_file = _create_pending_file(locks_dir, pr_number, source="session")

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：文件未被删除（session 路径保留文件，尝试注入）
        assert pending_file.exists(), "Session source file should not be deleted by scanner routing"

        # 验证：日志不包含 scanner 清理信息
        combined_output = result.stdout + result.stderr
        assert "silent cleanup" not in combined_output.lower(), (
            "Session source should not enter scanner silent cleanup path"
        )

        # 验证：尝试注入（由于闭端口会失败，但应有注入相关日志）
        # 此处不验证注入成功，只验证未进入 scanner 路径


class TestVAL_REG_010_Expired_Scanner_Silent_Cleanup:
    """VAL-REG-010: 过期 scanner 文件同样静默清理"""

    def test_expired_scanner_silent_cleanup(self, tmp_path):
        """过期 scanner 文件走静默清理而非 expiry fallback 路径"""
        env = _make_env(tmp_path)

        locks_dir = Path(env["LOCK_DIR"])
        pr_number = 4257
        # 创建过期的 scanner 文件（created_at 3 小时前）
        expired_at = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pending_file = _create_pending_file(
            locks_dir, pr_number, source="scanner", created_at=expired_at
        )

        result = subprocess.run(
            [str(TRIGGER_SCRIPT), str(pr_number)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # 验证：脚本成功退出
        assert result.returncode == 0, f"Script should exit 0, got {result.returncode}"

        # 验证：文件被删除（静默清理）
        assert not pending_file.exists(), "Expired scanner file should be silently deleted"

        # 验证：无 expiry fallback 事件（不进入过期路径）
        combined_output = result.stdout + result.stderr
        assert "ci_expired_pending_ci" not in combined_output, (
            "Expired scanner file should not trigger expiry fallback event"
        )

        # 验证：无 fallback spawn
        assert "spawn_fallback" not in combined_output, (
            "Expired scanner file should not spawn fallback"
        )


class TestVAL_RT_003_Injection_Only_To_Pending_Session:
    """VAL-RT-003: 注入仅回 pending 指定会话（核心反错投）"""

    def test_injection_only_to_pending_session(self, routing_env, tmp_path):
        """stub 注册指定会话 + mtime 更新的无关 orchestrator 会话：只有指定会话收到 POST"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4260
        target_session = "session-that-created-pr"
        _create_pending_file(locks_dir, pr_number, source="session", session_id=target_session)

        # 第二候选：无关聊天会话（mtime 更新——旧重绑定语义下它必然赢下注入）
        _write_sessions_index(
            tmp_path,
            [
                _index_entry(target_session, mtime=time.time() - 3600),
                _index_entry("unrelated-chatty-orchestrator", mtime=time.time()),
            ],
        )
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")

        result = _run_trigger(routing_env, pr_number)
        assert result.returncode == 0

        # 核心反错投断言：指定会话恰 1 POST，无关会话零 POST
        assert ROUTING_STATE["posts"] == [target_session], (
            f"Expected POST only to pending session, got: {ROUTING_STATE['posts']}"
        )
        # 探活也仅限指定会话
        assert ROUTING_STATE["probes"] == [target_session]

    def test_rebinding_block_unreachable_new_schema(self, routing_env, tmp_path):
        """旧 rebinding 路径对新 schema 不可达：无候选场景不再按 cwd 选会话"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4261
        target_session = "session-alive-no-index"
        _create_pending_file(locks_dir, pr_number, source="session", session_id=target_session)

        # 空 index：旧语义会 rebinding 失败走 fallback；新语义不依赖 index，直接注入
        _write_sessions_index(tmp_path, [])
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")

        result = _run_trigger(routing_env, pr_number)
        assert result.returncode == 0

        # 指定会话探活活着 → 直接注入（不因 index 无候选而 fallback）
        assert ROUTING_STATE["posts"] == [target_session]
        combined = result.stdout + result.stderr
        assert "fallback" not in combined.lower() or "FALLBACK" not in combined


class TestVAL_RT_004_Dead_Session_No_Rebind:
    """VAL-RT-004: 指定会话死亡 → fallback spawn，不换人"""

    def test_dead_session_falls_back_no_rebind(self, routing_env, tmp_path):
        """指定会话探活 404（无豁免）→ fallback，其他会话零 POST"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4262
        dead_session = "session-that-died"
        _create_pending_file(locks_dir, pr_number, source="session", session_id=dead_session)
        ROUTING_STATE["dead_sessions"].add(dead_session)

        # 第二候选活着且 mtime 更新：旧重绑定会换人投它——新语义必须零 POST
        _write_sessions_index(
            tmp_path,
            [
                _index_entry(dead_session, mtime=time.time() - 7200),
                _index_entry("alive-second-candidate", mtime=time.time()),
            ],
        )
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")

        result = _run_trigger(routing_env, pr_number)
        assert result.returncode == 0

        combined = result.stdout + result.stderr
        # 死亡判定 → fallback spawn
        assert "FALLBACK: Spawning droid exec" in combined, (
            "Dead session must trigger fallback spawn"
        )
        # 不换人：任何其他会话零 POST；死亡会话也零 POST（不烧注入）
        assert ROUTING_STATE["posts"] == [], (
            f"No session should receive POST, got: {ROUTING_STATE['posts']}"
        )
        # 探活仅针对指定会话（不试探第二候选）
        assert ROUTING_STATE["probes"] == [dead_session]
        # fallback 去重锁落盘
        assert (locks_dir / f"ci-fallback-{pr_number}.lock").exists()


class TestVAL_RT_005_Null_Session_Id_No_Misroute:
    """VAL-RT-005: null + source=session 宁丢勿错"""

    def test_null_session_source_session_skips_injection(self, routing_env, tmp_path):
        """null session_id：跳过注入 + ci_no_route_target 遥测，绝不按 cwd 选会话"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4263
        # M2 新 schema：write 脚本未带 --session-id → session_id 显式 null
        _create_pending_file(locks_dir, pr_number, source="session", session_id="")

        # index 里放一个 mtime 最新的无关 orchestrator：旧语义它必被选中
        _write_sessions_index(tmp_path, [_index_entry("unrelated-recent-orchestrator")])
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")

        result = _run_trigger(routing_env, pr_number)
        assert result.returncode == 0

        combined = result.stdout + result.stderr
        # 宁丢勿错：跳过注入（零探活、零 POST）
        assert ROUTING_STATE["posts"] == [], "Null session must not inject anywhere"
        assert ROUTING_STATE["probes"] == [], "Null session must not probe anywhere"
        # 遥测事件
        assert "ci_no_route_target" in combined, "Must emit ci_no_route_target telemetry"
        # 旧 rebinding 路径对新 schema 不可达
        assert "event-time rebinding" not in combined
        assert "Selected session via event-time rebinding" not in combined
        # 文件保留（照 ci_scanner_source_mismatch 先例，不删）
        assert (locks_dir / f"pending-ci-{pr_number}.json").exists()


class TestVAL_RT_006_Scanner_Source_No_Mac_Injection:
    """VAL-RT-006: scanner 来源不注入 Mac 会话"""

    def test_scanner_with_session_id_no_mac_post(self, routing_env, tmp_path):
        """scanner 来源（即使带 session_id）不向任何 Mac 会话 POST"""
        _install_fake_gh_scanner(tmp_path, routing_env)  # 交叉校验通过 → 走静默清理正路
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4264
        _create_pending_file(locks_dir, pr_number, source="scanner", session_id="some-mac-session")
        _write_sessions_index(
            tmp_path, [_index_entry("some-mac-session"), _index_entry("other-session")]
        )
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")

        result = _run_trigger(routing_env, pr_number)
        assert result.returncode == 0

        combined = result.stdout + result.stderr
        assert ROUTING_STATE["posts"] == [], "Scanner source must not POST to any Mac session"
        assert "silent cleanup" in combined, "Scanner must keep silent-cleanup semantics"


class TestVAL_RT_009_Four04_Exemption_Window_Narrowed:
    """VAL-RT-009: 404 仍注入豁免收窄——≤30min 且角色/标签双匹配"""

    def _scenario(self, routing_env, tmp_path, pr_number, index_mtime, role):
        locks_dir = Path(routing_env["LOCK_DIR"])
        pending_file = _create_pending_file(
            locks_dir, pr_number, source="session", session_id="exempt-candidate"
        )
        ROUTING_STATE["dead_sessions"].add("exempt-candidate")
        _write_sessions_index(
            tmp_path, [_index_entry("exempt-candidate", mtime=index_mtime, role=role)]
        )
        routing_env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json")
        result = _run_trigger(routing_env, pr_number)
        return pending_file, result

    def test_fresh_404_with_orchestrator_role_still_injects(self, routing_env, tmp_path):
        """404 + index 10min 新鲜 + role=orchestrator → 豁免仍注入"""
        pr_number = 4265
        _, result = self._scenario(
            routing_env, tmp_path, pr_number, index_mtime=time.time() - 600, role="orchestrator"
        )
        assert result.returncode == 0
        assert ROUTING_STATE["posts"] == ["exempt-candidate"], (
            "Fresh 404 + orchestrator role must still inject"
        )
        assert "attempting POST anyway" in (result.stdout + result.stderr)

    def test_stale_404_over_30min_no_injection(self, routing_env, tmp_path):
        """404 + index 40min（> 30min 窗口）→ 不再豁免，fallback"""
        pr_number = 4266
        self._scenario(
            routing_env, tmp_path, pr_number, index_mtime=time.time() - 2400, role="orchestrator"
        )
        assert ROUTING_STATE["posts"] == [], "404 beyond 30min window must not inject"

    def test_404_at_31min_boundary_no_injection(self, routing_env, tmp_path):
        """边界：index mtime 恰为 31min → 超窗，不豁免"""
        pr_number = 4267
        self._scenario(
            routing_env, tmp_path, pr_number, index_mtime=time.time() - 31 * 60, role="orchestrator"
        )
        assert ROUTING_STATE["posts"] == [], "31min must be outside the 30min exemption"

    def test_404_fresh_but_worker_role_no_injection(self, routing_env, tmp_path):
        """404 + index 新鲜但 role=worker → 双匹配失败，不豁免"""
        pr_number = 4268
        self._scenario(
            routing_env, tmp_path, pr_number, index_mtime=time.time() - 600, role="worker"
        )
        assert ROUTING_STATE["posts"] == [], "Worker-role entry must not satisfy the exemption"

    def test_404_fresh_but_no_mission_tag_no_injection(self, routing_env, tmp_path):
        """404 + index 新鲜但无 mission-session 标签 → 双匹配失败，不豁免"""
        pr_number = 4269
        self._scenario(routing_env, tmp_path, pr_number, index_mtime=time.time() - 600, role=None)
        assert ROUTING_STATE["posts"] == [], (
            "Entry without mission-session tag must not satisfy the exemption"
        )


# === M2 F7 (rt-sha-idempotency): per-SHA 幂等测试基建 ===


def _run_trigger_with_sha(env: dict, pr_number: int, sha: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", sha, "passed"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _seed_sha_state_dir(routing_env: dict) -> Path:
    """启用 trigger 脚本的 PR_SHA_STATE_DIR 覆盖（测试沙箱化 per-SHA 状态目录）"""
    state_dir = Path(routing_env["LOCK_DIR"]) / "pr-sha"
    state_dir.mkdir(parents=True, exist_ok=True)
    routing_env["PR_SHA_STATE_DIR"] = str(state_dir)
    return state_dir


def _sha_state_path(routing_env: dict, session_id: str, pr_number: int, sha: str) -> Path:
    """镜像 trigger 脚本的状态文件命名（session/pr/sha 均为 alnum，sanitize 恒等）"""
    state_dir = Path(routing_env["PR_SHA_STATE_DIR"])
    return state_dir / f"ci-complete-sha-{session_id}-{pr_number}-{sha}.json"


class TestVAL_RT_007_PerSHAIdempotency:
    """VAL-RT-007: fingerprint 键含 SHA——同 session+pr+sha 重复事件第二次跳过注入"""

    def test_same_sha_back_to_back_runs_second_skips(self, routing_env, tmp_path):
        """同 SHA 二连跑：第一次注入成功，第二次零 POST（不探活不注入）"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4270
        session_id = "sha-idem-session"
        sha = "d" * 40
        _create_pending_file(locks_dir, pr_number, source="session", session_id=session_id)
        _seed_sha_state_dir(routing_env)

        first = _run_trigger_with_sha(routing_env, pr_number, sha)
        assert first.returncode == 0
        assert ROUTING_STATE["posts"] == [session_id], "First same-SHA event must inject"

        second = _run_trigger_with_sha(routing_env, pr_number, sha)
        assert second.returncode == 0
        combined = second.stdout + second.stderr
        assert ROUTING_STATE["posts"] == [session_id], (
            "Second same-SHA event must be skipped (zero additional POST)"
        )
        assert ROUTING_STATE["probes"] == [session_id], (
            "Second same-SHA event must not even probe the session"
        )
        assert "Duplicate CI event for same SHA" in combined, "Skip decision must be logged"
        assert "ci_duplicate_sha_event" in combined, "Skip must emit dedup telemetry"
        assert _sha_state_path(routing_env, session_id, pr_number, sha).exists()
        # 跳过路径不得改写 pending 文件（审计轨迹保持首轮原样）
        pending = json.loads((locks_dir / f"pending-ci-{pr_number}.json").read_text())
        assert len(pending["injections"]) == 1, "Skip path must not append to injections[]"

    def test_preseeded_state_skips_injection(self, routing_env, tmp_path):
        """状态条目已存在（首轮在更早进程完成）→ 重复事件同样整体跳过"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4271
        session_id = "sha-preseed-session"
        sha = "e" * 40
        _create_pending_file(locks_dir, pr_number, source="session", session_id=session_id)
        _seed_sha_state_dir(routing_env)
        state_path = _sha_state_path(routing_env, session_id, pr_number, sha)
        state_path.write_text(json.dumps({"fingerprint": f"sha:{session_id}:{pr_number}:{sha}"}))

        result = _run_trigger_with_sha(routing_env, pr_number, sha)
        assert result.returncode == 0

        assert ROUTING_STATE["posts"] == [], "Pre-seeded same-SHA event must not POST"
        assert ROUTING_STATE["probes"] == [], "Pre-seeded same-SHA event must not probe"

    def test_different_sha_injects_normally(self, routing_env, tmp_path):
        """不同 SHA 各自独立键：SHA-1 已注入后，SHA-2 事件正常探活并注入"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4272
        session_id = "sha-distinct-session"
        sha1, sha2 = "1" * 40, "2" * 40
        _create_pending_file(locks_dir, pr_number, source="session", session_id=session_id)
        _seed_sha_state_dir(routing_env)

        r1 = _run_trigger_with_sha(routing_env, pr_number, sha1)
        assert r1.returncode == 0
        r2 = _run_trigger_with_sha(routing_env, pr_number, sha2)
        assert r2.returncode == 0

        assert ROUTING_STATE["posts"] == [session_id, session_id], (
            "Distinct SHAs must each inject (per-SHA keys, no cross-SHA dedup)"
        )
        assert ROUTING_STATE["probes"] == [session_id, session_id], (
            "Distinct SHAs must each probe independently"
        )
        combined = r2.stdout + r2.stderr
        assert "Duplicate CI event" not in combined, "Different SHA must not hit dedup"


class TestVAL_RT_008_InjectionsAuditTrail:
    """VAL-RT-008: injections[] 按 SHA 追加审计轨迹 + 顶层 watchdog 兼容字段保留"""

    def test_injections_array_appends_per_sha(self, routing_env, tmp_path):
        """两 SHA 两次注入 → pending 文件 injections 恰两条，按注入顺序各含审计字段"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4273
        session_id = "audit-trail-session"
        sha1, sha2 = "1" * 40, "2" * 40
        _create_pending_file(locks_dir, pr_number, source="session", session_id=session_id)
        _seed_sha_state_dir(routing_env)

        r1 = _run_trigger_with_sha(routing_env, pr_number, sha1)
        assert r1.returncode == 0
        r2 = _run_trigger_with_sha(routing_env, pr_number, sha2)
        assert r2.returncode == 0

        assert ROUTING_STATE["posts"] == [session_id, session_id]
        pending = json.loads((locks_dir / f"pending-ci-{pr_number}.json").read_text())
        injections = pending["injections"]
        assert isinstance(injections, list) and len(injections) == 2, (
            f"Two SHAs must produce two injection entries, got: {injections}"
        )
        assert [e["sha"] for e in injections] == [sha1, sha2], (
            "injections[] must append per SHA in injection order"
        )
        for entry in injections:
            assert entry.get("injected_at"), "Each entry must carry injected_at"
            assert entry.get("message_id"), "Each entry must carry message_id"
        assert pending["last_injected_sha"] == sha2

    def test_top_level_compat_fields_preserved(self, routing_env, tmp_path):
        """成功注入后顶层 injected_at/message_id 仍在且与条目一致（watchdog 对账兼容面）"""
        locks_dir = Path(routing_env["LOCK_DIR"])
        pr_number = 4274
        session_id = "watchdog-compat-session"
        sha = "3" * 40
        _create_pending_file(locks_dir, pr_number, source="session", session_id=session_id)
        _seed_sha_state_dir(routing_env)

        result = _run_trigger_with_sha(routing_env, pr_number, sha)
        assert result.returncode == 0
        assert ROUTING_STATE["posts"] == [session_id]

        pending = json.loads((locks_dir / f"pending-ci-{pr_number}.json").read_text())
        assert "injected_at" in pending, "Top-level injected_at must be kept for watchdog Phase B"
        assert "message_id" in pending, "Top-level message_id must be kept"
        assert len(pending["injections"]) == 1
        assert pending["injected_at"] == pending["injections"][0]["injected_at"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
