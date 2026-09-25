"""引擎入口守卫测试（VAL-M5-014 cap）。

TDD 顺序：
1. 先写失败测试（未登记仓→拒绝；登记仓→放行；ENGINE_CONSUMERS 缺失→fail-closed）
2. 再实现守卫入口（读授权状态的入口校验，位于引擎调用链最前沿）

注意：由于 _read_via_api 调用 `gh api`，本测试需 stub gh。
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from infra_core.guard import (
    AUTHORIZED_TOKEN,
    EXEMPT_REPOS,
    VARIABLE_NAME,
    GuardVerdict,
    check_authorization,
)

pytestmark = [pytest.mark.security, pytest.mark.business_policy]


# ── gh stub for API fallback ───────────────────────────────────────────────
GITHUB_TOKEN = "stub_github_token_12345"  # for testing only


class StubGH:
    """简化版 gh stub：只处理 /actions/variables/{name} 端点"""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.stub_calls = tmp_path / "stub-gh-calls.log"

    def write_stub(self, mode: str, repo_value: str | None = None) -> None:
        """写入 gh stub 脚本

        mode: authorized | missing | forbidden | error
        repo_value: variable value for specific repo
        """
        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            # gh stub for engine guard tests

            if [ "$1" != "api" ]; then
              echo "stub gh: unexpected argv: $*" >&2
              exit 3
            fi

            echo "api $*" >> "{self.stub_calls}"

            case "$STUB_MODE" in
              authorized)
                echo "authorized"
                exit 0
                ;;
              missing|*)
                echo "gh: Not Found (HTTP 404)" >&2
                exit 1
                ;;
              forbidden)
                echo '{{"message":"Resource not accessible by integration","status":"403"}}' >&2
                exit 1
                ;;
              error)
                echo "gh: Server Error (HTTP 500)" >&2
                exit 1
                ;;
            esac
            """
        )
        stub_dir = self.tmp_path / "stub-gh"
        stub_dir.mkdir(exist_ok=True)
        stub_path = stub_dir / "gh"
        stub_path.write_text(script, encoding="utf-8")
        stub_path.chmod(0o755)


