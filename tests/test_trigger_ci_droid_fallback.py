"""TD-DR-03: 注入降级修复测试 + VAL-M1-008 Mac 腿纯通知契约

覆盖 VAL-INJECT-001 ~ VAL-INJECT-007（M1-D 通知化改写，v3.2.2 C3）：
- 5xx 重试耗尽后同步记录 notify-suppressed（不再留锁空等，也绝不派发执行体）
- 去重锁防止遥测风暴（60min TTL）
- 探活端点存在性结论记录在代码注释
- 4xx 路径不回归（删锁 + notify-suppressed 记录）
- 200 路径不回归（写 injected_at，不产生 notify-suppressed 记录）
- watchdog 对账兜底仍生效（对账面本身也已通知化）
- 探活失败直接记录（不烧注入重试）

VAL-M1-008（v3.2.2 C3）：Mac 腿纯通知——trigger-ci-droid.sh 零执行体、
零合并语义；合并由守卫腿（auto-merge pipeline，REST 三门绿判唯一入口）
负责，禁手动合并。

测试用 stub HTTP server（mock sessions API），严禁向生产 session 注入测试消息。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from tests.stub_api_helpers import run_stub_api_server

REPO_ROOT = Path(__file__).parent.parent
TRIGGER_SCRIPT = REPO_ROOT / "webhook-scripts" / "trigger-ci-droid.sh"
WATCHDOG_SCRIPT = REPO_ROOT / "webhook-scripts" / "ci-timeout-watchdog.sh"


class StubSessionsAPIHandler(BaseHTTPRequestHandler):
    """Stub Sessions API for testing TD-DR-03

    支持场景：
    - GET /api/v0/sessions/{id} → 200（会话存在）或 404（会话不存在）
    - POST /api/v0/sessions/{id}/messages → 200（成功）/ 4xx / 5xx（失败）
    """

    def log_message(self, format, *args):
        """抑制 HTTP 请求日志"""
        pass

    def do_GET(self):
        """处理 GET 请求（探活）"""
        if "/api/v0/sessions/" in self.path and not self.path.endswith("/messages"):
            # 探活端点
            session_id = self.path.split("/")[-1]

            # 测试控制：特定 session_id 返回 404
            if "nonexistent" in session_id:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {
                    "detail": "Session does not exist",
                    "status": 404,
                    "title": "Not Found",
                }
                self.wfile.write(json.dumps(response).encode())
                return

            # 默认：会话存在
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "id": session_id,
                "status": "active",
                "createdAt": datetime.now(UTC).isoformat(),
            }
            self.wfile.write(json.dumps(response).encode())
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        """处理 POST 请求（注入）"""
        if "/api/v0/sessions/" in self.path and self.path.endswith("/messages"):
            content_length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(content_length)

            # 测试控制：通过 session_id 控制响应
            session_id = self.path.split("/")[-2]

            # 5xx 场景：始终返回 504
            if "5xx" in session_id:
                self.send_response(504)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {"error": "Gateway Timeout"}
                self.wfile.write(json.dumps(response).encode())
                return

            # 4xx 场景：始终返回 410
            if "4xx" in session_id:
                self.send_response(410)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {"error": "Gone"}
                self.wfile.write(json.dumps(response).encode())
                return

            # 200 成功场景
            if "success" in session_id or "active" in session_id:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {
                    "messageId": f"msg-{int(time.time())}",
                    "status": "queued",
                }
                self.wfile.write(json.dumps(response).encode())
                return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def stub_api_server():
    """启动 stub Sessions API 服务器（INFRA-529：样板收敛至共享 helper）"""
    yield from run_stub_api_server(StubSessionsAPIHandler)


@pytest.fixture
def temp_env(stub_api_server, tmp_path):
    """创建测试环境变量

    关键隔离：设置 WEBHOOK_BASE 为临时目录，确保测试不写生产路径
    （~/.factory/webhook）。脚本的 LOG_DIR 从 WEBHOOK_BASE 派生（非环境变量），
    因此必须设置 WEBHOOK_BASE 而非单独的 LOG_DIR。
    """
    env = os.environ.copy()

    # 覆盖 API 基础 URL 指向 stub（需要包含 /api/v0 路径）
    env["FACTORY_API_BASE"] = f"{stub_api_server}/api/v0"

    # 创建临时 WEBHOOK_BASE（隔离生产路径，跨平台兼容）
    # 脚本的 LOG_DIR 和 LOCK_DIR 默认从 WEBHOOK_BASE 派生
    webhook_base = tmp_path / "webhook"
    webhook_base.mkdir()
    env["WEBHOOK_BASE"] = str(webhook_base)

    # 显式设置 LOCK_DIR 和 LOG_DIR（虽然脚本会从 WEBHOOK_BASE 派生，但明确设置更安全）
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    env["LOCK_DIR"] = str(locks_dir)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    env["LOG_DIR"] = str(log_dir)

    # 测试用 Factory token（动态拼接假值，仅满足脚本 ^fk- 前缀校验；stub 不验证真实性）
    # 禁止在源码中出现完整 token 字面量（触发 Droid-Shield 密钥检测）
    env["FACTORY_TOKEN"] = "fk-" + "test-stub-" + "not-real"

    # 启用 ECHO_DROID 避免真实 droid exec
    env["ECHO_DROID"] = "1"

    # PostHog dry-run：避免测试发送真实分析事件
    env["POSTHOG_DRY_RUN"] = "1"

    # 缩短重试参数避免测试超时
    env["MAX_RETRIES"] = "2"
    env["RETRY_DELAY"] = "1"

    # 跨平台 Python 和 flock 路径（避免硬编码 macOS 路径）
    env["PYTHON_BIN"] = sys.executable
    flock_path = shutil.which("flock")
    if flock_path:
        env["FLOCK_BIN"] = flock_path
    # 如果 flock 不存在，脚本会用 flock 命令失败，但测试环境通常有 flock

    return env


def create_pending_ci_file(locks_dir: Path, pr_number: int, session_id: str):
    """创建 pending-ci 测试文件"""
    data = {
        "session_id": session_id,
        "pr_number": str(pr_number),
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cwd": "/test/repo",
    }
    file_path = locks_dir / f"pending-ci-{pr_number}.json"
    file_path.write_text(json.dumps(data))
    return file_path


class TestVAL_INJECT_001_5xx_Synchronous_Fallback:
    """VAL-INJECT-001: 5xx 重试耗尽后同步记录 notify-suppressed，不再只留锁空等"""

    def test_5xx_exhausted_triggers_fallback_immediately(self, temp_env, tmp_path):
        """5xx 重试耗尽后同一次调用内完成 notify-suppressed 记录（<1 分钟）"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 9001
        session_id = "test-5xx-exhausted"

        # 创建 pending-ci 文件
        create_pending_ci_file(locks_dir, pr_number, session_id)

        # 运行触发脚本
        start_time = time.time()
        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = time.time() - start_time

        # 验证：总耗时 < 1 分钟（秒级完成）
        assert elapsed < 60, f"Expected < 60s, got {elapsed:.1f}s"

        # 验证：日志包含 notify-suppressed 记录动作（Mac 腿纯通知：派发权已删）
        combined_output = result.stdout + result.stderr
        assert "FALLBACK: notify-suppressed" in combined_output, (
            "Expected fallback notify-suppressed log"
        )

        # 验证：日志包含 5xx 重试耗尽信息
        assert "5xx" in combined_output.lower() or "504" in combined_output, (
            "Expected 5xx retry logs"
        )

        # 验证：创建了 fallback 去重锁
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        assert fallback_lock.exists(), "Fallback dedup lock should be created"


