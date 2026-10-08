"""lib/dispatch-ledger.sh 语义检查 helper（shared-ledger 测试专用，零生产依赖）。

bash 断言通过 bash -c 调真源库完成（source 守卫文件存在性 → 执行片段）。
事件流断言（events.jsonl）与记录本体（<uuid>.json）读取也在此单源。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_LIB = REPO_ROOT / "webhook-scripts" / "lib" / "dispatch-ledger.sh"

LEDGER_BASH_PRELUDE = """
set -u
LEDGER_WEBHOOK_BASE="{base}"
WEBHOOK_BASE="{base}"
PYTHON_BIN="{python}"
source "{lib}"
"""


def run_ledger_fn(
    snippet: str, base: Path, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    """在独立 bash 进程内 source 真源账本库并执行片段；返回 CompletedProcess。"""
    env = os.environ.copy()
    env["PYTHON_BIN"] = env.get("PYTHON_BIN") or "python3"
    if env_extra:
        env.update(env_extra)
    script = (
        LEDGER_BASH_PRELUDE.format(base=base, python=env["PYTHON_BIN"], lib=LEDGER_LIB) + snippet
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def make_base(tmp_path: Path) -> Path:
    """账本宿主目录（=WEBHOOK_BASE 沙盒）。"""
    base = tmp_path / "webhook"
    base.mkdir(parents=True, exist_ok=True)
    return base


def read_events(base: Path) -> list[dict]:
    """读取事件流（不存在返回空表）。"""
    f = base / "status" / "ledger" / "events.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


def read_record(base: Path, uuid: str) -> dict | None:
    """按 uuid 直读 record 本体（不经过 by-ref 视图）。"""
    f = base / "status" / "ledger" / f"{uuid}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text())


def temp_ledger_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="ledger-check-")
