"""Shared trigger-droid.sh behavior-sandbox helpers (evolution/m1fix-chain-trigger-guard dedup).

test_mac_channel_separation.py 与 test_chain_trigger_evolution_guard.py 各自内联
了同一套沙箱原语（stub 写入、darwin 门控、日志汇总、非注释行枚举、轮询等待），
AST 相似度 85-100% 触发 CODE_HYGIENE_DUPLICATE_BLOCK；本模块是引擎仓
INFRA-1286 shard_sandbox_helpers.py 同款收敛：原语单源，行为面测试各自持有
场景构造（沙箱拓扑/注入差异留在调用方文件）。
"""

from __future__ import annotations

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