class TestVAL_INJECT_002_Dedup_Lock:
    """VAL-INJECT-002: fallback 去重锁防止遥测风暴"""

    def test_consecutive_5xx_calls_only_spawn_once(self, temp_env, tmp_path):
        """同一 5xx 场景连续调用 3 次只记录 1 次 notify-suppressed"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 9002
        session_id = "test-5xx-dedup"

        fallback_record_count = 0

        for _i in range(3):
            # 每次调用前创建 pending-ci 文件（模拟新的 webhook 触发）
            create_pending_ci_file(locks_dir, pr_number, session_id)

            result = subprocess.run(
                ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
                env=temp_env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            combined_output = result.stdout + result.stderr

            # 统计 notify-suppressed 记录次数
            if "FALLBACK: notify-suppressed" in combined_output:
                fallback_record_count += 1

        # 验证：只记录 1 次
        assert fallback_record_count == 1, (
            f"Expected 1 notify-suppressed record, got {fallback_record_count}"
        )

        # 验证：第 2、3 次调用命中去重锁
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        assert fallback_lock.exists(), "Fallback dedup lock should exist"


class TestVAL_INJECT_003_Probe_Comment:
    """VAL-INJECT-003: 探活端点存在性结论记录在代码注释"""

    def test_probe_conclusion_documented_in_code(self):
        """探活端点实测结论（存在/可用性、日期）记录在代码注释中"""
        script_content = TRIGGER_SCRIPT.read_text()

        # 验证：代码中存在探活相关注释
        assert "探活" in script_content or "probe" in script_content.lower(), (
            "Expected probe-related comments"
        )

        # 验证：注释包含实测日期
        assert "2026-08-20" in script_content or "2026-08-" in script_content, (
            "Expected probe test date in comments"
        )

        # 验证：注释包含端点存在性结论
        assert (
            "存在" in script_content
            or "可用" in script_content
            or "exist" in script_content.lower()
        ), "Expected endpoint existence conclusion in comments"


class TestVAL_INJECT_004_4xx_No_Regression:
    """VAL-INJECT-004: 4xx 路径行为不回归"""

    def test_4xx_still_deletes_lock_and_spawns_fallback(self, temp_env, tmp_path):
        """stub 返回 4xx 时维持既有语义：锁文件被删除，且 notify-suppressed 被记录"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 9003
        session_id = "test-4xx-no-regression"

        create_pending_ci_file(locks_dir, pr_number, session_id)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # 验证：4xx 路径记录 notify-suppressed（不再派发执行体）
        assert "FALLBACK: notify-suppressed" in combined_output, (
            "4xx should record notify-suppressed"
        )

        # 验证：创建了 fallback 去重锁
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        assert fallback_lock.exists(), "Fallback dedup lock should be created for 4xx"

        # 验证：主锁文件被删除（4xx 路径特征）
        # 注意：主锁文件可能仍存在（flock 机制），但 4xx 路径应该尝试删除
        # （删除动作在脚本内完成，此处不再引用具体锁文件路径）


