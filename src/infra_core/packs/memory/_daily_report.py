"""每日记忆巡检报告层：单项目编排 audit_project、报告组装与写入、
飞书通知摘要。

v3 拆分：自 daily_audit.py（原 memory_core/tools/_audit_server.py 编排半 + _audit_report.py 迁移整合）拆出。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._daily_base import (
    LARK_NOTIFY_ENV,
    LARK_NOTIFY_TIMEOUT,
    MANIFEST_PATH_REL,
    SYSTEM_DIR,
    _default_audit_dir,
    _make_violation,
    _now_iso_local,
    is_memory_core_source_repo,
    now_iso,
)
from ._daily_checks import (
    check_global_residue,
    check_large_or_db_files,
    check_manifest_integrity,
    check_unsigned_files,
    check_version_consistency,
)

# ---------------------------------------------------------------------------
# 单项目编排（原 _audit_server.audit_project）
# ---------------------------------------------------------------------------


def _safe_check(
    check_fn: Callable[[], list[Any]], error_violation: Callable[[Exception], Any]
) -> list[Any]:
    """Wrapper for check execution with error handling."""
    try:
        return check_fn()
    except Exception as e:
        return [error_violation(e)]


def audit_project(
    project_name: str,
    project_root: Path,
    global_fingerprints: dict[str, str],
    expected_version: str | None = None,
) -> dict[str, Any]:
    """对单个项目跑全部 5 项检查，返回报告子树。

    Args:
        project_name: 项目名（报告 key）。
        project_root: 项目根目录。
        global_fingerprints: 全局 KB 归一化指纹（残留检测用）。
        expected_version: 期望记忆版本（None 时跳过期望值比对）。
    """
    record: dict[str, Any] = {
        "path": str(project_root),
        "violations": [],
    }

    # 跳过不存在的项目路径
    if not project_root.exists():
        record["violations"].append(
            _make_violation(
                "hash_mismatch",
                "warning",
                str(project_root),
                "项目路径不存在（可能已删除），跳过",
            )
        )
        record["skipped"] = True
        return record

    # memory-core 自身是只读源仓库，不持有业务 KB，跳过 KB 相关检查和版本一致性检查
    # （源仓库的版本号由自身维护，跑版本检查会产生误报）
    # 此外，源仓库的完整性签名在设计上被禁用（sign_project 拒绝签名源仓库），
    # 因此 manifest.json 不存在是预期行为，跑完整性检查会产生 HASH_MISMATCH 误报。
    is_source_repo = is_memory_core_source_repo(project_root.resolve())

    if not is_source_repo:
        checks = [
            (
                lambda: check_manifest_integrity(project_root),
                lambda e: _make_violation(
                    "hash_mismatch", "warning", MANIFEST_PATH_REL, f"检查异常：{e}"
                ),
            ),
            (
                lambda: check_unsigned_files(project_root),
                lambda e: _make_violation(
                    "unsigned_file", "warning", "memory/kb/", f"检查异常：{e}"
                ),
            ),
            (
                lambda: check_global_residue(project_root, global_fingerprints),
                lambda e: _make_violation("residue", "warning", "memory/kb/", f"检查异常：{e}"),
            ),
            (
                lambda: check_large_or_db_files(project_root),
                lambda e: _make_violation(
                    "large_file", "warning", str(project_root), f"检查异常：{e}"
                ),
            ),
            (
                lambda: check_version_consistency(project_root, expected_version),
                lambda e: _make_violation(
                    "version_mismatch", "warning", f"{SYSTEM_DIR}/", f"检查异常：{e}"
                ),
            ),
        ]

        for check_fn, error_fn in checks:
            violations = _safe_check(check_fn, error_fn)
            record["violations"].extend(violations)

    if is_source_repo:
        record["note"] = "memory-core 源仓库：跳过完整性签名/KB未签名/残留/大文件检查"

    return record


# ---------------------------------------------------------------------------
# 报告 & 输出（原 _audit_report）
# ---------------------------------------------------------------------------


def build_report(
    projects_results: dict[str, dict[str, Any]],
    infrastructure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装最终报告字典。

    Args:
        projects_results: 项目检查结果。
        infrastructure: 基础设施检查结果（可为 None 表示本次未执行）。
    """
    today = now_iso()[:10]  # Extract YYYY-MM-DD from full ISO timestamp
    project_violations = sum(len(r.get("violations", [])) for r in projects_results.values())
    infra_violations = 0
    if infrastructure is not None:
        infra_violations = len(infrastructure.get("violations", []))

    report: dict[str, Any] = {
        "audit_date": today,
        "audited_at": _now_iso_local(),
        "projects_checked": len(projects_results),
        "total_violations": project_violations + infra_violations,
        "projects": projects_results,
    }
    if infrastructure is not None:
        report["infrastructure_checked"] = True
        # 把汇总 violations 从子树里去掉后塞进报告（避免重复 + 便于消费者）
        infra_view = dict(infrastructure)
        infra_view.pop("violations", None)
        report["infrastructure"] = infra_view
    return report


