# runner 宿主 VM Runner 实例布局清册

**生成时间**：2026-09-24  
**主机**：runner 宿主 VM（内网跳板地址详见私有清册）  
**注**：runner 宿主 VM 上共 7 个 self-hosted runner 实例，其中 4 台属 runner-hub（私有信任域/高敏感任务），2 台专属 consumer-a/b（私有消费仓），1 台（runner-05）已迁移至 hdot123/scheduler 专用于引擎工作流。

---

## 实例清单（7 实例）

| No. | 目录 | 注册目标 | 属主 | 启动方式 | labels | 状态 |
|-----|------|----------|------|----------|--------|------|
| 1 | `/root/actions-runner-infraro-core-01` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-01.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 2 | `/root/actions-runner-infraro-core-02` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-02.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 3 | `/root/actions-runner-infraro-core-03` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-03.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 4 | `/root/actions-runner-infraro-core-04` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-04.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 5 | `/root/actions-runner-infraro-core-05` | hdot123/scheduler（**已迁移**） | runner | `setsid nohup ./run.sh > /tmp/runner-05.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 6 | `/root/actions-runner-consumer-a` | hdot123/consumer-a | runner | `setsid nohup ./run.sh > /tmp/consumer-a.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 7 | `/root/actions-runner-consumer-b` | hdot123/consumer-b | runner | `setsid nohup ./run.sh > /tmp/consumer-b.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |

---

## 状态核实（2026-09-24）

### hdot123/runner-hub runners
```json
{
  "total_count": 4,
  "runners": [
    {"name": "runner-01", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-02", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-03", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-04", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

### hdot123/scheduler runners
```json
{
  "total_count": 1,
  "runners": [
    {"name": "runner-05", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

### hdot123/consumer-a runners
```json
{
  "total_count": 1,
  "runners": [
    {"name": "pve-runner-consumer-a", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

### hdot123/consumer-b runners
```json
{
  "total_count": 1,
  "runners": [
    {"name": "pve-runner-consumer-b", "status": "online", "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

---

## 运维注意事项

### 启停命令（禁用 systemctl）
runner 宿主 VM 上 runner 实例**禁用 systemctl**。正确启停方式：
- **停止**：`kill` 进程树（Listener 及其父 bash），然后确认无残留
- **启动**：以 **runner 用户**执行 `setsid nohup ./run.sh > /tmp/runner-xx.log 2>&1 < /dev/null &`
- **配置**：使用 `./config.sh` 命令，参数通过环境变量传入 token（不回显）

示例：
```bash
# 弊端：systemctl restart/actions.runner.hdot123-org.xxx（遗留单元 inactive，无效且 disable 有误伤风险）
# 正确：kill 进程树 + sudo -u runner ./config.sh/remove --token $TOKEN
```

### 边界约束（NEVER VIOLATE）
- runner-01~04 与 consumer-a/b runner **禁止触碰**
- pve 上其他 VM（mysql-01/apisix-gw-01/openwrt/Tailscale-Gateway/debian12-01）禁止操作
- token 值绝不落盘、不写日志、不进 PR diff

### 内网地址说明
本清册为脱敏版本，runner VM 内网地址（含跳板机地址及 runner 宿主 VM 内网 IP）详见私有清册：`library/ce01-runner-layout-full.md`（missionDir 私有路径）。

---

## 迁移记录（runner-05）

| 时间 | 事件 | runner-hub | scheduler | runner 宿主 VM 状态 |
|------|------|------------|-----------|---------------------|
| 迁移前 | runner-05 注册至 runner-hub | 5 | 0 | `/root/actions-runner-infraro-core-05` |
| 迁移后 | runner-05 重注册至 scheduler | 4 | 1 | 目录不变，config.sh 重配 |

**迁移后验证**：
- `gh api /repos/hdot123/runner-hub/actions/runners --jq .total_count` = 4 ✓
- `gh api /repos/hdot123/scheduler/actions/runners --jq .total_count` = 1 ✓
- `gh api /repos/hdot123/scheduler/actions/runners` 显示 runner-05 labels 含 `pve-linux` ✓