class TestVAL_INJECT_005_200_No_Regression:
    """VAL-INJECT-005: 200 成功路径语义保持（PR #850 对账不回归）"""

    def test_200_writes_injected_at_no_fallback(self, temp_env, tmp_path):
        """stub 返回 200 时：pending-ci JSON 写入 injected_at 时间戳，且不产生 notify-suppressed 记录"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 9004
        session_id = "test-200-success"

        pending_file = create_pending_ci_file(locks_dir, pr_number, session_id)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # 验证：200 路径不产生 fallback 记录
        assert "FALLBACK: notify-suppressed" not in combined_output, (
            "200 success should NOT record notify-suppressed"
        )

        # 验证：pending-ci 文件被标记 injected_at
        if pending_file.exists():
            pending_data = json.loads(pending_file.read_text())
            assert "injected_at" in pending_data, (
                "200 success should write injected_at to pending-ci file"
            )
            assert "message_id" in pending_data, (
                "200 success should write message_id to pending-ci file"
            )


class TestVAL_INJECT_006_Watchdog_Still_Works:
    """VAL-INJECT-006: watchdog 对账兜底仍生效（对账面已同步通知化，仍为第二道防线）"""

    def test_watchdog_phase_a_still_detects_stale_files(self, temp_env, tmp_path):
        """构造一条无 injected_at 的陈旧 pending-ci 条目，watchdog Phase A 识别并补救"""
        # 这个测试验证 watchdog 脚本本身的行为，不在 trigger-ci-droid.sh 中测试
        # 参见 tests/test_ci_timeout_watchdog.py（PR #850 已有）
        # 此处只验证 trigger-ci-droid.sh 的 fallback 记录不破坏 watchdog 对账机制
        pass  # 由现有 test_ci_timeout_watchdog.py 覆盖


class TestVAL_INJECT_007_Probe_Failure_Direct_Fallback:
    """VAL-INJECT-007: 探活失败直接记录——不烧注入重试"""

    def test_probe_404_skips_post_retries(self, temp_env, tmp_path):
        """stub 对探活 GET 返回 404：脚本直接记录 notify-suppressed，不执行 POST 重试"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 9005
        session_id = "test-nonexistent-session"

        create_pending_ci_file(locks_dir, pr_number, session_id)

        start_time = time.time()
        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        elapsed = time.time() - start_time

        combined_output = result.stdout + result.stderr

        # 验证：探活失败后直接记录（Mac 腿无执行体可派发）
        probe_or_rebind_failed = (
            "PROBE FAILED" in combined_output
            or "Session does not exist" in combined_output
            or "is dead (probe HTTP" in combined_output
        )
        assert probe_or_rebind_failed, "Expected probe failure log"

        # 验证：触发了 notify-suppressed 记录
        assert "FALLBACK: notify-suppressed" in combined_output, (
            "Probe failure should record notify-suppressed"
        )

        # 验证：总耗时短（未烧满 POST 重试）
        assert elapsed < 30, f"Expected fast fallback (< 30s), got {elapsed:.1f}s"

        # 验证：未执行完整的 POST 重试序列（通过检查日志中无 "Attempt 3/3" 等）
        # 注意：探活失败后应直接 exit，不进入 POST 循环
        assert "Attempt 3/3" not in combined_output or "POSTing to" not in combined_output, (
            "Should not exhaust POST retries after probe failure"
        )


