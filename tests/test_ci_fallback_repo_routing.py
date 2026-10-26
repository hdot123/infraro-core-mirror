"""INFRA-701: CI fallback repo 路由回归测试

事故背景（2026-09-01，infra-core PR #136）：hooks.json 的 ci-complete 钩子只
透传 4 个参数（pr_number/branch/sha/status），CI payload 的 repo 字段被丢弃。
release-please PR #136 的 pending-ci 文件不存在（非 session 会话创建，无
write-pending-ci 注册），fallback 走 missing-pending-ci 路径，repo 解析退化为
webhook 接收器的 command-working-directory（/path/to/memory）——infra-core
的 PR #136 通知被误记到 memory 仓上下文（memory 仓 2026-07 的旧 PR #136）。

M1-D（v3.2.2 C3，VAL-M1-008）：Mac 腿纯通知——fallback 不再派发 droid exec，
只记录 notify-suppressed（repo 路由保留为审计观测面）；合并由守卫腿负责。

修复语义（fallback repo 选择优先级）：
1. PENDING_CWD —— pending-ci 文件携带的 cwd（发 PR 会话的确切仓库路径）
2. 显式 repo slug（CI payload repo 字段 → repositories.yml 反查本地路径）
3. SCRIPT_CWD —— webhook 接收器工作目录（最后兜底）

（解析结果仅用于 notify-suppressed 审计记录，不再派发执行体。）

测试隔离：WEBHOOK_BASE/LOCK_DIR/LOG_DIR
指向临时目录；不读写生产 ~/.factory/webhook。FACTORY_TOKEN 用动态拼接的假值
（FACTORY_API_BASE 指向闭端口，不会真注入）；FLOCK_BIN 按平台解析——脚本默认
路径 /opt/homebrew/bin/flock 仅 macOS 存在，Linux CI 上 flock 缺失（退出码 127）
会被幂等锁分支误判为 ci_lock_held，导致 fallback 未记录（INFRA-701 CI 首红根因）。
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
import yaml

from tests.pending_ci_helpers import create_pending_file as _create_pending_file
from tests.stub_api_helpers import run_stub_api_server

REPO_ROOT = Path(__file__).parent.parent
TRIGGER_SCRIPT = REPO_ROOT / "webhook-scripts" / "trigger-ci-droid.sh"


def _make_env(tmp_path: Path, repo_config: Path | None = None) -> dict:
    """构造测试环境变量（全部隔离到临时目录）"""
    env = os.environ.copy()

    webhook_base = tmp_path / "webhook"
    webhook_base.mkdir(exist_ok=True)
    env["WEBHOOK_BASE"] = str(webhook_base)

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir(exist_ok=True)
    env["LOCK_DIR"] = str(locks_dir)

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    env["LOG_DIR"] = str(log_dir)

    # 闭端口：注入路径必失败，强制走 fallback 分支
    env["FACTORY_API_BASE"] = "http://127.0.0.1:1"

    # 测试用 Factory token（动态拼接假值，仅满足脚本 ^fk- 前缀校验；闭端口不会真注入）。
    # 不设假值时脚本会走 1Password 读取（op-mcp.sh），非 macOS 环境必然失败并在
    # fallback 记录前 exit 0。禁止在源码中出现完整 token 字面量（Droid-Shield 密钥检测）。
    env["FACTORY_TOKEN"] = "fk-" + "test-stub-" + "not-real"

    # 跨平台 flock 路径（对齐 tests/test_trigger_ci_droid_fallback.py 的 temp_env）：
    # 脚本默认 /opt/homebrew/bin/flock 仅 macOS 存在
    flock_path = shutil.which("flock")
    if flock_path:
        env["FLOCK_BIN"] = flock_path

    env["ECHO_DROID"] = "1"
    env["POSTHOG_DRY_RUN"] = "1"
    env["PYTHON_BIN"] = sys.executable

    if repo_config is not None:
        env["REPO_CONFIG"] = str(repo_config)
    else:
        # 未提供测试配置时指向不存在的路径，切断对生产 repositories.yml 的依赖
        # （slug 解析必然失败，走 SCRIPT_CWD 兜底，行为确定）
        env["REPO_CONFIG"] = str(tmp_path / "repositories.yml.absent")

    # 404 豁免检查的 index 同样指向不存在的路径（fail-closed 无豁免，
    # 探活 404 确定走 fallback，不读生产 ~/.factory/sessions-index.json）
    env["SESSIONS_INDEX"] = str(tmp_path / "sessions-index.json.absent")

    # 剥离宿主机 CI_REPO（本仓库 CI 会话环境携带 CI_REPO=hdot123/infraro-core，
    # 会经 REPO_SLUG_ARG 的 ${CI_REPO:-} 兜底渗入 legacy 调用测试；需要 CI_REPO
    # 的用例在 _make_env 之后显式设置）
    env.pop("CI_REPO", None)

    return env


def _make_repo_config(tmp_path: Path, entries: list[dict]) -> Path:
    """生成测试用 repositories.yml（结构对齐生产配置）"""
    config = {
        "version": 1,
        "teams": {
            "infra": {
                "teamKey": "INFRA",
                "repos": entries,
            }
        },
    }
    config_path = tmp_path / "repositories.yml"
    config_path.write_text(yaml.dump(config))
    return config_path


def _read_latest_log(temp_env: dict) -> str:
    """读取脚本写入的最新日志文件内容（LOG_DIR 由脚本从 WEBHOOK_BASE 派生）"""
    webhook_base = Path(temp_env["WEBHOOK_BASE"])
    log_dir = webhook_base / "logs"
    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        return ""
    return log_files[-1].read_text()


class DeadSessionStubHandler(BaseHTTPRequestHandler):
    """探活恒 404 的 stub Sessions API（驱动死会话 fallback 记录路径，零外呼）"""

    def log_message(self, format, *args):
        """抑制 HTTP 请求日志"""

    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"detail": "Session does not exist", "status": 404}')

    def do_POST(self):
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"detail": "Session does not exist", "status": 404}')


@pytest.fixture(scope="module")
def dead_session_stub():
    """探活恒 404 的 stub server（M2：fallback 仅剩死会话单一入口；M1-D 起纯记录）"""
    yield from run_stub_api_server(DeadSessionStubHandler, url_prefix="/api/v0")


class TestFallbackRepoRouting:
    """INFRA-701: fallback repo 三级优先级路由"""

    def test_missing_pending_ci_routes_by_repo_slug(self, tmp_path):
        """missing-pending-ci 路径：第 5 参数 repo slug 经 repositories.yml 反查本地路径

        复现 #136 事故形态（无 pending 文件），验证 repo 参数不再退化为
        SCRIPT_CWD（事故中为 memory 仓）。
        """
        target_repo = tmp_path / "infra-core-like"
        target_repo.mkdir()
        repo_config = _make_repo_config(
            tmp_path,
            [
                {
                    "repoKey": "infra-core",
                    "repoPath": str(target_repo),
                    "githubRepo": "acme/infra-core",
                }
            ],
        )
        env = _make_env(tmp_path, repo_config)

        result = subprocess.run(
            [
                "bash",
                str(TRIGGER_SCRIPT),
                "136",  # 事故 PR 号
                "release-please--branches--main",
                "abc123",
                "success",
                "acme/infra-core",  # 第 5 参数：repo slug
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        # fallback 记录产生
        assert "FALLBACK: notify-suppressed" in combined, "Expected fallback notify record"
        # 路由到 slug 解析的本地路径，而非脚本 CWD
        assert (
            f"FALLBACK: notify-suppressed for PR #136 (status: success, repo: {target_repo})"
            in combined
        ), f"Expected fallback repo {target_repo}, got:\n{combined[-800:]}"
        # 显式路由日志
        assert "routed by CI payload repo slug" in combined

    def test_missing_pending_ci_ci_repo_env_fallback(self, tmp_path):
        """第 5 参数缺省时 CI_REPO 环境变量兜底（hooks.json 双通道的 env 通道）"""
        target_repo = tmp_path / "via-env"
        target_repo.mkdir()
        repo_config = _make_repo_config(
            tmp_path,
            [{"repoKey": "via-env", "repoPath": str(target_repo), "githubRepo": "acme/via-env"}],
        )
        env = _make_env(tmp_path, repo_config)
        env["CI_REPO"] = "acme/via-env"

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "200", "b", "abc", "success"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        assert "FALLBACK: notify-suppressed" in combined
        assert f"(status: success, repo: {target_repo})" in combined, (
            f"Expected env-channel routing to {target_repo}, got:\n{combined[-800:]}"
        )

    def test_pending_cwd_wins_over_repo_slug(self, tmp_path, dead_session_stub):
        """pending-ci 文件带 cwd：fallback repo = PENDING_CWD（最权威）

        M2 后 fallback 仅剩「指定会话探活 404」单一入口（null session_id 在
        source 路由段以 ci_no_route_target 丢弃——绝不误投）。原 INFRA-701 语义
        （PENDING_CWD 优先于 repo slug）在 fallback 记录的 repo 解析中保持不变。
        """
        target_repo = tmp_path / "slug-target"
        target_repo.mkdir()
        pending_cwd = tmp_path / "pending-cwd-target"
        pending_cwd.mkdir()
        repo_config = _make_repo_config(
            tmp_path,
            [
                {
                    "repoKey": "slug-target",
                    "repoPath": str(target_repo),
                    "githubRepo": "acme/slug-target",
                }
            ],
        )
        env = _make_env(tmp_path, repo_config)
        # 探活指向 stub（session 恒 404 → 判死 → fallback spawn）
        env["FACTORY_API_BASE"] = f"{dead_session_stub}/api/v0"
        env["MAX_RETRIES"] = "1"
        env["RETRY_DELAY"] = "0"

        locks_dir = Path(env["LOCK_DIR"])
        _create_pending_file(
            locks_dir,
            201,
            cwd=str(pending_cwd),
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            session_id="dead-session-for-201",
        )

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "201", "b", "abc", "passed", "acme/slug-target"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        # 死会话（probe 404 + 无 index 豁免）→ fallback notify-suppressed 记录
        assert "FALLBACK: notify-suppressed" in combined, (
            f"Dead session must trigger fallback spawn; got:\n{combined[-800:]}"
        )
        # INFRA-701 语义：fallback repo = PENDING_CWD（优先于 repo slug 解析）
        assert f"repo: {pending_cwd})" in combined, (
            f"PENDING_CWD should win over slug; got:\n{combined[-800:]}"
        )

    def test_legacy_schema_null_session_id_dropped_not_misrouted(self, tmp_path):
        """旧 schema pending（无 session_id 字段，如 M5 产物）→ 丢弃不误投

        null + source=session 走 ci_no_route_target（宁丢勿错），绝不进入
        fallback spawn，也绝不按 cwd/slug 选会话（Bug#002 错投根因）。
        """
        pending_cwd = tmp_path / "pending-cwd-legacy"
        pending_cwd.mkdir()
        env = _make_env(tmp_path)

        locks_dir = Path(env["LOCK_DIR"])
        _create_pending_file(locks_dir, 206, cwd=str(pending_cwd))

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "206", "b", "abc", "passed", "acme/slug-target"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        assert "FALLBACK: notify-suppressed" not in combined, (
            f"null session_id must never fallback spawn; got:\n{combined[-800:]}"
        )
        assert "ci_no_route_target" in combined, (
            f"Expected ci_no_route_target telemetry; got:\n{combined[-800:]}"
        )

    def test_unresolvable_slug_falls_back_to_script_cwd(self, tmp_path):
        """repo slug 无法解析（不在 repositories.yml）→ 回退 SCRIPT_CWD 并留 WARN"""
        repo_config = _make_repo_config(
            tmp_path,
            [{"repoKey": "known", "repoPath": str(tmp_path / "known"), "githubRepo": "acme/known"}],
        )
        env = _make_env(tmp_path, repo_config)

        script_cwd = tmp_path / "receiver-cwd"
        script_cwd.mkdir()

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "202", "b", "abc", "success", "unknown-org/unknown-repo"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=script_cwd,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        assert "FALLBACK: notify-suppressed" in combined
        assert "not resolvable" in combined, "Expected WARN for unresolvable slug"
        assert f"repo: {script_cwd})" in combined, (
            f"Expected SCRIPT_CWD fallback, got:\n{combined[-800:]}"
        )

    def test_legacy_call_without_repo_keeps_script_cwd(self, tmp_path):
        """旧调用形态（4 参数、无 CI_REPO）：保持 SCRIPT_CWD 行为，向后兼容"""
        env = _make_env(tmp_path)

        script_cwd = tmp_path / "legacy-cwd"
        script_cwd.mkdir()

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "203", "b", "abc", "success"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=script_cwd,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        assert "FALLBACK: notify-suppressed" in combined
        assert f"repo: {script_cwd})" in combined, (
            f"Legacy call should keep SCRIPT_CWD, got:\n{combined[-800:]}"
        )

    def test_startup_log_includes_repo_field(self, tmp_path):
        """启动日志记录 repo 字段（可观测性：payload repo 不再静默丢弃）"""
        env = _make_env(tmp_path)

        subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "204", "b", "abc", "success", "acme/some-repo"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        log_content = _read_latest_log(env)
        assert "REPO=acme/some-repo" in log_content, (
            f"Startup log should include repo field, got:\n{log_content[:500]}"
        )

    def test_resolved_path_must_exist(self, tmp_path):
        """slug 命中但本地路径不存在 → 不采用，回退 SCRIPT_CWD（防幽灵路径）"""
        repo_config = _make_repo_config(
            tmp_path,
            [
                {
                    "repoKey": "ghost",
                    "repoPath": str(tmp_path / "does-not-exist"),
                    "githubRepo": "acme/ghost",
                }
            ],
        )
        env = _make_env(tmp_path, repo_config)

        script_cwd = tmp_path / "real-cwd"
        script_cwd.mkdir()

        result = subprocess.run(
            ["bash", str(TRIGGER_SCRIPT), "205", "b", "abc", "success", "acme/ghost"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=script_cwd,
        )

        log_content = _read_latest_log(env)
        combined = result.stdout + result.stderr + log_content

        assert "FALLBACK: notify-suppressed" in combined
        assert f"repo: {script_cwd})" in combined, (
            f"Non-existent resolved path must fall back to SCRIPT_CWD, got:\n{combined[-800:]}"
        )


class TestHooksConfigContract:
    """生产 hooks.json 契约：ci-complete 钩子透传 repo 字段

    hooks.json 不在 MANIFEST 管控内（生产专属配置），本契约测试防回退：
    repo 字段一旦从 hooks.json 丢失，INFRA-701 的参数通道即断裂。
    """

    def test_ci_complete_hook_passes_repo_argument(self):
        hooks_path = Path.home() / ".factory" / "webhook" / "hooks.json"
        # 池内 runner 异构（muse1 的 ~/.factory 不可读）：.exists() 抛 EACCES → 视同不存在
        try:
            hooks_exists = hooks_path.exists()
        except OSError:
            hooks_exists = False
        if not hooks_exists:
            import pytest

            pytest.skip("生产 hooks.json 不存在（非 Mac 生产环境）")

        hooks = json.loads(hooks_path.read_text())
        ci_hook = next((h for h in hooks if h.get("id") == "ci-complete"), None)
        assert ci_hook is not None, "ci-complete hook must exist"

        arg_names = [a.get("name") for a in ci_hook.get("pass-arguments-to-command", [])]
        assert arg_names[:4] == ["pr_number", "branch", "sha", "status"]
        assert "repo" in arg_names, (
            "ci-complete hook must pass payload repo as the 5th argument (INFRA-701)"
        )

        env_names = [e.get("envname") for e in ci_hook.get("pass-environment-to-command", [])]
        assert "CI_REPO" in env_names, (
            "ci-complete hook must pass payload repo as CI_REPO env (INFRA-701)"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