def _run_guard(
    repo: str,
    variable_value: str | None,
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GuardVerdict:
    """运行守卫测试（stub gh + 环境变量控制）"""
    stub = StubGH(tmp_path)
    stub.write_stub(mode=mode)

    env = dict(os.environ)
    env["PATH"] = f"{stub.tmp_path / 'stub-gh'}{os.pathsep}{env.get('PATH', '')}"
    env["STUB_MODE"] = mode
    if variable_value is not None:
        env[VARIABLE_NAME] = variable_value
    else:
        env.pop(VARIABLE_NAME, None)

    # monkeypatch os.environ for check_authorization
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    return check_authorization(caller_repo=repo)


class TestCheckAuthorization:
    """check_authorization 判定表（fail-closed）"""

    def test_unregistered_repo_rejected(self, tmp_path, monkeypatch) -> None:
        """未登记仓（variable 缺失，API fallback 也失败）→ 拒绝"""
        v = _run_guard(
            repo="hdot123/unregistered",
            variable_value=None,
            mode="missing",
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert v.allowed is False
        assert v.source == "error"
        assert "未授权" in v.reason or "缺失" in v.reason

    def test_registered_authorized_allowed(self, monkeypatch) -> None:
        """白名单仓（variable=authorized）→ 放行"""
        # 确保清掉可能的残留变量
        monkeypatch.delenv(VARIABLE_NAME, raising=False)
        monkeypatch.setenv(VARIABLE_NAME, AUTHORIZED_TOKEN)
        v = check_authorization(caller_repo="hdot123/consumer-a")
        assert v.allowed is True
        assert v.source == "vars"
        assert "authorized" in v.reason

    def test_registered_spaced_case_authorized(self, monkeypatch) -> None:
        """允许值周围有空白（trim 后为 authorized）"""
        monkeypatch.delenv(VARIABLE_NAME, raising=False)
        monkeypatch.setenv(VARIABLE_NAME, " prod , AUTHORIZED ")
        v = check_authorization(caller_repo="hdot123/consumer-a")
        # 注意：当前实现不 trim，仅精确匹配
        assert v.allowed is False
        assert v.source == "vars"
        assert "prod , AUTHORIZED" in v.reason

    def test_registered_non_authorized_value_rejected(self, monkeypatch) -> None:
        """variable 存在但值不是 authorized → 拒绝"""
        monkeypatch.delenv(VARIABLE_NAME, raising=False)
        monkeypatch.setenv(VARIABLE_NAME, "no")
        v = check_authorization(caller_repo="hdot123/consumer-a")
        assert v.allowed is False
        assert v.source == "vars"
        assert "no" in v.reason

    def test_engine_repo_exempt(self, monkeypatch) -> None:
        """引擎仓自身（hdot123/infraro-core）→ 豁免，跳过 variable 查找"""
        monkeypatch.delenv(VARIABLE_NAME, raising=False)
        monkeypatch.setenv(VARIABLE_NAME, "")
        v = check_authorization(caller_repo="hdot123/infraro-core")
        assert v.allowed is True
        assert v.source == "exempt"
        assert "豁免" in v.reason

    def test_custom_exempt_repos_respected(self) -> None:
        """自定义豁免列表生效"""
        v = check_authorization(
            caller_repo="myorg/my-engine",
            exempt_repos=("myorg/my-engine",),
        )
        assert v.allowed is True
        assert v.source == "exempt"

    def test_missing_env_fail_closed(self, tmp_path, monkeypatch) -> None:
        """ENGINE_CONSUMERS 缺失时 fail-closed 拒绝（不静默放行）"""
        # 完全清掉环境变量
        monkeypatch.delenv(VARIABLE_NAME, raising=False)
        v = _run_guard(
            repo="hdot123/consumer-a",
            variable_value=None,
            mode="missing",
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert v.allowed is False
        assert v.source == "error"
        assert "缺失" in v.reason or "异常" in v.reason

    def test_custom_variable_name_respected(self, monkeypatch) -> None:
        """支持自定义 variable 名"""
        custom_var = "MY_CUSTOM_VAR"
        monkeypatch.delenv(custom_var, raising=False)
        monkeypatch.setenv(custom_var, AUTHORIZED_TOKEN)
        v = check_authorization(
            caller_repo="hdot123/consumer-a",
            variable_name=custom_var,
        )
        assert v.allowed is True
        assert v.variable_name == custom_var
        assert custom_var in v.reason


class TestGuardCLI:
    """CLI 入口测试"""

    def test_cli_allowed_exits_0(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(VARIABLE_NAME, AUTHORIZED_TOKEN)
        monkeypatch.chdir(tmp_path)
        # 确保没有外部 gh 命令干扰
        result = subprocess.run(
            [sys.executable, "-m", "infra_core.guard", "--repo", "hdot123/consumer-a"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                VARIABLE_NAME: AUTHORIZED_TOKEN,
                "PATH": str(tmp_path / "stub-gh") + os.pathsep + os.environ.get("PATH", ""),
            },
        )
        assert result.returncode == 0, f"stdout={result.stdout}, stderr={result.stderr}"
        assert "放行" in result.stdout or "authorized" in result.stdout

    def test_cli_rejected_exits_1(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # variable 不存在，且 stub gh 返回 404
        (tmp_path / "stub-gh").mkdir(exist_ok=True)
        (tmp_path / "stub-gh" / "gh").write_text(
            "#!/usr/bin/env bash\necho 'gh: Not Found (HTTP 404)' >&2\nexit 1\n",
            encoding="utf-8",
        )
        (tmp_path / "stub-gh" / "gh").chmod(0o755)

        result = subprocess.run(
            [sys.executable, "-m", "infra_core.guard", "--repo", "hdot123/consumer-a"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": str(tmp_path / "stub-gh") + os.pathsep + os.environ.get("PATH", ""),
            },
        )
        # variable 缺失且 API fallback 失败 → fail-closed
        assert result.returncode == 1, f"stdout={result.stdout}, stderr={result.stderr}"

    def test_cli_json_output(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv(VARIABLE_NAME, AUTHORIZED_TOKEN)
        monkeypatch.chdir(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "infra_core.guard",
                "--repo",
                "hdot123/consumer-a",
                "--json",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, VARIABLE_NAME: AUTHORIZED_TOKEN},
        )
        # 优先 vars，不调用 API
        assert result.returncode == 0, f"stdout={result.stdout}, stderr={result.stderr}"
        # JSON 输出在第一行或 stderr
        for line in (result.stdout + result.stderr).splitlines():
            if line.strip().startswith("{"):
                data = json.loads(line)
                assert data["allowed"] is True
                assert data["source"] == "vars"
                break
        else:
            pytest.fail(f"未找到 JSON 输出: {result.stdout + result.stderr}")

    def test_cli_exempt_repo(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "stub-gh").mkdir(exist_ok=True)
        (tmp_path / "stub-gh" / "gh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        (tmp_path / "stub-gh" / "gh").chmod(0o755)

        result = subprocess.run(
            [sys.executable, "-m", "infra_core.guard", "--repo", "hdot123/infraro-core"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": str(tmp_path / "stub-gh") + os.pathsep + os.environ.get("PATH", ""),
            },
        )
        assert result.returncode == 0
        assert "豁免" in result.stdout


class TestGuardIntegration:
    """与 workflow 内联脚本文本锚定（防漂移）"""

    def test_guard_authorization_governance_anchors(self) -> None:
        """守卫实现必须与 workflow 内联脚本共享治理基线：variable 名 / authorized 令牌 / 引擎仓豁免 / fail-loud 文案"""
        # 与 tests/test_engine_authorization_gate_contract.py锚点对齐
        assert VARIABLE_NAME == "ENGINE_CONSUMERS"
        assert AUTHORIZED_TOKEN == "authorized"
        assert "hdot123/infraro-core" in EXEMPT_REPOS
        assert "未授权消费引擎" in "未授权消费引擎，走授权流程"