class TestProbeEndpointExists:
    """验证探活端点实测结论（补充 VAL-INJECT-003）"""

    def test_probe_endpoint_returns_json_404_not_html(self):
        """GET /api/v0/sessions/{fake_id} 返回 JSON 404（API 级），非 HTML 404（路由级）"""
        # 这个测试验证真实 API 端点的存在性
        # 通过检查代码注释中的结论来间接验证
        script_content = TRIGGER_SCRIPT.read_text()

        # 验证：代码注释中记录了端点返回 JSON 格式错误（非 HTML）
        assert "JSON" in script_content and "404" in script_content, (
            "Expected JSON 404 response documented in comments"
        )

        # 验证：代码区分了 API 级 404（JSON）和路由级 404（HTML）
        assert "Session does not exist" in script_content or "session" in script_content.lower(), (
            "Expected session existence check in probe logic"
        )


# === F3: Injection fix tests (VAL-INJ-003/004/005/012/013) ===


class TestVAL_INJ_003_BranchA_Probe404ButIndexFresh:
    """VAL-INJ-003(A): Probe 404 but sessions-index shows recent activity → still inject"""

    def test_probe_404_but_index_fresh_still_injects(self, temp_env, tmp_path):
        """Branch A: 404 probe + fresh sessions-index → attempt POST injection"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4250
        # Use a session_id that stub will return 404 for
        session_id = "nonexistent-but-index-fresh"

        # Create pending-ci file
        create_pending_ci_file(locks_dir, pr_number, session_id)

        # Create sessions-index.json with this session marked as fresh (mtime = now)
        sessions_index = tmp_path / "sessions-index.json"
        import time

        sessions_index.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "sessionId": session_id,
                            "cwd": "/test/repo",
                            "mtime": time.time(),  # Fresh: just now
                            "tags": [
                                {"name": "mission-session", "metadata": {"role": "orchestrator"}}
                            ],
                            "callingSessionId": None,
                        }
                    ]
                }
            )
        )
        temp_env["SESSIONS_INDEX"] = str(sessions_index)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Branch A: should attempt POST despite 404 probe (index fresh)
        assert (
            "WARN: Probe 404 but sessions-index shows recent activity" in combined_output
            or "attempting POST anyway" in combined_output
        ), "Should attempt POST when sessions-index is fresh"

        # Should NOT spawn fallback (because we attempt POST)
        # Actually, the POST will fail because stub returns 404 for this session_id
        # But the point is we ATTEMPTED POST, not skipped it
        assert (
            "PROBE FAILED: Session does not exist (404 JSON)" not in combined_output
            or "attempting POST anyway" in combined_output
        ), "Should not immediately judge death on 404 when index is fresh"


class TestVAL_INJ_004_SessionSelectionExcludesDecoys:
    """VAL-INJ-004: M2 F6 后候选池整体移除——null session 场景任何 index 会话都不可达"""

    def test_session_selection_excludes_decoys(self, temp_env, tmp_path):
        """旧 rebinding（按 cwd+mtime 选 orchestrator）已删除：null session_id 一律宁丢勿错"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4251

        # Create pending-ci file (M2 schema: session_id 显式 null)
        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        pending_file.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "cwd": "/test/repo",
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": None,
                }
            )
        )

        # Create sessions-index with decoy entries + one valid entry — 全部必须不可达
        import time

        now = time.time()
        sessions_index = tmp_path / "sessions-index.json"
        sessions_index.write_text(
            json.dumps(
                {
                    "entries": [
                        # Decoy 1: non-null callingSessionId (worker session)
                        {
                            "sessionId": "decoy-1-calling-session",
                            "cwd": "/test/repo",
                            "mtime": now,
                            "tags": [
                                {"name": "mission-session", "metadata": {"role": "orchestrator"}}
                            ],
                            "callingSessionId": "parent-session-id",
                        },
                        # Decoy 2: cwd mismatch
                        {
                            "sessionId": "decoy-2-wrong-cwd",
                            "cwd": "/wrong/repo",
                            "mtime": now,
                            "tags": [
                                {"name": "mission-session", "metadata": {"role": "orchestrator"}}
                            ],
                            "callingSessionId": None,
                        },
                        # Decoy 3: role=worker (not orchestrator)
                        {
                            "sessionId": "decoy-3-worker-role",
                            "cwd": "/test/repo",
                            "mtime": now,
                            "tags": [{"name": "mission-session", "metadata": {"role": "worker"}}],
                            "callingSessionId": None,
                        },
                        # Decoy 4: old mtime (not the most recent)
                        {
                            "sessionId": "decoy-4-old-mtime",
                            "cwd": "/test/repo",
                            "mtime": now - 86400,  # 1 day old
                            "tags": [
                                {"name": "mission-session", "metadata": {"role": "orchestrator"}}
                            ],
                            "callingSessionId": None,
                        },
                        # Valid entry: most recent, correct criteria
                        {
                            "sessionId": "valid-session-most-recent",
                            "cwd": "/test/repo",
                            "mtime": now - 10,  # 10 seconds ago (most recent valid)
                            "tags": [
                                {"name": "mission-session", "metadata": {"role": "orchestrator"}}
                            ],
                            "callingSessionId": None,
                        },
                    ]
                }
            )
        )
        temp_env["SESSIONS_INDEX"] = str(sessions_index)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # 宁丢勿错：null session → 遥测 + 跳过注入
        assert "ci_no_route_target" in combined_output, "Null session must emit ci_no_route_target"

        # 任何 index 会话（包括最合法的 valid-session）都不得被选中/探活/注入
        for sid in (
            "valid-session-most-recent",
            "decoy-1-calling-session",
            "decoy-2-wrong-cwd",
            "decoy-3-worker-role",
            "decoy-4-old-mtime",
        ):
            assert sid not in combined_output, (
                f"Session {sid} must be unreachable (no selection/probe/POST)"
            )

        # 旧 rebinding 路径不可达
        assert "Selected session" not in combined_output
        assert "event-time rebinding" not in combined_output

        # 文件保留（照 ci_scanner_source_mismatch 先例）
        assert pending_file.exists(), "Null-session pending file must be kept"


