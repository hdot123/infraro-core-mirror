"""INFRA-521 + M5 + M2: write-pending-ci.sh 回归测试。

M3 硬化语义（PR #978）：
- 原子写入 pending-ci JSON（tmp+mv，无 .tmp 残留）

M5 事件时重绑定（PR #986，已被 M2 取代）：
- 会话探活与选择职责迁移至 trigger-ci-droid.sh

M2 发起者绑定（rt-session-id-persist）：
- write-pending-ci.sh 写 {pr_number, cwd, created_at, session_id}：
  --session-id <id> 显式参数 → 字符串；未传 → 显式 null
- 分层捕获：显式参数 > 进程血缘自动探测 > null + PostHog
  ci_session_id_unresolved。血缘探测经 2026-10-02 实测结构性不可行
  （droid 是共享 daemon、无 pid→session 注册表），故 null 是未传参时的
  唯一确定行为——绝不按 sessions 目录 mtime 猜测（那是 Bug#002 错投根因）
- VAL-RT-001: --session-id roundtrip
- VAL-RT-002: null + ci_session_id_unresolved 遥测（dry-run 可断言）

测试用 stub HTTP server 模拟 Factory Sessions API（沿用
test_trigger_ci_droid_fallback.py 的隔离模式），HOME 重定向到临时目录，
严禁触碰生产 ``~/.factory`` 路径。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from tests.stub_api_helpers import run_stub_api_server

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "webhook-scripts" / "write-pending-ci.sh"


class StubSessionsAPIHandler(BaseHTTPRequestHandler):
    """Stub Factory Sessions API。

    会话 ID 编码控制探活结果：
    - 含 "nonexistent" → 404（会话不存在）
    - 含 "unreachable" → 500（API 故障）
    - 其他 → 200（会话存活）
    """

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler 签名
        pass

    def do_GET(self):
        path = self.path.removeprefix("/api/v0")
        if path.startswith("/sessions/") and not path.endswith("/messages"):
            session_id = path.split("/")[-1]
            if "nonexistent" in session_id:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"detail": "Session does not exist", "status": 404}).encode()
                )
                return
            if "unreachable" in session_id:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"detail": "internal error"}).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "id": session_id,
                        "status": "active",
                        "createdAt": datetime.now(UTC).isoformat(),
                    }
                ).encode()
            )
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def stub_api_server():
    yield from run_stub_api_server(StubSessionsAPIHandler, url_prefix="/api/v0")


def _run_script(script: Path, *args: str, env: dict) -> tuple[int, str, str]:
    result = subprocess.run(
        ["bash", str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=env.get("_TEST_CWD", str(REPO_ROOT)),
    )
    return result.returncode, result.stdout, result.stderr


def _make_env(stub_base: str, tmp_path: Path, *, cwd: Path | None = None) -> dict:
    """构造隔离环境：HOME 重定向 + stub API + dry-run PostHog。

    - HOME → tmp_path/home（LOCKS_DIR / SESSIONS_INDEX 全部指向沙箱）
    - FACTORY_API_BASE → stub server
    - FACTORY_TOKEN → 动态拼接假值（仅满足 ^fk- 前缀校验；stub 不验证真实性，
      禁止在源码中出现完整 token 字面量）
    - POSTHOG_DRY_RUN=1 → 不发送真实分析事件
    """
    home = tmp_path / "home"
    (home / ".factory" / "webhook" / "locks").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["FACTORY_API_BASE"] = stub_base
    env["FACTORY_TOKEN"] = "fk-" + "test-stub-" + "not-real"
    env["POSTHOG_DRY_RUN"] = "1"
    env["PYTHON_BIN"] = sys.executable
    if cwd is not None:
        env["_TEST_CWD"] = str(cwd)
    return env


def _init_sandbox_repo(tmp_path: Path) -> tuple[Path, str]:
    """初始化沙箱 git 仓库，返回 (repo_path, toplevel)。

    PROJECT_CWD 探测依赖 git rev-parse --show-toplevel；macOS 上该命令返回
    解析符号链接后的路径（/private/var/...），与 pytest tmp_path 的字面值
    （/var/folders/...）不同，sessions-index 的 cwd 必须使用 toplevel 原值。
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True, text=True
    )
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, toplevel


