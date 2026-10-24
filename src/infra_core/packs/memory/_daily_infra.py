"""每日记忆巡检基础设施层：清单加载、SSH/TCP/磁盘/systemd 探测、
服务器与数据库健康检查。

v3 拆分：自 daily_audit.py（原 memory_core/tools/_audit_infra.py + _audit_server.py 迁移整合）拆出。
PyYAML 可选依赖（yaml / _HAS_YAML）随 _load_infra_inventory 定义在本模块。"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from ._daily_base import (
    HTTP_TIMEOUT,
    SSH_CONNECT_TIMEOUT,
    SSH_TIMEOUT,
    TCP_TIMEOUT,
    _default_infra_inventory,
    _make_violation,
    _shell_quote,
)

try:
    import yaml

    _HAS_YAML = True
except ImportError:  # pragma: no cover - 缺 PyYAML 时跳过基础设施检查
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# 基础设施底层（原 _audit_infra）
# ---------------------------------------------------------------------------


def _load_infra_inventory(inventory_path: Path | None = None) -> dict[str, Any] | None:
    """加载基础设施清单 YAML。

    Returns:
        dict: 解析后的清单（含 servers / databases 键，可能为空列表）。
        None: 文件不存在、不可解析、或 PyYAML 不可用（调用方据此跳过）。
    """
    if not _HAS_YAML:
        print(
            "[infra] PyYAML 不可用，跳过基础设施检查 （可 `pip install pyyaml` 启用）",
            file=sys.stderr,
        )
        return None
    inv_path = inventory_path or _default_infra_inventory()
    if not inv_path.exists():
        print(
            f"[infra] 清单文件不存在：{inv_path}，跳过基础设施检查",
            file=sys.stderr,
        )
        return None
    try:
        data = yaml.safe_load(inv_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        print(
            f"[infra] 清单解析失败：{e}，跳过基础设施检查",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print("[infra] 清单顶层不是 mapping，跳过基础设施检查", file=sys.stderr)
        return None
    return data


def _tcp_connect_ok(host: str, port: int, timeout: int = TCP_TIMEOUT) -> bool:
    """TCP connect 探测，成功返回 True，超时/拒绝/错误返回 False。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (TimeoutError, ConnectionRefusedError, OSError):
        return False


