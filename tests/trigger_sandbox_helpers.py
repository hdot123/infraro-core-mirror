"""Shared trigger-droid.sh behavior-sandbox helpers (evolution/m1fix-chain-trigger-guard dedup).

test_mac_channel_separation.py 与 test_chain_trigger_evolution_guard.py 各自内联
了同一套沙箱原语（stub 写入、darwin 门控、日志汇总、非注释行枚举、轮询等待），
AST 相似度 85-100% 触发 CODE_HYGIENE_DUPLICATE_BLOCK；本模块是引擎仓
INFRA-1286 shard_sandbox_helpers.py 同款收敛：原语单源，行为面测试各自持有
场景构造（沙箱拓扑/注入差异留在调用方文件）。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


def write_stub(path: Path, content: str) -> None:
    """写入可执行 stub 脚本（测试假 bin）。"""
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def darwin_skip_reason() -> str | None:
    """行为沙箱跑真源 trigger-droid.sh 全链路：macOS 专属路径已在 MANIFEST
    ENV_DIFF_LINES 声明（/opt/homebrew/bin/{python3,flock}）。Linux CI 上跳过
    行为层（归一化/变异矩阵/结构断言仍全平台执行）。"""
    if sys.platform != "darwin":
        return f"behavior sandbox is macOS-only (got {sys.platform})"
    if not Path("/opt/homebrew/bin/flock").exists():
        return "/opt/homebrew/bin/flock missing"
    if not Path("/opt/homebrew/bin/python3").exists():
        return "/opt/homebrew/bin/python3 missing"
    return None


def combined_log(log_dir: Path, extra_paths: list[Path] | None = None) -> str:
    """汇总沙箱日志目录下全部 *.log + 可选补充文件（gh stub 调用日志等）。"""
    out = []
    if log_dir.exists():
        for p in sorted(log_dir.glob("*.log")):
            try:
                out.append(p.read_text(encoding="utf-8"))
            except OSError:
                pass
    for p in extra_paths or []:
        if p.exists():
            out.append(p.read_text(encoding="utf-8"))
    return "\n".join(out)


def wait_for(condition, timeout: float = 60.0, interval: float = 1.0) -> bool:
    """轮询等待条件成立（trigger-droid.sh 立即返回，droid 在后台子 shell 执行）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


def non_comment_lines(path: Path) -> list[tuple[int, str]]:
    """脚本非空非注释行枚举（结构性 grep 断言用），返回 (行号, 去首尾空白行)。"""
    out = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((idx, stripped))
    return out


def bash_syntax_ok(*paths: Path) -> tuple[bool, str]:
    """对脚本集合跑 bash -n 语法门；返回 (是否全过, 首个错误描述)。"""
    for path in paths:
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return False, f"{path.name} 语法错误: {result.stderr}"
    return True, ""


_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEBHOOK_DIR = _REPO_ROOT / "webhook-scripts"


def make_trigger_sandbox(tmp_path: Path) -> dict:
    """trigger-droid.sh 全链路沙盒：真源脚本 + 账本/通道/posthog lib，droid/gh/linear 走 stub。

    返回 {sandbox, logs, marker, tmp}；droid stub 启动即 touch marker 并 exit 127。
    """
    sandbox = tmp_path / "webhook"
    repo = tmp_path / "target-repo"
    logs = sandbox / "logs"
    logs.mkdir(parents=True)
    (sandbox / "locks").mkdir(parents=True)
    (sandbox / "status").mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    marker = tmp_path / "droid-launched.marker"
    (tmp_path / "bin").mkdir()
    write_stub(
        tmp_path / "bin" / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho "STUB-DROID-LAUNCHED" >&2\nexit 127\n',
    )
    write_stub(
        tmp_path / "bin" / "gh",
        '#!/bin/bash\necho "gh-stub: $*" >> "GH_LOG"\nexit 0\n'.replace(
            "GH_LOG", str(tmp_path / "gh-calls.log")
        ),
    )
    # JSON 是合法 YAML：trigger-droid.sh 以 yaml.safe_load 读取
    (tmp_path / "repositories.yml").write_text(
        json.dumps(
            {
                "teams": {
                    "infra": {
                        "teamKey": "INFRA",
                        "repos": [{"repoPath": str(repo), "default": True}],
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    prod_scripts = sandbox / "scripts"
    prod_lib = prod_scripts / "lib"
    prod_lib.mkdir(parents=True)
    write_stub(
        prod_lib / "linear-queue.sh",
        "linear_queue_init() { return 1; }\n"
        "linear_move_to_in_progress() { return 1; }\n"
        "linear_query_next_queued() { return 1; }\n"
        "linear_get_issue_team_key() { return 1; }\n",
    )
    write_stub(prod_lib / "p0a-guard.sh", "# test stub: guard disabled\n")
    shutil.copy2(_WEBHOOK_DIR / "trigger-droid.sh", prod_scripts / "trigger-droid.sh")
    for name in ("dispatch-ledger.sh", "evolution-channel.sh", "posthog.sh"):
        shutil.copy2(_WEBHOOK_DIR / "lib" / name, prod_lib / name)
    write_stub(
        prod_lib / "op-mcp.sh",
        'OP_VAULT_SEVER="test-vault-id"\nop_get_field() { return 1; }\n',
    )
    return {"sandbox": sandbox, "logs": logs, "marker": marker, "tmp": tmp_path}


def run_trigger_sandbox(
    sandbox: dict,
    ref: str = "INFRA-3301",
    uuid: str = "11111111-aaaa-bbbb-cccc-00000000000a",
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """在 make_trigger_sandbox 沙盒内跑一次 trigger-droid.sh create Issue 派发。"""
    env = os.environ.copy()
    env.update(
        {
            "WEBHOOK_BASE": str(sandbox["sandbox"]),
            "REPO_CONFIG": str(sandbox["tmp"] / "repositories.yml"),
            "PATH": f"{sandbox['tmp'] / 'bin'}:{env.get('PATH', '')}",
            "POSTHOG_DRY_RUN": "1",
            "LINEAR_API_KEY": "",
        }
    )
    env.pop("LINEAR_LABELS", None)
    env.pop("RESUME_SESSION_ID", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [
            "bash",
            str(sandbox["sandbox"] / "scripts" / "trigger-droid.sh"),
            "create",
            "Issue",
            ref,
            uuid,
            "INFRA",
            "normal dispatch title",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(sandbox["tmp"]),
    )


def trigger_sandbox_log(sandbox: dict, result: subprocess.CompletedProcess) -> str:
    """派发结果 stdout/stderr + 沙盒日志目录汇总。"""
    return "\n".join([result.stdout, result.stderr, combined_log(sandbox["logs"])])


def wait_droid_marker(sandbox: dict, timeout: float = 30.0) -> bool:
    """droid exec 在后台子 shell 异步启动：轮询等 stub marker。"""
    return wait_for(sandbox["marker"].exists, timeout=timeout, interval=0.5)
