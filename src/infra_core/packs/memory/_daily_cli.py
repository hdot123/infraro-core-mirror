"""每日记忆巡检 CLI：参数解析、编排 helper 与 main 入口。

v3 拆分：自 daily_audit.py（原 memory_core/tools/_audit_cli.py 迁移整合）拆出。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ._daily_base import (
    _default_lifecycle_index,
    _make_violation,
    build_global_kb_fingerprints,
    load_registered_projects,
)
from ._daily_infra import check_infrastructure
from ._daily_report import (
    audit_project,
    build_report,
    notify_via_lark,
    write_report,
)

# ---------------------------------------------------------------------------
# CLI 入口（原 _audit_cli）
# ---------------------------------------------------------------------------


def _resolve_expected_version(cli_value: str | None) -> str | None:
    """期望版本解析：CLI 参数 > 环境变量 INFRA_EXPECTED_MEMORY_VERSION > None。

    None 表示跳过「与期望版本比对」，仅保留三文件互相一致性校验
    （原 memory-core 版此值来自包版本 memory_core.__version__）。
    """
    if cli_value:
        return cli_value
    env = os.environ.get("INFRA_EXPECTED_MEMORY_VERSION", "").strip()
    return env or None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="infra-daily-audit",
        description="每日记忆巡检：检查所有接入项目的记忆纯度和完整性。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "检查项:\n"
            "  1. manifest.json 哈希完整性\n"
            "  2. memory/kb/ 下未签名文件\n"
            "  3. 通用经验残留检测（项目 KB vs 全局 KB）\n"
            "  4. 大文件/数据库文件违规\n"
            "  5. 三文件版本一致性\n"
            "  6. 基础设施健康检查（SSH / Docker / 端口 / HTTP / 数据库）\n"
            "\n"
            "示例:\n"
            "  infra-daily-audit --repo-root /path/to/repo --json\n"
            "  infra-daily-audit --repo-root . --report-only --output report.json\n"
            "  infra-daily-audit                    # 扫描 lifecycle index 注册的所有项目\n"
            "  infra-daily-audit --no-infra         # 跳过基础设施检查\n"
            "  infra-daily-audit --notify           # 扫描后通过 lark-cli 发飞书通知\n"
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="审计单一仓库（跳过 lifecycle index 扫描；pack 调用模式）",
    )
    parser.add_argument(
        "--project-name",
        default=None,
        help="报告中的项目名（默认取 --repo-root 目录名）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="将完整 JSON 报告输出到 stdout（仍会写文件）",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="扫描后通过 lark-cli 发飞书通知（需设置 LARK_AUDIT_CHAT_ID）",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="不写报告文件（仅 --json 或 --notify 时有意义）",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="禁止写默认 audit 目录；仅当给出 --output 时写显式路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="报告显式输出路径（优先于默认 audit 目录）",
    )
    parser.add_argument(
        "--no-infra",
        action="store_true",
        help="跳过基础设施检查（只做项目记忆巡检）",
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="期望记忆版本（默认读 INFRA_EXPECTED_MEMORY_VERSION；均未设置时跳过期望值比对）",
    )
    parser.add_argument(
        "--lifecycle-index",
        type=Path,
        default=None,
        help="项目注册索引路径（默认 <INFRA_MEMORY_CORE_HOME>/project-lifecycle/path-index.json）",
    )
    parser.add_argument(
        "--infra-inventory",
        type=Path,
        default=None,
        help="基础设施清单 YAML 路径（默认 <INFRA_MEMORY_CORE_HOME>/infrastructure-inventory.yaml）",
    )
    parser.add_argument(
        "--global-kb-root",
        type=Path,
        default=None,
        help="全局 KB 根目录（默认 ~/.memory/global-kb 或 INFRA_GLOBAL_KB_ROOT）",
    )
    return parser.parse_args(argv)


def _count_critical_infra(infra: dict[str, Any] | None) -> int:
    """统计基础设施子树里 critical 违规数（servers + databases）。"""
    if not infra:
        return 0
    n = 0
    for kind in ("servers", "databases"):
        for _name, rec in (infra.get(kind) or {}).items():
            n += sum(1 for v in rec.get("violations", []) if v.get("severity") == "critical")
    return n


def _count_warning_infra(infra: dict[str, Any] | None) -> int:
    """统计基础设施子树里 warning 违规数（servers + databases）。"""
    if not infra:
        return 0
    n = 0
    for kind in ("servers", "databases"):
        for _name, rec in (infra.get(kind) or {}).items():
            n += sum(1 for v in rec.get("violations", []) if v.get("severity") == "warning")
    return n


def _run_infra_check(
    no_infra: bool,
    inventory_path: Path | None = None,
) -> dict[str, Any] | None:
    """执行基础设施检查，异常时降级返回 None。"""
    if no_infra:
        return None
    print("[audit] 基础设施检查开始…", file=sys.stderr)
    try:
        infra_results = check_infrastructure(inventory_path)
        infra_viol = len(infra_results.get("violations", []))
        print(
            f"[audit] 基础设施检查完成: "
            f"服务器={len(infra_results.get('servers', {}))} "
            f"数据库={len(infra_results.get('databases', {}))} "
            f"违规={infra_viol}",
            file=sys.stderr,
        )
        return infra_results
    except Exception as e:
        print(f"[audit] 基础设施检查异常（已降级跳过）：{e}", file=sys.stderr)
        return None


def _resolve_projects(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """解析待审计项目列表：--repo-root 单仓模式 or lifecycle index 全量模式。"""
    if args.repo_root is not None:
        repo_root = args.repo_root.expanduser().resolve()
        name = args.project_name or repo_root.name or str(repo_root)
        return [(name, repo_root)]
    return load_registered_projects(args.lifecycle_index)


def _handle_no_projects(
    infra_results: dict[str, Any] | None,
    args: argparse.Namespace,
    lifecycle_index: Path,
) -> int:
    """处理无注册项目场景，返回退出码。"""
    print(
        f"[audit] 未发现注册项目（或 {lifecycle_index} 不存在），可用 --repo-root 指定单一仓库",
        file=sys.stderr,
    )
    report = build_report({}, infrastructure=infra_results)
    written = _maybe_write_report(report, args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.notify:
        notify_via_lark(report, str(written) if written else None)
    infra_crit = _count_critical_infra(infra_results)
    return 1 if infra_crit > 0 else 0


def _maybe_write_report(
    report: dict[str, Any],
    args: argparse.Namespace,
) -> Path | None:
    """按 CLI 参数决定是否写报告文件，返回写入路径（未写返回 None）。

    规则：
        - ``--no-write`` → 不写
        - ``--output``   → 写显式路径（父目录自动创建）
        - ``--report-only`` → 不写默认 audit 目录（无 --output 即不写）
        - 默认            → 写 <audit_dir>/daily-audit-YYYY-MM-DD.json
    """
    if args.no_write:
        return None
    if args.output is not None:
        out = write_report(report, output_path=args.output)
        print(f"[audit] 报告已写入: {out}", file=sys.stderr)
        return out
    if args.report_only:
        return None
    out = write_report(report)
    print(f"[audit] 报告已写入: {out}", file=sys.stderr)
    return out


def _audit_all_projects(
    projects: list[tuple[str, Path]],
    global_fingerprints: dict[str, str],
    expected_version: str | None,
) -> dict[str, dict[str, Any]]:
    """逐项目审计，单项目异常不影响整体。"""
    projects_results: dict[str, dict[str, Any]] = {}
    for name, root in projects:
        print(f"[audit] 检查项目: {name} ({root})", file=sys.stderr)
        try:
            projects_results[name] = audit_project(
                name, root, global_fingerprints, expected_version
            )
        except Exception as e:
            projects_results[name] = {
                "path": str(root),
                "violations": [
                    _make_violation(
                        "hash_mismatch",
                        "warning",
                        str(root),
                        f"项目巡检异常：{e}",
                    )
                ],
                "error": str(e),
            }
    return projects_results


def _summarize_to_console(
    projects_results: dict[str, dict[str, Any]],
    infra_results: dict[str, Any] | None,
    report: dict[str, Any],
) -> int:
    """打印控制台摘要，返回 critical 计数。"""
    crit = sum(
        1
        for r in projects_results.values()
        for v in r.get("violations", [])
        if v.get("severity") == "critical"
    )
    warn = sum(
        1
        for r in projects_results.values()
        for v in r.get("violations", [])
        if v.get("severity") == "warning"
    )
    if infra_results is not None:
        crit += _count_critical_infra(infra_results)
        warn += _count_warning_infra(infra_results)
    print(
        f"[audit] 完成: 项目={report['projects_checked']} "
        f"违规={report['total_violations']} (critical={crit}, warning={warn})",
        file=sys.stderr,
    )
    return crit


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Usage:
        infra-daily-audit --repo-root /path/to/repo --json
        infra-daily-audit                    # 扫描 lifecycle index 所有项目
        infra-daily-audit --no-infra         # 跳过基础设施检查
        infra-daily-audit --notify           # 扫描后通过 lark-cli 发飞书通知

    Returns:
        0  全部通过（无违规）
        0  有 warning 级别违规（巡检本身成功，不阻断）
        1  有 critical 级别违规（项目或基础设施）或 巡检过程出错
    """
    args = _parse_args(argv)
    expected_version = _resolve_expected_version(args.expected_version)

    # 0. 基础设施检查
    infra_results = _run_infra_check(args.no_infra, args.infra_inventory)

    # 1. 解析项目列表（--repo-root 单仓 or lifecycle index 全量）
    projects = _resolve_projects(args)
    if not projects:
        return _handle_no_projects(
            infra_results,
            args,
            args.lifecycle_index or _default_lifecycle_index(),
        )

    # 2. 预计算全局 KB 指纹
    global_fingerprints = build_global_kb_fingerprints(args.global_kb_root)
    print(
        f"[audit] 全局 KB 指纹: {len(global_fingerprints)} 个知识文件",
        file=sys.stderr,
    )

    # 3. 逐项目检查
    projects_results = _audit_all_projects(projects, global_fingerprints, expected_version)

    # 4. 组装 + 写报告
    report = build_report(projects_results, infrastructure=infra_results)
    written = _maybe_write_report(report, args)

    # 5. 控制台摘要
    crit = _summarize_to_console(projects_results, infra_results, report)

    # 6. 可选：JSON 到 stdout
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # 7. 可选：飞书通知
    if args.notify:
        notify_via_lark(report, str(written) if written else None)

    # 8. 退出码
    return 1 if crit > 0 else 0