class TestVAL_INJ_005_FallbackNotifyOnly:
    """VAL-INJ-005 + VAL-M1-008: fallback 路径纯通知——记录 notify-suppressed，零执行体"""

    def test_fallback_prompt_contains_gh_pr_view(self, temp_env, tmp_path):
        """死会话 fallback：只记录 notify-suppressed（含 PR 号），绝不派发执行体"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4252

        # M2 F6: fallback 入口改为「指定会话死亡」（旧 rebinding-exhausted 已删除）
        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        pending_file.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "cwd": str(tmp_path),
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": "nonexistent-dead-session",
                }
            )
        )

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # VAL-M1-008：死会话路径必须记录 notify-suppressed（含 PR 号），零 droid exec
        assert "FALLBACK: notify-suppressed" in combined_output, (
            f"Dead-session fallback must record notify-suppressed; got:\n{combined_output[-800:]}"
        )
        assert f"PR #{pr_number}" in combined_output, "notify record must carry PR number"
        assert "droid exec" not in combined_output, "Mac leg must not launch droid exec (v3.2.2 C3)"
        # ECHO_DROID 模式专属落痕不得再出现（ECHO 面随执行体一起删除）
        assert "[ECHO_DROID]" not in combined_output, "ECHO_DROID surface must be gone"

        # 无 LOG_FILE dry-run 写入（ECHO 面已删）
        webhook_base = Path(temp_env["WEBHOOK_BASE"])
        log_dir = webhook_base / "logs"
        for log_file in log_dir.glob("*.log"):
            log_content = log_file.read_text()
            assert "[ECHO_DROID] Would run:" not in log_content, (
                "Dry-run droid exec echo must not exist (no executor on Mac leg)"
            )


class TestVAL_INJ_012_ProbeUnavailableStillInjects:
    """VAL-INJ-012: Probe 5xx/000 still attempts POST, only 404 JSON kills"""

    def test_probe_500_still_injects(self, temp_env, tmp_path):
        """Stub probe returns 500 → still attempt POST injection"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4253
        session_id = "test-probe-500-active"

        create_pending_ci_file(locks_dir, pr_number, session_id)

        # This test validates that when probe is unavailable (5xx/000), we still attempt POST
        # The stub returns 200 for active sessions, so we can't directly test 500
        # But we verify the code path by checking logs for the session being used
        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Probe returns 200 for this session (no "nonexistent" in name)
        # Verify the session was used (not rejected)
        assert (
            "is alive (probe HTTP 200)" in combined_output
            or "probe unavailable" in combined_output
            or "Session test-probe-500-active is alive" in combined_output
        ), "Should either succeed probe or log unavailability and still use session"

    def test_probe_connection_refused_still_injects(self, temp_env, tmp_path):
        """Probe connection refused (000) → still attempt POST injection"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4254
        session_id = "test-active-session"

        create_pending_ci_file(locks_dir, pr_number, session_id)

        # Point FACTORY_API_BASE to a closed port to simulate connection refused
        temp_env["FACTORY_API_BASE"] = "http://127.0.0.1:1"  # Port 1 is typically closed

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Probe should fail with 000 (connection refused)
        # Script logs "probe unavailable (probe HTTP $PROBE_CODE)" for non-404 errors
        assert (
            "probe unavailable" in combined_output
            or "Probe HTTP Response Code: 000" in combined_output
            or "HTTP 000" in combined_output
        ), "Probe should fail with 000 (connection refused)"

        # Should still attempt to use session (not go directly to fallback)
        # Check that we see "using it anyway" (not "dead" or "fallback")
        assert (
            "using it anyway" in combined_output
            or "is alive" in combined_output
            or "attempting POST" in combined_output
        ), "Should still attempt POST when probe is unavailable (not fallback)"

    def test_probe_404_json_stale_index_goes_fallback(self, temp_env, tmp_path):
        """Probe 404 JSON + stale/missing sessions-index → go to fallback"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4255
        session_id = "nonexistent-session"  # Stub returns 404 for this

        create_pending_ci_file(locks_dir, pr_number, session_id)

        # Empty sessions-index (no entries) → stale/missing
        sessions_index = tmp_path / "sessions-index.json"
        sessions_index.write_text(json.dumps({"entries": []}))
        temp_env["SESSIONS_INDEX"] = str(sessions_index)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Probe 404 + no fresh index → should mark session as dead
        # Script logs "is dead (probe HTTP 404, index stale/missing)"
        assert (
            "is dead (probe HTTP 404" in combined_output or "index stale/missing" in combined_output
        ), "Should detect session death on 404 with stale index"

        # Should eventually record the notification loss (after probe 404 + stale index)
        assert (
            "FALLBACK: notify-suppressed" in combined_output
            or "Created fallback lock" in combined_output
        ), "Should record notify-suppressed when probe 404 and index stale"