def _run_ssh(
    ssh_alias: str,
    remote_cmd: list[str],
    timeout: int = SSH_TIMEOUT,
) -> tuple[int, str, str]:
    """以 BatchMode 执行一条 SSH 命令，返回 (rc, stdout, stderr)。"""
    cmd = [
        "ssh",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-o",
        "BatchMode=yes",
        ssh_alias,
        *remote_cmd,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", "ssh 命令未找到"
    except subprocess.TimeoutExpired:
        return 124, "", f"SSH 超时（>{timeout}s）"
    return result.returncode, result.stdout, result.stderr


def check_ssh_reachable(ssh_alias: str) -> bool:
    """检查 SSH 是否可达（`ssh <alias> echo ok`）。"""
    rc, out, _ = _run_ssh(ssh_alias, ["echo", "ok"])
    return rc == 0 and out.strip() == "ok"


def _parse_df_output(out: str) -> dict[str, dict[str, Any]]:
    """Parse df -h -P output into filesystems dict."""
    filesystems: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("Filesystem"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        size, used, avail = parts[1], parts[2], parts[3]
        use_pct_str = parts[4].rstrip("%")
        mount = " ".join(parts[5:])
        try:
            use_pct = int(use_pct_str)
        except ValueError:
            continue
        filesystems[mount] = {"size": size, "used": used, "avail": avail, "use_pct": use_pct}
    return filesystems


def _find_matching_mount(
    disk_check: dict[str, Any], filesystems: dict[str, dict[str, Any]]
) -> str | None:
    """Find matching mount point for a disk check config."""
    mount_config = disk_check.get("mount")
    pattern = disk_check.get("pattern")
    if mount_config:
        mount_str = str(mount_config)
        return mount_str if mount_str in filesystems else None
    if pattern:
        for fs_mount in filesystems:
            if re.search(pattern, fs_mount):
                return fs_mount
    return None


def check_disk_space(
    ssh_alias: str,
    server_name: str,
    disk_checks: list[dict[str, Any]],
    global_violations: list[dict[str, Any]],
    record_violations: list[dict[str, Any]],
) -> dict[str, Any]:
    """通过 SSH 检查磁盘空间使用率。

    用一条 SSH 命令执行 ``df -P`` 获取所有挂载点信息，
    然后逐个对比配置的阈值。超过 warn_pct 报 warning，
    超过 crit_pct 报 critical。

    磁盘满了会导致 MySQL 写入失败、Docker 构建失败、日志丢失等严重问题。

    Args:
        ssh_alias: SSH 别名。
        server_name: 服务器名（用于违规 file 字段前缀）。
        disk_checks: 磁盘检查配置列表，每项含 mount/pattern、warn_pct、crit_pct。
        global_violations: 全局违规列表（就地追加）。
        record_violations: 当前 server record 的违规列表（就地追加）。

    Returns:
        {mount_point: {size, used, avail, use_pct, status}} 磁盘使用情况。
    """
    result: dict[str, Any] = {}

    if not disk_checks:
        return result

    # 用一条 SSH 命令获取所有挂载点信息
    # df -P: POSIX 输出格式，保证一行一个文件系统
    rc, out, _err = _run_ssh(
        ssh_alias,
        ["df", "-h", "-P"],
    )
    if rc != 0:
        v = _make_violation(
            "disk_full",
            "warning",
            f"{server_name} (df)",
            f"df 命令执行失败 (rc={rc})，无法检查磁盘空间",
        )
        record_violations.append(v)
        global_violations.append(v)
        return result

    # 解析 df -h -P 输出
    filesystems = _parse_df_output(out)

    # 逐个配置项检查
    for dc in disk_checks:
        if not isinstance(dc, dict):
            continue
        warn_pct = int(dc.get("warn_pct", 80))
        crit_pct = int(dc.get("crit_pct", 90))

        # 通过 mount 精确匹配或 pattern 正则匹配
        matched_mount = _find_matching_mount(dc, filesystems)

        if matched_mount is None:
            label = dc.get("mount") or dc.get("pattern") or "?"
            v = _make_violation(
                "disk_full",
                "warning",
                f"{server_name}:{label}",
                f"未找到匹配的挂载点: {label}",
            )
            record_violations.append(v)
            global_violations.append(v)
            continue

        fs_info = filesystems[matched_mount]
        result[matched_mount] = fs_info

        use_pct = fs_info["use_pct"]
        fs_info["status"] = "ok"

        if use_pct >= crit_pct:
            fs_info["status"] = "critical"
            v = _make_violation(
                "disk_full",
                "critical",
                f"{server_name}:{matched_mount}",
                f"磁盘空间严重不足：{matched_mount} 使用 {use_pct}% "
                f"(>={crit_pct}%)，剩余 {fs_info['avail']}，"
                f"总量 {fs_info['size']}（MySQL/Docker 有写入失败风险）",
            )
            record_violations.append(v)
            global_violations.append(v)
        elif use_pct >= warn_pct:
            fs_info["status"] = "warning"
            v = _make_violation(
                "disk_full",
                "warning",
                f"{server_name}:{matched_mount}",
                f"磁盘空间不足：{matched_mount} 使用 {use_pct}% "
                f"(>={warn_pct}%)，剩余 {fs_info['avail']}，"
                f"总量 {fs_info['size']}",
            )
            record_violations.append(v)
            global_violations.append(v)

    return result


def _check_systemd_services(
    ssh_alias: str,
    server_name: str,
    services: list[str],
    global_violations: list[dict[str, Any]],
    record_violations: list[dict[str, Any]],
) -> dict[str, str]:
    """通过 SSH 批量查询 systemd 服务状态。

    用一条 SSH 命令遍历所有服务（`systemctl show ... --property=`），
    解析 LoadState/ActiveState/SubState，对每个期望服务判断：

        - ActiveState=active 且 SubState=running → "running"
        - LoadState=not-found → warning（服务未安装，可能不适用于此机）
        - 其他异常 → critical（service_down）
        - systemctl 命令执行失败 → warning（无法核对，疑似权限问题）

    Args:
        ssh_alias: SSH 别名。
        server_name: 服务器名（用于违规 file 字段前缀）。
        services: 期望检查的 systemd 服务名列表。
        global_violations: 全局违规列表（就地追加）。
        record_violations: 当前 server record 的违规列表（就地追加）。

    Returns:
        {service_name: status_str}，status_str 为 "running" / 状态描述。
        未解析到输出的服务记为 "unknown"。
    """
    statuses: dict[str, str] = {}

    if not services:
        return statuses

    # 批量查询：一条 SSH 命令遍历所有服务，避免多次往返。
    # 注意：必须把整段脚本作为「单个字符串」传给 _run_ssh（即单元素 list），
    # 否则 ssh 客户端会把多个 argv 用空格拼接后送远端 shell，导致
    # 多行脚本被重新分词而破坏（见 ssh(1) 的 command 拼接行为）。
    services_quoted = " ".join(_shell_quote(s) for s in services)
    remote_script = (
        f"for svc in {services_quoted}; do\n"
        '  echo "=== $svc ==="\n'
        '  systemctl show "$svc" '
        "--property=LoadState,ActiveState,SubState --no-pager\n"
        "done\n"
    )
    rc, out, _err = _run_ssh(ssh_alias, [remote_script])

    if rc != 0:
        # systemctl 整体不可用（权限/PATH 问题），逐个标 warning
        detail = (
            f"systemctl 批量查询执行失败 (rc={rc})，无法核对服务状态（疑似权限或 systemd 未安装）"
        )
        v = _make_violation(
            "service_down",
            "warning",
            f"{server_name} (systemctl)",
            detail,
        )
        record_violations.append(v)
        global_violations.append(v)
        for svc in services:
            statuses[svc] = "unknown"
        return statuses

    # 解析输出：=== <svc> === 块，块内是 LoadState/ActiveState/SubState 三行
    current_svc: str | None = None
    current_props: dict[str, str] = {}
    blocks: dict[str, dict[str, str]] = {}

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("=== ") and line.endswith(" ==="):
            # 保存上一个块
            if current_svc is not None:
                blocks[current_svc] = current_props
            current_svc = line[4:-4].strip()
            current_props = {}
        elif "=" in line and current_svc is not None:
            key, _, val = line.partition("=")
            current_props[key.strip()] = val.strip()
    # 收尾最后一个块
    if current_svc is not None:
        blocks[current_svc] = current_props

    # 逐个期望服务判定
    for svc in services:
        props = blocks.get(svc)
        if not props:
            statuses[svc] = "unknown"
            v = _make_violation(
                "service_down",
                "warning",
                f"{server_name}/{svc}",
                f"systemd 服务 {svc} 未在输出中找到（解析失败或未安装）",
            )
            record_violations.append(v)
            global_violations.append(v)
            continue

        load_state = props.get("LoadState", "")
        active_state = props.get("ActiveState", "")
        sub_state = props.get("SubState", "")

        # LoadState=not-found → 服务未安装，warning（可能不适用此机）
        if load_state == "not-found":
            statuses[svc] = "not-found"
            v = _make_violation(
                "service_down",
                "warning",
                f"{server_name}/{svc}",
                f"systemd 服务 {svc} 未安装（LoadState=not-found）",
            )
            record_violations.append(v)
            global_violations.append(v)
            continue

        # 正常运行
        if active_state == "active" and sub_state == "running":
            statuses[svc] = "running"
            continue

        # 其他异常状态 → critical
        statuses[svc] = f"{active_state}/{sub_state}"
        v = _make_violation(
            "service_down",
            "critical",
            f"{server_name}/{svc}",
            f"systemd 服务 {svc} 状态异常：ActiveState={active_state}, SubState={sub_state}",
        )
        record_violations.append(v)
        global_violations.append(v)

    return statuses


# ---------------------------------------------------------------------------
# 服务器/数据库检查（原 _audit_server）
# ---------------------------------------------------------------------------


def _append_violation(
    record: dict[str, Any],
    global_violations: list[dict[str, Any]],
    violation: dict[str, Any],
) -> None:
    """DRY helper: append violation to both record and global list."""
    record["violations"].append(violation)
    global_violations.append(violation)


def _check_server_ssh(
    server: dict[str, Any],
    record: dict[str, Any],
    global_violations: list[dict[str, Any]],
) -> bool | None:
    """Check SSH connectivity for server. Returns ssh_ok status."""
    name = str(server.get("name", "unknown"))
    host = str(record["host"])
    checks = server.get("checks") or {}
    ssh_alias = server.get("ssh_alias")
    want_ssh = bool(checks.get("ssh")) and bool(ssh_alias)

    ssh_ok: bool | None = None
    if want_ssh:
        ssh_ok = check_ssh_reachable(str(ssh_alias))
        record["ssh_ok"] = ssh_ok
        if not ssh_ok:
            v = _make_violation(
                "server_unreachable",
                "critical",
                f"{host} ({ssh_alias})",
                f"SSH 不可达：{ssh_alias}",
            )
            _append_violation(record, global_violations, v)
    elif ssh_alias is None and bool(checks.get("ssh")):
        # 声明了 ssh=true 但缺 ssh_alias
        v = _make_violation(
            "server_unreachable",
            "critical",
            name,
            "checks.ssh=true 但缺少 ssh_alias 字段",
        )
        _append_violation(record, global_violations, v)

    return ssh_ok


def _check_server_docker(
    server: dict[str, Any],
    record: dict[str, Any],
    global_violations: list[dict[str, Any]],
    ssh_ok: bool | None,
) -> None:
    """Check Docker container status (depends on SSH)."""
    name = str(server.get("name", "unknown"))
    ssh_alias = server.get("ssh_alias")
    checks = server.get("checks") or {}
    expected_containers = checks.get("docker_containers") or []

    if expected_containers and ssh_ok:
        # 注意：_run_ssh 将 remote_cmd 用空格拼接发给远端 shell，
        # format 串含空格必须用单引号包裹，否则 shell 会拆成两个参数。
        rc, out, _err = _run_ssh(
            str(ssh_alias),
            ["docker", "ps", "--format", "'{{.Names}}: {{.Status}}'"],
        )
        running: dict[str, str] = {}
        if rc == 0:
            for line in out.splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                cname, _, cstatus = line.partition(":")
                running[cname.strip()] = cstatus.strip()
        else:
            v = _make_violation(
                "container_down",
                "warning",
                f"{name} ({ssh_alias})",
                f"docker ps 执行失败 (rc={rc})，无法核对容器状态",
            )
            _append_violation(record, global_violations, v)

        # 对照期望列表
        for expected in expected_containers:
            expected = str(expected)
            status = running.get(expected)
            if status is None:
                record["containers"][expected] = "DOWN"
                v = _make_violation(
                    "container_down",
                    "critical",
                    f"{name}/{expected}",
                    f"期望容器未运行：{expected}",
                )
                _append_violation(record, global_violations, v)
            else:
                record["containers"][expected] = status
                low = status.lower()
                if "restarting" in low or "unhealthy" in low:
                    v = _make_violation(
                        "container_down",
                        "warning",
                        f"{name}/{expected}",
                        f"容器状态异常：{expected} -> {status}",
                    )
                    _append_violation(record, global_violations, v)

    # else: SSH 已失败或未配置 docker_containers，跳过


def _check_server_ports(
    server: dict[str, Any],
    record: dict[str, Any],
    global_violations: list[dict[str, Any]],
) -> None:
    """Check TCP port connectivity."""
    host = str(record["host"])
    checks = server.get("checks") or {}

    for port in checks.get("ports") or []:
        port = int(port)
        ok = _tcp_connect_ok(host, port, timeout=3)
        record["ports"][str(port)] = ok
        if not ok:
            v = _make_violation(
                "port_closed",
                "critical",
                f"{host}:{port}",
                f"端口 {port} 不可达（TCP connect 失败）",
            )
            _append_violation(record, global_violations, v)


def _check_server_http_endpoints(
    server: dict[str, Any],
    record: dict[str, Any],
    global_violations: list[dict[str, Any]],
) -> None:
    """Check HTTP endpoint health via curl."""
    checks = server.get("checks") or {}

    for ep in checks.get("http_endpoints") or []:
        if not isinstance(ep, dict):
            continue
        url = ep.get("url")
        ep_name = str(ep.get("name") or url)
        expected_status = int(ep.get("expected_status", 200))
        if not url:
            continue
        cmd = [
            "curl",
            "-sf",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            str(HTTP_TIMEOUT),
            str(url),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=HTTP_TIMEOUT + 2,
            )
            status_str = (result.stdout or "").strip()
            try:
                status_code = int(status_str) if status_str else 0
            except ValueError:
                status_code = 0
        except FileNotFoundError:
            status_code = -1
        except subprocess.TimeoutExpired:
            status_code = -2

        ep_record: dict[str, Any] = {
            "status": status_code,
            "expected": expected_status,
            "ok": status_code == expected_status,
        }
        record["http_endpoints"][ep_name] = ep_record

        if status_code == -2:
            v = _make_violation(
                "http_error",
                "critical",
                url,
                f"HTTP 端点超时（>{HTTP_TIMEOUT}s）：{ep_name}",
            )
            _append_violation(record, global_violations, v)
        elif status_code in (-1, 0):
            v = _make_violation(
                "http_error",
                "critical",
                url,
                f"HTTP 端点连接失败：{ep_name}",
            )
            _append_violation(record, global_violations, v)
        elif status_code != expected_status:
            v = _make_violation(
                "http_error",
                "warning",
                url,
                f"HTTP 状态码 {status_code} != 期望 {expected_status}：{ep_name}",
            )
            _append_violation(record, global_violations, v)


def check_server(
    server: dict[str, Any],
    global_violations: list[dict[str, Any]],
) -> dict[str, Any]:
    """对单台服务器跑 SSH / Docker / 端口 / HTTP 检查。

    Args:
        server: inventory 里的一条 server 记录。
        global_violations: 累积违规列表（就地追加，便于汇总 total）。

    Returns:
        该服务器的检查结果子树（host / ssh_ok / containers / ports /
        http_endpoints / violations）。
    """
    host = str(server.get("host", ""))
    checks = server.get("checks") or {}

    record: dict[str, Any] = {
        "host": host,
        "ssh_ok": None,
        "containers": {},
        "ports": {},
        "http_endpoints": {},
        "disk_space": {},
        "violations": [],
    }

    ssh_alias = server.get("ssh_alias")

    # 6a. SSH 连通性
    ssh_ok = _check_server_ssh(server, record, global_violations)

    # 6b. Docker 容器（依赖 SSH 可达）
    _check_server_docker(server, record, global_violations, ssh_ok)

    # 6b2. systemd 服务状态（依赖 SSH 可达）
    expected_systemd = checks.get("systemd_services") or []
    if expected_systemd and ssh_ok:
        name = str(server.get("name", "unknown"))
        record["systemd_services"] = _check_systemd_services(
            ssh_alias=str(ssh_alias),
            server_name=name,
            services=[str(s) for s in expected_systemd],
            global_violations=global_violations,
            record_violations=record["violations"],
        )
    # else: SSH 已失败或未配置 systemd_services，跳过

    # 6b3. 磁盘空间检查（依赖 SSH 可达，防止磁盘满导致 MySQL/Docker 故障）
    disk_checks = checks.get("disk_space") or []
    if disk_checks and ssh_ok:
        name = str(server.get("name", "unknown"))
        normalized_checks: list[dict[str, Any]] = [
            {"path": d} if isinstance(d, str) else d for d in disk_checks
        ]
        record["disk_space"] = check_disk_space(
            ssh_alias=str(ssh_alias),
            server_name=name,
            disk_checks=normalized_checks,
            global_violations=global_violations,
            record_violations=record["violations"],
        )
    # else: SSH 已失败或未配置 disk_space，跳过

    # 6c. 端口连通性（Python socket，超时 3s 贴合规格）
    _check_server_ports(server, record, global_violations)

    # 6d. HTTP 端点健康检查（curl）
    _check_server_http_endpoints(server, record, global_violations)

    return record


def check_database(
    database: dict[str, Any],
    global_violations: list[dict[str, Any]],
) -> dict[str, Any]:
    """对单个数据库做 TCP connect 探测（6e）。"""
    name = str(database.get("name", "unknown"))
    host = str(database.get("host", ""))
    port = int(database.get("port", 0))

    record: dict[str, Any] = {
        "host": host,
        "port": port,
        "connect_ok": None,
        "violations": [],
    }

    # check 字段兼容 tcp_connect / mysql_ping（当前只实现 tcp_connect）
    check_kind = str(database.get("check", "tcp_connect")).lower()
    if check_kind != "tcp_connect":
        # 未支持的检查类型，按 warning 提示但不阻塞
        v = _make_violation(
            "db_unreachable",
            "warning",
            f"{host}:{port}",
            f"不支持的 database.check={check_kind}，仅支持 tcp_connect",
        )
        record["connect_ok"] = False
        record["violations"].append(v)
        global_violations.append(v)
        return record

    ok = _tcp_connect_ok(host, port, timeout=TCP_TIMEOUT)
    record["connect_ok"] = ok
    if not ok:
        v = _make_violation(
            "db_unreachable",
            "critical",
            f"{host}:{port}",
            f"数据库不可达：{name} ({host}:{port}) TCP connect 失败",
        )
        record["violations"].append(v)
        global_violations.append(v)

    return record


def check_infrastructure(inventory_path: Path | None = None) -> dict[str, Any]:
    """执行基础设施健康检查（第 6 项），返回报告 infrastructure 子树。

    结构:
        {
          "servers": { "<name>": {...} },
          "databases": { "<name>": {...} },
          "violations": [...]   # 全部基础设施违规（便于汇总）
        }
    """
    result: dict[str, Any] = {
        "servers": {},
        "databases": {},
        "violations": [],
    }

    data = _load_infra_inventory(inventory_path)
    if data is None:
        return result

    # 服务器
    for server in data.get("servers") or []:
        if not isinstance(server, dict):
            continue
        name = str(server.get("name", "unknown"))
        result["servers"][name] = check_server(server, result["violations"])

    # 数据库
    for database in data.get("databases") or []:
        if not isinstance(database, dict):
            continue
        name = str(database.get("name", "unknown"))
        result["databases"][name] = check_database(database, result["violations"])

    return result
