"""每日记忆巡检 — 从 memory-core tools 迁移整合版（8 文件合一）。

迁移来源（memory_core/tools/，行为保持等价）：
- _audit_project.py: 路径常量、工具函数、项目解析、全局 KB 指纹
- _audit_checks.py:  检查 1-5（manifest / 未签名 / 残留 / 大文件 / 版本一致性）
- _audit_infra.py:   基础设施清单加载、SSH、TCP、磁盘、systemd 服务
- _audit_server.py:  服务器/数据库检查、单项目编排 audit_project
- _audit_report.py:  报告组装、文件写入、飞书通知摘要
- _audit_cli.py:     CLI 入口 main() 与编排
- daily_kb_audit.py: 原 re-export 门面（合并后不再需要）

infra-core 适配（相对 memory-core 版的差异）：
1. 移除 memory_core.constants 依赖：
   - SYSTEM_DIR 内联为常量 ``"memory/system"``
   - CURRENT_MEMORY_VERSION 改为可配置（``--expected-version`` /
     环境变量 ``INFRA_EXPECTED_MEMORY_VERSION``）；未配置时跳过
     「与期望版本比对」，仅保留三文件互相一致性校验
2. 移除硬编码 ``~/.memory-core`` 路径：
   - ``--repo-root`` 指定单一被审计仓库（跳过 lifecycle index 扫描）
   - lifecycle index / audit 目录 / 基础设施清单根路径由
     ``INFRA_MEMORY_CORE_HOME`` 环境变量覆盖（默认 ``~/.memory-core``）
   - ``--lifecycle-index`` / ``--infra-inventory`` / ``--global-kb-root``
     显式覆盖各子路径
3. ``--report-only``：禁止写默认 audit 目录，仅当给出 ``--output`` 时写
   显式路径（适配沙箱/CI 环境，避免污染共享状态目录）
4. is_memory_core_source_repo 内联为标记检测（不再导入 memory_core.ownership）

检查项:
    1. manifest.json 哈希完整性（SHA-256 重新计算比对）
    2. memory/kb/ 下未签名文件（对比 manifest entries）
    3. 通用经验残留检测（项目 KB 文件 vs 全局 KB）
    4. 大文件/数据库文件违规（参考 no-database-files-in-repo.md）
    5. 三文件版本一致性（memory.lock / adapter.toml / ownership.toml）
    6. 基础设施健康检查（SSH / Docker / 端口 / HTTP / 数据库，
       清单来自 infrastructure-inventory.yaml）

Usage:
    infra-daily-audit --repo-root /path/to/repo --json
    infra-daily-audit --repo-root /path/to/repo --report-only --output report.json
    infra-daily-audit                    # 扫描 lifecycle index 注册的所有项目
    infra-daily-audit --no-infra         # 跳过基础设施检查
    infra-daily-audit --notify           # 扫描后通过 lark-cli 发飞书通知

设计原则:
    - 幂等、安全、只读（绝不修改任何项目文件）
    - 跳过不存在的项目路径（可能已删除）
    - 单个项目检查失败不影响其他项目
    - 基础设施清单文件缺失或 PyYAML 不可用时优雅降级（不崩溃）

v3 拆分（架构分层决策步骤 5，2026-09-21）：本模块原为 2280 行单体，现拆为 5 个平铺
私有模块，本文件退化为门面（facade）：

- ``_daily_base``:   常量、路径解析、工具函数、项目解析与全局 KB 指纹
- ``_daily_checks``: 检查 1-5（manifest / 未签名 / 残留 / 大文件 / 版本一致性）
- ``_daily_infra``:  基础设施清单、SSH/TCP/磁盘/systemd、服务器与数据库检查
- ``_daily_report``: audit_project 编排、报告组装与写入、飞书通知摘要
- ``_daily_cli``:    参数解析、编排 helper、main 入口

依赖方向单向：cli → report → infra/checks → base。门面显式 re-export 全部既有
导出名（含下划线别名）以保持 import / entry point / ``python -m`` 行为不变；
下划线别名不在 ``__all__`` 中（避免改变 ``import *`` 语义），故整文件豁免 F401。
"""