def write_report(
    report: dict[str, Any],
    output_path: str | Path | None = None,
    audit_dir: Path | None = None,
) -> Path:
    """把报告写入 JSON 文件，返回写入路径。

    Args:
        report: build_report 产物。
        output_path: 显式输出路径（父目录自动创建）。
        audit_dir: 默认目录模式（<audit_dir>/daily-audit-YYYY-MM-DD.json）；
            None 时用 ``_default_audit_dir()``。
    """
    if output_path is not None:
        out_path = Path(output_path).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        base = audit_dir or _default_audit_dir()
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / f"daily-audit-{report['audit_date']}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# 飞书通知（lark-cli）
# ---------------------------------------------------------------------------


def _summarize_violation_block(
    label: str, viols: list[dict[str, Any]], lines: list[str]
) -> tuple[int, int]:
    """格式化违规块并追加到 lines,返回 (critical_count, warning_count)。"""
    c = sum(1 for v in viols if v.get("severity") == "critical")
    w = sum(1 for v in viols if v.get("severity") == "warning")
    lines.append(f"• {label}: {len(viols)} 条违规 (critical={c}, warning={w})")
    # 每个项目最多列 3 条详情，避免消息过长
    for v in viols[:3]:
        lines.append(f"    [{v.get('severity')}] {v.get('type')}: {v.get('detail')}")
    if len(viols) > 3:
        lines.append(f"    ...还有 {len(viols) - 3} 条")
    return c, w


def _summarize_report(report: dict[str, Any], report_path_hint: str | None = None) -> str:
    """生成飞书通知用的纯文本摘要。"""
    hint = report_path_hint or str(
        _default_audit_dir() / f"daily-audit-{report['audit_date']}.json"
    )
    lines: list[str] = []
    lines.append(f"📋 每日记忆巡检报告 {report['audit_date']}")
    lines.append(f"检查项目数: {report['projects_checked']}")
    lines.append(f"违规总数: {report['total_violations']}")
    lines.append("")

    if report["total_violations"] == 0:
        lines.append("✅ 全部项目通过，无违规。")
        # 即便无违规，也附上基础设施摘要（若有）
        _append_infra_summary(lines, report)
        lines.append("")
        lines.append(f"详细报告: {hint}")
        return "\n".join(lines)

    critical_count = 0
    warning_count = 0

    # 项目违规
    for name, rec in report["projects"].items():
        viols = rec.get("violations", [])
        if not viols:
            continue
        c, w = _summarize_violation_block(name, viols, lines)
        critical_count += c
        warning_count += w

    # 基础设施违规
    infra = report.get("infrastructure") or {}
    for kind in ("servers", "databases"):
        for _name, rec in (infra.get(kind) or {}).items():
            viols = rec.get("violations", [])
            if not viols:
                continue
            label = f"[infra/{kind}] {_name}"
            c, w = _summarize_violation_block(label, viols, lines)
            critical_count += c
            warning_count += w

    lines.insert(3, f"其中 critical={critical_count}, warning={warning_count}")

    # 基础设施摘要段
    _append_infra_summary(lines, report)

    lines.append("")
    lines.append(f"详细报告: {hint}")
    return "\n".join(lines)


def _summarize_ssh(rec: dict[str, Any]) -> str:
    """Return SSH status mark for a server record."""
    ssh_ok = rec.get("ssh_ok")
    return "✓" if ssh_ok else ("✗" if ssh_ok is False else "-")


def _summarize_systemd(rec: dict[str, Any]) -> str:
    """Return systemd services summary string."""
    systemd = rec.get("systemd_services") or {}
    if systemd:
        up_n = sum(1 for s in systemd.values() if s == "running")
        return f"systemd {up_n}/{len(systemd)}"
    return "systemd -"