def _write_sessions_index(
    home: Path, entries: list[dict], default_cwd: str = "/sandbox/repo"
) -> None:
    """写入沙箱 sessions-index.json。"""
    payload = {
        "entries": [
            {
                "sessionId": e["sessionId"],
                "cwd": e.get("cwd", default_cwd),
                "mtime": e["mtime"],
                "callingSessionId": e.get("callingSessionId"),
                "tags": [
                    {"name": "mission-session", "metadata": {"role": e.get("role", "orchestrator")}}
                ],
            }
            for e in entries
        ]
    }
    index_path = home / ".factory" / "sessions-index.json"
    index_path.write_text(json.dumps(payload))


def _read_pending(home: Path, pr_number: int) -> dict | None:
    f = home / ".factory" / "webhook" / "locks" / f"pending-ci-{pr_number}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


# ============================================================================
# VAL-WPC-001: M5→M2 write-pending-ci.sh 基础功能（session_id 分层新语义）
# ============================================================================
class TestM5BasicWrite:
    def test_write_pending_without_session_id(self, stub_api_server, tmp_path):
        """M2: 不传 --session-id → 写显式 null（探测不可行，绝不 mtime 猜测）。"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "9103", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 9103)
        assert pending is not None
        # M2 schema: session_id 键存在且为显式 null（探测降级 ③）
        assert "session_id" in pending, "M2 schema must contain session_id key"
        assert pending["session_id"] is None
        assert pending["pr_number"] == "9103"
        assert pending["created_at"]
        assert pending["cwd"] == toplevel

    def test_write_pending_schema_fields(self, stub_api_server, tmp_path):
        """M2: pending 文件恰包含 pr_number, cwd, created_at, session_id 四字段。"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "9104", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 9104)
        assert pending is not None
        assert set(pending.keys()) == {"pr_number", "cwd", "created_at", "session_id"}
        assert pending["session_id"] is None

    def test_no_api_probe_in_write(self, stub_api_server, tmp_path):
        """M5 沿革：write-pending-ci.sh 不探测 Sessions API（职责在 trigger 侧）。"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        # 即使不设置 FACTORY_TOKEN，脚本也应成功（因为不需要探测 API）
        env.pop("FACTORY_TOKEN", None)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "9105", env=env)
        assert code == 0, f"Expected success without token, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 9105)
        assert pending is not None
        assert pending["pr_number"] == "9105"


# ============================================================================
# VAL-WPC-002: sessions-index 候选迭代（已迁移至 trigger-ci-droid.sh）
# M5 变更：write-pending-ci.sh 不再负责 session 选择，此测试类保留但仅验证迁移完整性
# ============================================================================
class TestCandidateIteration:
    """M5 后此测试类不再适用。session 选择逻辑已迁移至 trigger-ci-droid.sh。

    原测试覆盖的 mtime 排序、worker 过滤、cwd 匹配等逻辑现在由
    test_trigger_ci_droid_fallback.py 覆盖（见 VAL-TRIGCI-003）。
    """

    pass


# ============================================================================
# VAL-WPC-003: 原子写入 — tmp+mv，成功后无 .tmp 残留、内容为合法 JSON
# ============================================================================
class TestAtomicWrite:
    def test_no_tmp_residue_and_valid_json(self, stub_api_server, tmp_path):
        """写入成功后 locks 目录无 .tmp.* 残留文件，pending 内容合法。"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, _, stderr = _run_script(SCRIPT_PATH, "9301", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        locks_dir = Path(env["HOME"]) / ".factory" / "webhook" / "locks"
        tmp_files = list(locks_dir.glob("pending-ci-9301.json.tmp.*"))
        assert not tmp_files, f"Atomic write left tmp residue: {tmp_files}"
        pending = _read_pending(Path(env["HOME"]), 9301)
        assert pending is not None
        # M2 schema: pr_number, cwd, created_at, session_id
        assert set(pending) == {"pr_number", "cwd", "created_at", "session_id"}


