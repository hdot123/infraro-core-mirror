"""每日记忆巡检基础层：常量、路径解析、工具函数、项目解析与全局 KB 指纹。

v3 拆分：自 daily_audit.py（原 memory_core/tools/_audit_project.py 迁移整合）拆出。
本模块是拆分后的最底层，禁止反向 import 其他 _daily_* 模块。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量（原 memory_core.constants 依赖内联）
# ---------------------------------------------------------------------------

# 原 memory_core.constants.SYSTEM_DIR
SYSTEM_DIR = "memory/system"

MANIFEST_FILENAME = "manifest.json"

MANIFEST_PATH_REL = f"{SYSTEM_DIR}/{MANIFEST_FILENAME}"  # memory/system/manifest.json

# memory/kb/ 下无需签名的模板文件（和 init_project_memory 保持一致）
KB_UNSIGNED_WHITELIST = {".keep", "README.md", "INDEX.md"}

# 全局 KB 跳过的非知识文件
GLOBAL_KB_SKIP = {".keep", "README.md", "INDEX.md"}

# 全局 KB 的三个域
GLOBAL_KB_DOMAINS = ("operations", "engineering", "collaboration")

# 数据库/大文件违规规则（参考 no-database-files-in-repo.md）
LARGE_SQL_THRESHOLD = 1024 * 1024  # 1MB

DATABASE_FILE_SUFFIXES = (".sql.gz", ".dump", ".bak", ".sqlite", ".db")

# 飞书通知
LARK_NOTIFY_ENV = "LARK_AUDIT_CHAT_ID"

LARK_NOTIFY_TIMEOUT = 15  # 秒

# 超时（秒）
SSH_TIMEOUT = 10  # 顶层 SSH 探测 / docker ps 通过 SSH 的整体超时

SSH_CONNECT_TIMEOUT = 5  # ssh -o ConnectTimeout=...

TCP_TIMEOUT = 5  # 端口/数据库 TCP connect

HTTP_TIMEOUT = 5  # curl --max-time

# 排除目录（大文件扫描）
_EXCLUDED_DIR_SEGMENTS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)

# ---------------------------------------------------------------------------
# 路径解析（替代原硬编码 ~/.memory-core 常量）
# ---------------------------------------------------------------------------


def _default_memory_core_home() -> Path:
    """状态根目录：环境变量 INFRA_MEMORY_CORE_HOME 覆盖，默认 ~/.memory-core。"""
    env = os.environ.get("INFRA_MEMORY_CORE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".memory-core"


def _default_lifecycle_index() -> Path:
    return _default_memory_core_home() / "project-lifecycle" / "path-index.json"


def _default_audit_dir() -> Path:
    return _default_memory_core_home() / "audit"


def _default_infra_inventory() -> Path:
    return _default_memory_core_home() / "infrastructure-inventory.yaml"


def _default_global_kb_root() -> Path:
    env = os.environ.get("INFRA_GLOBAL_KB_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".memory" / "global-kb"


# ---------------------------------------------------------------------------
# 工具函数（原 _audit_project 底层）
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """当前本地时间 ISO8601 字符串（带时区）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _now_iso_local() -> str:
    """now_iso 的别名（保留原模块顶层名）。"""
    return now_iso()


def sha256_file(path: Path) -> str | None:
    """计算文件内容 SHA-256，读取失败返回 None。"""
    try:
        hasher = hashlib.sha256()
        with Path(path).open("rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


# 向后兼容别名
_sha256_file = sha256_file


def _strip_frontmatter(text: str) -> str:
    """去掉 Markdown 顶部的 YAML frontmatter（--- ... ---）。"""
    return _FRONTMATTER_RE.sub("", text, count=1)


def _normalize_for_compare(text: str) -> str:
    """去 frontmatter → 全文归一化 → 去所有空白 → 小写。

    修复指纹碰撞：原实现仅取前 200 字符，导致共享模板头但正文不同的文档假阳性。
    现改为全文归一化，确保区分性。
    """
    body = _strip_frontmatter(text)
    no_ws = re.sub(r"\s+", "", body)
    return no_ws.lower()


def _read_text_safe(path: Path) -> str | None:
    """读 UTF-8 文本，失败返回 None。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _make_violation(
    vtype: str,
    severity: str,
    file: str,
    detail: str,
) -> dict[str, Any]:
    """构造一条违规记录。"""
    return {
        "type": vtype,
        "severity": severity,
        "file": file,
        "detail": detail,
    }


def _shell_quote(s: str) -> str:
    """POSIX shell 单引号转义，用于构造安全的远程脚本。"""
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------------------------------------------------------------------------
# 项目路径解析
# ---------------------------------------------------------------------------


def load_registered_projects(lifecycle_index: Path | None = None) -> list[tuple[str, Path]]:
    """从 path-index.json 读取所有注册项目，返回 [(name, path), ...]。

    跳过不存在或无法解析的条目。返回顺序按 path-index.json 的 key 排序，
    保证报告幂等。
    """
    index_path = lifecycle_index or _default_lifecycle_index()
    if not index_path.exists():
        return []

    try:
        idx = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    paths_dict = idx.get("paths", {})
    if not isinstance(paths_dict, dict):
        return []

    # 排除非业务项目（Droid 运行环境配置目录，非消费项目）
    EXCLUDE_PATHS = {str(Path.home() / ".factory")}

    projects: list[tuple[str, Path]] = []
    for raw_path in sorted(paths_dict.keys()):
        if raw_path in EXCLUDE_PATHS:
            continue
        meta = paths_dict.get(raw_path) or {}
        name = meta.get("project_name") or Path(raw_path).name or raw_path
        projects.append((str(name), Path(raw_path).expanduser()))
    return projects


# ---------------------------------------------------------------------------
# memory-core 源仓库检测（内联，替代 memory_core.ownership 导入）
# ---------------------------------------------------------------------------


def is_memory_core_source_repo(project_root: Path) -> bool:
    """检测是否为 memory-core 源仓库（防自污染跳过逻辑）。

    通过仓库内标记文件判断，不依赖 git、不导入 memory_core。
    """
    resolved = project_root.resolve()
    markers = [
        resolved / "memory_core" / "tools" / "memory_hook_gateway.py",
        resolved / "memory_core" / "tools" / "factory_global_hooks.py",
        resolved / "memory_core" / "ownership.py",
    ]
    return any(marker.exists() for marker in markers)


# ---------------------------------------------------------------------------
# 全局 KB 内容指纹（用于残留检测）
# ---------------------------------------------------------------------------


def build_global_kb_fingerprints(global_kb_root: Path | None = None) -> dict[str, str]:
    """对全局 KB 三个域下每个知识文件计算“归一化指纹”。

    Returns:
        {normalized_fingerprint: global_kb_rel_path}
    """
    kb_root = global_kb_root or _default_global_kb_root()
    fingerprints: dict[str, str] = {}
    if not kb_root.exists():
        return fingerprints

    for domain in GLOBAL_KB_DOMAINS:
        domain_dir = kb_root / domain
        if not domain_dir.is_dir():
            continue
        for md_path in sorted(domain_dir.rglob("*.md")):
            if md_path.name in GLOBAL_KB_SKIP:
                continue
            text = _read_text_safe(md_path)
            if text is None:
                continue
            fp = _normalize_for_compare(text)
            if not fp:
                continue
            rel = str(md_path.relative_to(kb_root))
            fingerprints.setdefault(fp, rel)
    return fingerprints