def _summarize_containers(rec: dict[str, Any]) -> str:
    """Return containers summary string."""
    containers = rec.get("containers") or {}
    if containers:
        up_n = sum(
            1
            for s in containers.values()
            if s and s != "DOWN" and "restarting" not in s.lower() and "unhealthy" not in s.lower()
        )
        return f"容器 {up_n}/{len(containers)} 正常"
    return "容器 -"


def _summarize_ports(rec: dict[str, Any]) -> str:
    """Return ports summary string."""
    ports = rec.get("ports") or {}
    if ports:
        return f"端口 {sum(1 for v in ports.values() if v)}/{len(ports)}"
    return "端口 -"


def _summarize_http(rec: dict[str, Any]) -> str:
    """Return HTTP endpoints summary string."""
    https = rec.get("http_endpoints") or {}
    if https:
        return f"HTTP {sum(1 for v in https.values() if v.get('ok'))}/{len(https)}"
    return "HTTP -"


def _summarize_disks(rec: dict[str, Any]) -> str:
    """Return disk space summary string."""
    disks = rec.get("disk_space") or {}
    if not disks:
        return "磁盘 -"
    disk_parts = []
    for d_mount, d_info in disks.items():
        pct = d_info.get("use_pct", 0)
        avail = d_info.get("avail", "?")
        mark = "🔴" if pct >= 90 else ("🟡" if pct >= 80 else "✓")
        disk_parts.append(f"{d_mount} {mark}{pct}% (剩{avail})")
    return "磁盘 " + ", ".join(disk_parts)


def _summarize_database_entry(name: str, rec: dict[str, Any]) -> str:
    """Return formatted line for a single database entry."""
    ok = rec.get("connect_ok")
    mark = "✓" if ok else ("✗" if ok is False else "-")
    return f"  {name}: {mark}"


def _summarize_server_entry(name: str, rec: dict[str, Any]) -> list[str]:
    """Return two formatted lines for a single server entry."""
    ssh_mark = _summarize_ssh(rec)
    s_summary = _summarize_systemd(rec)
    c_summary = _summarize_containers(rec)
    p_summary = _summarize_ports(rec)
    h_summary = _summarize_http(rec)
    d_summary = _summarize_disks(rec)
    return [
        f"  {name}: SSH {ssh_mark}, {s_summary}, {c_summary}, {p_summary}, {h_summary}",
        f"        {d_summary}",
    ]


def _append_infra_summary(lines: list[str], report: dict[str, Any]) -> None:
    """向摘要里追加一段「🖥 基础设施」概览。无基础设施节点则跳过。"""
    infra = report.get("infrastructure")
    if not infra:
        return

    servers = infra.get("servers") or {}
    databases = infra.get("databases") or {}
    if not servers and not databases:
        return

    lines.append("")
    lines.append("🖥 基础设施:")

    for name, rec in servers.items():
        lines.extend(_summarize_server_entry(name, rec))

    for name, rec in databases.items():
        lines.append(_summarize_database_entry(name, rec))


def notify_via_lark(report: dict[str, Any], report_path_hint: str | None = None) -> bool:
    """通过 lark-cli 发送飞书通知（bot 身份）。

    CHAT_ID 从环境变量 LARK_AUDIT_CHAT_ID 读取。
    只有存在违规或环境变量明确要求时才发。
    返回是否成功发送。
    """
    chat_id = os.environ.get(LARK_NOTIFY_ENV)
    if not chat_id:
        print(
            f"[notify] 跳过飞书通知：环境变量 {LARK_NOTIFY_ENV} 未设置",
            file=sys.stderr,
        )
        return False

    summary = _summarize_report(report, report_path_hint)
    # lark-cli im +messages-send --chat-id <ID> --text "<TEXT>"
    cmd = [
        "lark-cli",
        "im",
        "+messages-send",
        "--chat-id",
        chat_id,
        "--text",
        summary,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=LARK_NOTIFY_TIMEOUT,
        )
    except FileNotFoundError:
        print("[notify] lark-cli 未安装，跳过飞书通知", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("[notify] lark-cli 执行超时，跳过飞书通知", file=sys.stderr)
        return False

    if result.returncode != 0:
        print(
            f"[notify] lark-cli 失败 (rc={result.returncode}): {result.stderr.strip() or result.stdout.strip()}",
            file=sys.stderr,
        )
        return False

    print(f"[notify] 已发送飞书通知到 chat {chat_id}", file=sys.stderr)
    return True