# ============================================================================
# VAL-WPC-004: 参数与环境守卫
# ============================================================================
class TestGuards:
    def test_missing_pr_number_usage_error(self, stub_api_server, tmp_path):
        """缺 PR_NUMBER 参数：usage 报错非 0 退出。"""
        env = _make_env(stub_api_server, tmp_path)
        code, stdout, stderr = _run_script(SCRIPT_PATH, env=env)
        assert code != 0
        assert "Usage" in stdout or "Usage" in stderr

    def test_not_a_git_repo_fails(self, stub_api_server, tmp_path):
        """不在 git 仓库：报错退出。"""
        plain = tmp_path / "plain"
        plain.mkdir()
        env = _make_env(stub_api_server, tmp_path, cwd=plain)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "9401", env=env)
        combined = stdout + stderr
        assert code != 0
        assert "Not in a git repository" in combined or "git repository" in combined.lower()

    def test_invalid_pr_number_rejected(self, stub_api_server, tmp_path):
        """非法 PR_NUMBER（0、负数、非数字）应被拒绝。"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        for bad_pr in ["0", "-1", "abc", "", "1.5"]:
            code, stdout, stderr = _run_script(SCRIPT_PATH, bad_pr, env=env)
            assert code != 0, f"Expected non-zero exit for PR_NUMBER={bad_pr!r}"
            assert _read_pending(Path(env["HOME"]), int(bad_pr) if bad_pr.isdigit() else 0) is None


# ============================================================================
# M5 迁移说明：TestMtimeScanProbeRC 和 TestCandidateIteration 已废弃
# ============================================================================
# 原 BLK-M3-R1-1 probe rc 捕获测试和 TestCandidateIteration 的 mtime 扫描逻辑
# 已迁移至 trigger-ci-droid.sh（事件时重绑定）。
# 相关测试覆盖位于 tests/test_trigger_ci_droid_*.py
# 本文件仅保留 write-pending-ci.sh 的职责：写 {pr_number, cwd, created_at}


# ============================================================================
# VAL-REG-001: --source session --context 写全字段且保真
# ============================================================================
class TestSourceSessionContext:
    def test_source_session_context_roundtrip(self, stub_api_server, tmp_path):
        """VAL-REG-001: --source session --context 写全字段且 context 逐字符保真"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        test_context = "修复 \"权宜\" 之'计'并 `echo hi`"
        code, stdout, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", test_context, "4242", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4242)
        assert pending is not None
        # M5 三字段 + source + context
        assert pending["pr_number"] == "4242"
        assert pending["cwd"] == toplevel
        assert pending["created_at"]
        assert pending["source"] == "session"
        assert pending["context"] == test_context
        # M2 语义：未传 --session-id → 显式 null 键（不再是「无键」旧语义）
        assert "session_id" in pending
        assert pending["session_id"] is None
        # 无 .tmp 残留
        locks_dir = Path(env["HOME"]) / ".factory" / "webhook" / "locks"
        tmp_files = list(locks_dir.glob("pending-ci-4242.json.tmp.*"))
        assert not tmp_files