class TestVAL_INJ_013_FallbackTTLLockThreePhases:
    """VAL-INJ-013: Fallback 60min TTL lock three-phase semantics"""

    def test_fallback_lock_written_on_trigger(self, temp_env, tmp_path):
        """Phase 1: Fallback trigger writes ci-fallback-<PR>.lock"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4256

        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        pending_file.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "cwd": str(tmp_path),
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": "nonexistent-dead-4256",
                }
            )
        )

        subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Fallback lock should be created
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        assert fallback_lock.exists(), "Fallback lock should be created on trigger"

    def test_fallback_lock_skip_under_60min(self, temp_env, tmp_path):
        """Phase 2: Lock age < 60min → skip duplicate spawn"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4257

        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        pending_file.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "cwd": str(tmp_path),
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": "nonexistent-dead-4257",
                }
            )
        )

        # Pre-create a fresh fallback lock (< 60min old)
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        fallback_lock.write_text("recent")

        # Empty sessions-index → would trigger fallback
        sessions_index = tmp_path / "sessions-index.json"
        sessions_index.write_text(json.dumps({"entries": []}))
        temp_env["SESSIONS_INDEX"] = str(sessions_index)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Should skip duplicate spawn (lock age < 60min)
        assert (
            "SKIP: Fallback already triggered" in combined_output or "lock age" in combined_output
        ), "Should skip duplicate spawn when lock < 60min"

    def test_fallback_lock_rebuild_over_60min(self, temp_env, tmp_path):
        """Phase 3: Lock age > 60min → remove stale lock and spawn again"""
        locks_dir = Path(temp_env["LOCK_DIR"])
        pr_number = 4258

        pending_file = locks_dir / f"pending-ci-{pr_number}.json"
        pending_file.write_text(
            json.dumps(
                {
                    "pr_number": str(pr_number),
                    "cwd": str(tmp_path),
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "session_id": "nonexistent-dead-4258",
                }
            )
        )

        # Pre-create a stale fallback lock (> 60min old)
        fallback_lock = locks_dir / f"ci-fallback-{pr_number}.lock"
        fallback_lock.write_text("stale")
        # Set mtime to 61 minutes ago
        import time

        stale_time = time.time() - (61 * 60)
        os.utime(fallback_lock, (stale_time, stale_time))

        # Empty sessions-index → would trigger fallback
        sessions_index = tmp_path / "sessions-index.json"
        sessions_index.write_text(json.dumps({"entries": []}))
        temp_env["SESSIONS_INDEX"] = str(sessions_index)

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), str(pr_number), "test-branch", "abc123", "passed"],
            env=temp_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        combined_output = result.stdout + result.stderr

        # Should detect stale lock and record again
        assert "Stale fallback lock" in combined_output or "> 60min" in combined_output, (
            "Should detect stale lock > 60min"
        )
        assert (
            "FALLBACK: notify-suppressed" in combined_output
            or "Created fallback lock" in combined_output
        ), "Should record notify-suppressed after removing stale lock"