# 门面模块：显式 re-export（含下划线别名），整文件豁免未使用导入告警。
# ruff: noqa: F401

from __future__ import annotations

import sys

from ._daily_base import (
    _EXCLUDED_DIR_SEGMENTS,
    _FRONTMATTER_RE,
    DATABASE_FILE_SUFFIXES,
    GLOBAL_KB_DOMAINS,
    GLOBAL_KB_SKIP,
    HTTP_TIMEOUT,
    KB_UNSIGNED_WHITELIST,
    LARGE_SQL_THRESHOLD,
    LARK_NOTIFY_ENV,
    LARK_NOTIFY_TIMEOUT,
    MANIFEST_FILENAME,
    MANIFEST_PATH_REL,
    SSH_CONNECT_TIMEOUT,
    SSH_TIMEOUT,
    SYSTEM_DIR,
    TCP_TIMEOUT,
    _default_audit_dir,
    _default_global_kb_root,
    _default_infra_inventory,
    _default_lifecycle_index,
    _default_memory_core_home,
    _make_violation,
    _normalize_for_compare,
    _now_iso_local,
    _read_text_safe,
    _sha256_file,
    _shell_quote,
    _strip_frontmatter,
    build_global_kb_fingerprints,
    is_memory_core_source_repo,
    load_registered_projects,
    now_iso,
    sha256_file,
)
from ._daily_checks import (
    _check_single_file,
    _extract_version_from_toml,
    _is_excludable_path,
    check_global_residue,
    check_large_or_db_files,
    check_manifest_integrity,
    check_unsigned_files,
    check_version_consistency,
)
from ._daily_cli import (
    _audit_all_projects,
    _count_critical_infra,
    _count_warning_infra,
    _handle_no_projects,
    _maybe_write_report,
    _parse_args,
    _resolve_expected_version,
    _resolve_projects,
    _run_infra_check,
    _summarize_to_console,
    main,
)
from ._daily_infra import (
    _append_violation,
    _check_server_docker,
    _check_server_http_endpoints,
    _check_server_ports,
    _check_server_ssh,
    _check_systemd_services,
    _find_matching_mount,
    _load_infra_inventory,
    _parse_df_output,
    _run_ssh,
    _tcp_connect_ok,
    check_database,
    check_disk_space,
    check_infrastructure,
    check_server,
    check_ssh_reachable,
)
from ._daily_report import (
    _append_infra_summary,
    _safe_check,
    _summarize_containers,
    _summarize_database_entry,
    _summarize_disks,
    _summarize_http,
    _summarize_ports,
    _summarize_report,
    _summarize_server_entry,
    _summarize_ssh,
    _summarize_systemd,
    _summarize_violation_block,
    audit_project,
    build_report,
    notify_via_lark,
    write_report,
)

__all__ = [
    # 常量
    "SYSTEM_DIR",
    "MANIFEST_FILENAME",
    "MANIFEST_PATH_REL",
    "KB_UNSIGNED_WHITELIST",
    "GLOBAL_KB_DOMAINS",
    "GLOBAL_KB_SKIP",
    "LARGE_SQL_THRESHOLD",
    "DATABASE_FILE_SUFFIXES",
    "LARK_NOTIFY_ENV",
    "LARK_NOTIFY_TIMEOUT",
    "SSH_TIMEOUT",
    "SSH_CONNECT_TIMEOUT",
    "TCP_TIMEOUT",
    "HTTP_TIMEOUT",
    # 工具函数
    "now_iso",
    "sha256_file",
    "_strip_frontmatter",
    "_normalize_for_compare",
    "_make_violation",
    "_shell_quote",
    # 项目解析 / 指纹
    "load_registered_projects",
    "build_global_kb_fingerprints",
    "is_memory_core_source_repo",
    # 检查 1-5
    "check_manifest_integrity",
    "check_unsigned_files",
    "check_global_residue",
    "check_large_or_db_files",
    "check_version_consistency",
    # 基础设施
    "check_infrastructure",
    "check_server",
    "check_database",
    "check_ssh_reachable",
    "check_disk_space",
    # 报告 / 通知
    "build_report",
    "write_report",
    "notify_via_lark",
    # 编排 / CLI
    "audit_project",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