# ============================================================================
# VAL-REG-002: 旧式无参调用保持 M5 schema（缺 source 键）
# ============================================================================
class TestLegacyCallSchema:
    def test_legacy_call_no_source_key(self, stub_api_server, tmp_path):
        """VAL-REG-002: 不带选项调用，无 source 键、无 context 键（M2 起含显式 null session_id）"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "4243", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4243)
        assert pending is not None
        assert set(pending.keys()) == {"pr_number", "cwd", "created_at", "session_id"}
        assert pending["session_id"] is None


# ============================================================================
# VAL-REG-003: --source scanner 写入成功（D1）
# ============================================================================
class TestSourceScanner:
    def test_source_scanner_write_success(self, stub_api_server, tmp_path):
        """VAL-REG-003: --source scanner 写入成功，不得拒绝"""
        repo, toplevel = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "--source", "scanner", "4244", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4244)
        assert pending is not None
        assert pending["source"] == "scanner"
        assert pending["pr_number"] == "4244"
        assert pending["cwd"] == toplevel
        assert pending["created_at"]


# ============================================================================
# VAL-REG-004: 非法 source 值 fail-fast
# ============================================================================
class TestInvalidSource:
    def test_invalid_source_fail_fast(self, stub_api_server, tmp_path):
        """VAL-REG-004: 非法 source 值 fail-fast，不产生任何文件"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        # 变体 1: --source foo
        code1, stdout1, stderr1 = _run_script(SCRIPT_PATH, "--source", "foo", "4245", env=env)
        assert code1 != 0, "Expected non-zero exit for --source foo"
        assert "source" in (stdout1 + stderr1).lower() or "invalid" in (stdout1 + stderr1).lower()
        pending1 = _read_pending(Path(env["HOME"]), 4245)
        assert pending1 is None, "No file should be created for invalid source"

        # 变体 2: --source ""
        code2, stdout2, stderr2 = _run_script(SCRIPT_PATH, "--source", "", "4245", env=env)
        assert code2 != 0, "Expected non-zero exit for --source ''"
        pending2 = _read_pending(Path(env["HOME"]), 4245)
        assert pending2 is None, "No file should be created for empty source"


# ============================================================================
# VAL-RT-001: --session-id 显式参数 roundtrip
# ============================================================================
class TestSessionIdExplicit:
    def test_session_id_roundtrip(self, stub_api_server, tmp_path):
        """VAL-RT-001: --session-id <id> 逐字符落盘 roundtrip。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        sid = "f1e2d3c4-b5a6-9788-7969-5847-3625140fbdca"
        code, stdout, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--session-id", sid, "4201", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4201)
        assert pending is not None
        assert pending["session_id"] == sid
        assert pending["source"] == "session"
        assert pending["pr_number"] == "4201"

    def test_session_id_no_posthog_alert(self, stub_api_server, tmp_path):
        """显式传入 --session-id 时不得触发 ci_session_id_unresolved 告警。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(
            SCRIPT_PATH, "--session-id", "explicit-sid-4202", "4202", env=env
        )
        assert code == 0
        combined = stdout + stderr
        assert "ci_session_id_unresolved" not in combined
        pending = _read_pending(Path(env["HOME"]), 4202)
        assert pending["session_id"] == "explicit-sid-4202"

    def test_session_id_ignores_well_formed_session_env_shape(self, stub_api_server, tmp_path):
        """--session-id 无特殊字符约束放宽：UUID 与短串均按字面写入。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        cases = [("abc123", "4206"), ("4db22519-88c8-422a-8038-b201985ddb8d", "4207")]
        for sid, pr in cases:
            code, _, stderr = _run_script(SCRIPT_PATH, "--session-id", sid, pr, env=env)
            assert code == 0, f"Expected success for sid={sid!r}, stderr: {stderr}"
            pending = _read_pending(Path(env["HOME"]), int(pr))
            assert pending is not None
            assert pending["session_id"] == sid

    def test_session_id_whitespace_rejected(self, stub_api_server, tmp_path):
        """VAL-RT-001 负向：--session-id 含空白字符 fail-fast，不产生文件。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(
            SCRIPT_PATH, "--session-id", "bad sid with spaces", "4204", env=env
        )
        assert code != 0
        assert _read_pending(Path(env["HOME"]), 4204) is None