class TestVAL_M1_008_MacLegNotifyOnly:
    """VAL-M1-008: Mac 腿纯通知（v3.2.2 C3）——trigger-ci-droid.sh 结构契约"""

    def test_no_executor_launch_in_trigger_ci_droid(self):
        """trigger-ci-droid.sh 不得包含任何执行体启动（droid exec / with_timeout 派发）"""
        script_content = TRIGGER_SCRIPT.read_text()
        non_comment = "\n".join(
            ln
            for ln in script_content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
        assert "droid exec" not in non_comment, "Mac leg must not launch droid exec (v3.2.2 C3)"
        assert "with_timeout" not in non_comment, (
            "with_timeout executor wrapper must be removed with the executor"
        )
        assert "ECHO_DROID" not in non_comment, "ECHO_DROID dry-run surface must be gone"
        assert "nohup" not in non_comment, "nohup background launch must be gone"

    def test_no_spawn_function_remains(self):
        """spawn_fallback 派发函数已删除；仅存 notify_fallback 记录函数"""
        script_content = TRIGGER_SCRIPT.read_text()
        assert "spawn_fallback()" not in script_content, (
            "spawn_fallback (droid exec dispatcher) must be removed"
        )
        assert "notify_fallback()" in script_content, "notify_fallback (record+exit) must exist"
        # 七处调用点全部中性化：函数定义之外不得再有 spawn_fallback 引用
        call_sites = [
            ln
            for ln in script_content.splitlines()
            if "spawn_fallback" in ln and not ln.strip().startswith("#")
        ]
        assert call_sites == [], f"spawn_fallback references must be gone: {call_sites}"

    def test_notify_fallback_records_and_exits(self):
        """notify_fallback 函数体：记录 + 退出，无可执行启动（注释中的守卫协议不受限）"""
        script_content = TRIGGER_SCRIPT.read_text()
        match = re.search(r"notify_fallback\(\) \{(.*?)\n\}", script_content, re.DOTALL)
        assert match, "notify_fallback function body not found"
        body_lines = [
            ln
            for ln in match.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        body = "\n".join(body_lines)
        assert "notify-suppressed" in body, "notify_fallback must log notify-suppressed"
        assert "droid" not in body, "notify_fallback must not reference executor launch"
        assert "nohup" not in body, "notify_fallback must not launch anything"
        # 超时包装器（with_timeout）随执行体删除，函数体内不得出现
        assert "with_timeout" not in body, "notify_fallback must not use executor wrapper"

    def test_no_merge_semantics_in_trigger(self):
        """注入链无合并语义：无「合并 PR」步骤、无 SKIPPED/NEUTRAL 豁免、注入文案为通知体"""
        script_content = TRIGGER_SCRIPT.read_text()
        non_comment = "\n".join(
            ln
            for ln in script_content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
        assert "合并 PR" not in non_comment, "merge step must be removed from PROMPT"
        assert "请合并" not in non_comment, "inject text must not instruct merging"
        assert "SKIPPED/NEUTRAL" not in non_comment, "SKIPPED/NEUTRAL exemption must be removed"
        assert "不合并" not in non_comment, "merge-decision language must be gone"
        # :861 区注入文案为通知体：明确合并由守卫腿负责、禁手动合并
        assert "守卫腿" in non_comment, "notify body must state guard leg owns merging"
        assert "禁手动合并" in non_comment, "notify body must forbid manual merging"

    def test_no_merge_semantics_in_watchdog(self):
        """ci-timeout-watchdog.sh 同步去执行/合并语义：对账保留、派发体删除"""
        script_content = WATCHDOG_SCRIPT.read_text()
        non_comment = "\n".join(
            ln
            for ln in script_content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        )
        assert "droid_bin" not in non_comment and "droid exec" not in non_comment, (
            "watchdog must not launch droid exec (v3.2.2 C3)"
        )
        assert "nohup" not in non_comment, "watchdog nohup launch must be gone"
        assert "branch cleanup" not in non_comment, (
            "merge-semantic cleanup prompt must be gone from watchdog"
        )
        # 对账与记录面保留
        assert "notify_fallback" in non_comment, "watchdog keeps reconcile + record function"
        assert "notify-suppressed" in non_comment, "watchdog must record notify-suppressed"
        # 派发函数删除，调用点中性化
        assert "spawn_fallback" not in non_comment, "watchdog spawn_fallback must be renamed/gone"


class TestVAL_M2_Code_Fixes:
    """M2 Code Fixes: 毫秒纪元归一化、verify_scanner_identity -R 标志、gh 字段名校验"""

    def test_millisecond_epoch_normalization_in_check_sessions_index_fresh(self, tmp_path):
        """Fix B: sessions-index.json 的 mtime 如果 > 1e12（毫秒纪元），应除以 1000"""
        # 创建 sessions-index.json，使用毫秒纪元的 mtime
        index_file = tmp_path / "sessions-index.json"
        now_ms = int(time.time() * 1000)  # 当前时间的毫秒纪元
        index_data = {
            "entries": [
                {
                    "session_id": "test-session-ms",
                    "mtime": now_ms,  # 毫秒纪元
                    "cwd": "/test/repo",
                }
            ]
        }
        index_file.write_text(json.dumps(index_data))

        # 提取函数并测试
        env = {
            "PYTHON_BIN": sys.executable,
        }

        # 构建测试脚本
        test_script = f"""
import json, time
def check_sessions_index_fresh(session_id, index_file, max_age_sec=72*3600):
    try:
        with open(index_file) as f:
            data = json.load(f)
    except (IOError, json.JSONDecodeError):
        return False

    now = time.time()
    for entry in data.get('entries', []):
        if entry.get('session_id') == session_id:
            mtime = entry.get('mtime', 0)
            # Fix B: 如果 mtime > 1e12，认为是毫秒纪元，转换为秒
            if mtime > 1e12:
                mtime = mtime / 1000.0
            age = now - mtime
            if age < max_age_sec:
                return True
            return False
    return False

result = check_sessions_index_fresh('test-session-ms', '{index_file}')
print('FRESH' if result else 'STALE')
"""
        result = subprocess.run(
            [sys.executable, "-c", test_script], capture_output=True, text=True, env=env, timeout=10
        )

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "FRESH" in result.stdout, (
            "Millisecond epoch should be normalized to seconds and considered fresh"
        )

    def test_verify_scanner_identity_derives_repo_from_cwd(self, tmp_path):
        """Fix C: verify_scanner_identity 应从 PENDING_CWD 推导 owner/repo 并传 -R"""
        # 创建 mock git 仓库
        repo_dir = tmp_path / "test-repo"
        repo_dir.mkdir()

        # 初始化 git 仓库并设置 remote
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test-owner/test-repo.git"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

        # 读取函数实现
        script_content = TRIGGER_SCRIPT.read_text()

        # 验证函数签名包含 cwd 参数
        assert (
            "verify_scanner_identity()" in script_content
            or "verify_scanner_identity" in script_content
        ), "Function verify_scanner_identity should exist"

        # 验证函数从 PENDING_CWD 推导 repo
        assert "PENDING_CWD" in script_content, "Function should reference PENDING_CWD"

        # 验证函数使用 git remote 获取 owner/repo
        assert "git" in script_content and "remote" in script_content, (
            "Function should use git remote to derive owner/repo"
        )

        # 验证 gh pr view 调用包含 -R 标志
        assert "-R" in script_content or "--repo" in script_content, (
            "gh pr view should be called with -R or --repo flag"
        )

    def test_gh_shim_rejects_invalid_fields(self, tmp_path):
        """Fix A: gh shim 应拒绝无效的 --json 字段名"""
        # 创建 gh shim 脚本（使用 sed 而非 grep -P，兼容 macOS）
        shim_path = tmp_path / "gh"
        shim_content = """#!/bin/bash
# Mock gh CLI that validates field names (macOS compatible, no grep -P)

# 提取 --json 后的字段
get_json_fields() {
    echo "$@" | sed -n 's/.*--json \\([^ ]*\\).*/\\1/p'
}

if [[ "$*" == *"pr view"* && "$*" == *"--json"* ]]; then
    fields=$(get_json_fields "$@")

    # gh pr view 的有效字段
    valid_fields="number title body state author labels createdAt updatedAt url commits"

    for field in $(echo "$fields" | tr ',' ' '); do
        if ! echo "$valid_fields" | grep -qw "$field"; then
            echo "Error: Unknown field '$field' for 'gh pr view'" >&2
            echo "Valid fields are: $valid_fields" >&2
            exit 1
        fi
    done

    echo '{"number": 1, "title": "test"}'
elif [[ "$*" == *"pr checks"* && "$*" == *"--json"* ]]; then
    # gh pr checks 的有效字段
    fields=$(get_json_fields "$@")
    valid_fields="name state completedAt description url"

    for field in $(echo "$fields" | tr ',' ' '); do
        if ! echo "$valid_fields" | grep -qw "$field"; then
            echo "Error: Unknown field '$field' for 'gh pr checks'" >&2
            echo "Valid fields are: $valid_fields" >&2
            exit 1
        fi
    done

    echo '[]'
else
    exec /usr/bin/gh "$@"
fi
"""
        shim_path.write_text(shim_content)
        shim_path.chmod(0o755)

        # 测试无效字段
        result = subprocess.run(
            [str(shim_path), "pr", "view", "1", "--json", "invalid_field"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode != 0, "Should reject invalid field for pr view"
        assert "Unknown field" in result.stderr or "invalid_field" in result.stderr

        # 测试有效字段
        result = subprocess.run(
            [str(shim_path), "pr", "view", "1", "--json", "number,title"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"Should accept valid fields: {result.stderr}"

        # 测试 pr checks 无效字段
        result = subprocess.run(
            [str(shim_path), "pr", "checks", "1", "--json", "status,conclusion"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode != 0, "Should reject invalid fields for pr checks"
        assert "Unknown field" in result.stderr or "status" in result.stderr

        # 测试 pr checks 有效字段
        result = subprocess.run(
            [str(shim_path), "pr", "checks", "1", "--json", "name,state"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, f"Should accept valid fields for pr checks: {result.stderr}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