# ============================================================================
# VAL-RT-002: 分层捕获 null 路径 — 告警遥测 + 绝不 mtime 猜测
# ============================================================================
class TestSessionIdUnresolved:
    def test_null_with_telemetry_alert(self, stub_api_server, tmp_path):
        """VAL-RT-002: 未传 --session-id → session_id 显式 null + dry-run 遥测事件。

        POSTHOG_DRY_RUN=1 下 lib/posthog.sh 将事件打印到 stdout，
        据此断言 ci_session_id_unresolved 触发。
        """
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "4211", env=env)
        assert code == 0, f"Webhook-semantic success expected, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4211)
        assert pending is not None
        assert pending["session_id"] is None
        combined = stdout + stderr
        assert "ci_session_id_unresolved" in combined, (
            f"Expected ci_session_id_unresolved telemetry in dry-run output:\n{combined}"
        )

    def test_null_telemetry_carries_pr_number(self, stub_api_server, tmp_path):
        """VAL-RT-002: 遥测事件携带 PR 号 identifier，便于生产定位。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        code, stdout, stderr = _run_script(SCRIPT_PATH, "4212", env=env)
        assert code == 0
        combined = stdout + stderr
        dry_run_lines = [ln for ln in combined.splitlines() if "ci_session_id_unresolved" in ln]
        assert dry_run_lines, "telemetry line missing"
        assert "4212" in dry_run_lines[0], f"PR number not in telemetry line: {dry_run_lines[0]}"

    def test_no_mtime_guessing_decoy_sessions_untouched(self, stub_api_server, tmp_path):
        """VAL-RT-002 核心负向断言：绝不按 mtime 扫描猜测会话。

        沙箱 sessions-index.json 预置一个 mtime 更新的无关会话（Bug#002
        错投形态的诱饵）。未传 --session-id 时 write 侧不得选中/写入该
        会话 id——session_id 必须是 null。mtime 猜测正是 Bug#002 根因。
        """
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        _write_sessions_index(
            Path(env["HOME"]),
            [
                {
                    "sessionId": "decoy-newest-mtime-session",
                    "mtime": 9999999999999,  # 远未来 mtime 诱饵
                    "callingSessionId": None,
                    "role": "orchestrator",
                    "cwd": "/sandbox/repo",
                }
            ],
        )
        code, stdout, stderr = _run_script(SCRIPT_PATH, "4213", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4213)
        assert pending is not None
        assert pending["session_id"] is None, (
            "mtime-decoy session must NEVER be picked; null is the only "
            "deterministic no-flag behavior (Bug#002 regression guard)"
        )
        assert "decoy-newest-mtime-session" not in (stdout + stderr)

    def test_dry_run_telemetry_does_not_break_write(self, stub_api_server, tmp_path):
        """遥测是 fail-open 装饰：写文件成功才是主语义（webhook exit 0）。"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        env["POSTHOG_DRY_RUN"] = "1"
        env["POSTHOG_API_KEY"] = ""  # 无 key 分支也必须不阻断写入
        code, stdout, stderr = _run_script(SCRIPT_PATH, "4214", env=env)
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4214)
        assert pending is not None
        assert pending["session_id"] is None

    def test_flagged_write_never_scans_sessions_dir(self, stub_api_server, tmp_path):
        """传 --session-id 时直接用显式值：不读 sessions-index、不产生遥测。

        sessions-index.json 指向不存在文件时脚本仍须成功（探活职责在
        trigger 侧），证明 write 侧零探测依赖。
        """
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        env["SESSIONS_INDEX"] = str(Path(env["HOME"]) / ".factory" / "nonexistent-index.json")
        code, stdout, stderr = _run_script(
            SCRIPT_PATH, "--session-id", "given-directly-4215", "4215", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4215)
        assert pending is not None
        assert pending["session_id"] == "given-directly-4215"
        assert "ci_session_id_unresolved" not in (stdout + stderr)


# ============================================================================
# VAL-REG-017: --context 边界值鲁棒性
# ============================================================================
class TestContextBoundary:
    def test_context_boundary_quotes(self, stub_api_server, tmp_path):
        """VAL-REG-017a: 含单双引号与反引号"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        test_context = "修复 \"权宜\" 之'计'并 `echo hi`"
        code, _, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", test_context, "4246", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4246)
        assert pending is not None
        assert pending["context"] == test_context

    def test_context_boundary_special_chars(self, stub_api_server, tmp_path):
        """VAL-REG-017b: 特殊字符与中文混排"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        test_context = "特殊 $ ` \\ ! ; & | 字符与中文混排"
        code, _, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", test_context, "4247", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4247)
        assert pending is not None
        assert pending["context"] == test_context

    def test_context_boundary_long_string(self, stub_api_server, tmp_path):
        """VAL-REG-017c: ≥4000 字符"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        test_context = "A" * 4000
        code, _, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", test_context, "4248", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4248)
        assert pending is not None
        assert pending["context"] == test_context
        assert len(pending["context"]) == 4000

    def test_context_boundary_multiline(self, stub_api_server, tmp_path):
        """VAL-REG-017d: 多行串"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        test_context = "第一行\n第二行\n第三行"
        code, _, stderr = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", test_context, "4249", env=env
        )
        assert code == 0, f"Expected success, stderr: {stderr}"
        pending = _read_pending(Path(env["HOME"]), 4249)
        assert pending is not None
        assert pending["context"] == test_context


# ============================================================================
# VAL-REG-018: 同 PR 重复注册（并发/覆盖语义）
# ============================================================================
class TestDuplicateRegistration:
    def test_same_pr_duplicate_registration(self, stub_api_server, tmp_path):
        """VAL-REG-018: 同 PR 两次注册，last-write-wins 幂等"""
        repo, _ = _init_sandbox_repo(tmp_path)
        env = _make_env(stub_api_server, tmp_path, cwd=repo)
        # 第一次注册
        code1, _, _ = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", "第一次意图", "4250", env=env
        )
        assert code1 == 0
        pending1 = _read_pending(Path(env["HOME"]), 4250)
        assert pending1 is not None
        assert pending1["context"] == "第一次意图"
        created_at1 = pending1["created_at"]

        # 等待 1 秒确保 created_at 可区分
        import time

        time.sleep(1)

        # 第二次注册（不同 context）
        code2, _, _ = _run_script(
            SCRIPT_PATH, "--source", "session", "--context", "第二次意图", "4250", env=env
        )
        assert code2 == 0
        pending2 = _read_pending(Path(env["HOME"]), 4250)
        assert pending2 is not None
        assert pending2["context"] == "第二次意图"
        assert pending2["created_at"] > created_at1  # 覆盖为新记录

        # 恰一份合法 JSON（无副本）
        locks_dir = Path(env["HOME"]) / ".factory" / "webhook" / "locks"
        matching_files = list(locks_dir.glob("pending-ci-4250.json*"))
        assert len(matching_files) == 1, f"Expected exactly 1 file, found {len(matching_files)}"


# ============================================================================
# 生产一致性：受管副本与生产脚本字节一致（回填完整性快照）
# ============================================================================
class TestProdSync:
    PROD_SCRIPT = Path.home() / ".factory" / "webhook" / "scripts" / "write-pending-ci.sh"

    def test_managed_copy_matches_production(self):
        """受管副本与 ~/.factory 生产脚本一致（PR #978 回填基线）。

        生产脚本在 CI runner 上不存在 → skip（本地/mission 环境执行）。
        CI runner（自建 pve-linux 持久共享 HOME）上，droid mission 会按
        「创建 PR → 注册路由」纪律调用/重建 ~/.factory/webhook/scripts/
        下的脚本（2026-09-11 事故：mission 装入 pre-M5 旧版副本，导致
        main 与 PR #288 全量 CI 红）。runner 的 ~/.factory 是 mission
        沙箱而非生产 webhook 环境，漂移门禁只应作用于本地开发机；
        GITHUB_ACTIONS 在 GitHub Actions（含 self-hosted）恒为 true。
        """
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.skip(
                "drift gate targets local dev machines; CI runner HOME is a droid mission sandbox"
            )
        if not self.PROD_SCRIPT.exists():
            pytest.skip("production script not present on this machine")
        assert SCRIPT_PATH.read_bytes() == self.PROD_SCRIPT.read_bytes(), (
            "webhook-scripts/write-pending-ci.sh drifted from production; "
            "run webhook-scripts/sync-webhook-scripts.sh to reconcile"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
