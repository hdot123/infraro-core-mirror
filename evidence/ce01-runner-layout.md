# runner 宿主 VM Runner 实例布局清册

**生成时间**：2026-09-24（v1 快照）  
**最近校准**：2026-10-11 追加 runner-org-memory-10（org 仓 hdot123-org/memory repo 级止血临时桥，PR #1277/#1278 卡队列），总量口径 9 → 10；前史：2026-09-28 四次：06:49 gh api 活体复测重写为 9 实例口径；晚 10 时段 runner-06~09 退役后二次校准为 5 实例口径（见 §3.1）；同日引擎仓专属池重建后回写为 9 实例口径（见 §3.3）；终局 muse-1 事件闭环回写 @17:16Z 复探（宿主外实例已离场，活体==清册 9，见 §4 回写记录）  
**主机**：runner 宿主 VM（内网跳板地址详见私有清册）  
**总量口径**：全账号 10 个 self-hosted runner 实例（宿主 VM 10：runner-hub 4 + scheduler 1 + 引擎仓 4 + org memory 仓 1[repo 级临时桥]）；2026-09-28 曾出现 owner 侧宿主外实例 muse-1（引擎仓池，08:0xZ 快照），当日 17:16Z 复探已不在任何池——事件闭环与回写见 §4（活体口径==本表，逐仓数字见 §2）

---

## 1. 实例清单（10 实例）

| No. | 目录 | 注册目标 | 属主 | 启动方式 | labels | 状态 |
|-----|------|----------|------|----------|--------|------|
| 1 | `/root/actions-runner-infraro-core-01` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-01.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 2 | `/root/actions-runner-infraro-core-02` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-02.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 3 | `/root/actions-runner-infraro-core-03` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-03.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 4 | `/root/actions-runner-infraro-core-04` | hdot123/runner-hub | runner | `setsid nohup ./run.sh > /tmp/runner-04.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 5 | `/root/actions-runner-infraro-core-05` | hdot123/scheduler | runner | `setsid nohup ./run.sh > /tmp/runner-05.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 6 | `/root/actions-runner-infraro-core-06` | hdot123/infraro-core | runner | `setsid nohup ./run.sh > /tmp/runner-06.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 7 | `/root/actions-runner-infraro-core-07` | hdot123/infraro-core | runner | `setsid nohup ./run.sh > /tmp/runner-07.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 8 | `/root/actions-runner-infraro-core-08` | hdot123/infraro-core | runner | `setsid nohup ./run.sh > /tmp/runner-08.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 9 | `/root/actions-runner-infraro-core-09` | hdot123/infraro-core | runner | `setsid nohup ./run.sh > /tmp/runner-09.log 2>&1 < /dev/null &` | `[self-hosted, Linux, X64, pve-linux]` | online |
| 10 | `/root/actions-runner-org-memory-10` | hdot123-org/memory（repo 级） | runner | `setsid nohup su - runner -c ./run.sh` | `[self-hosted, Linux, X64, pve-linux]` | online（2026-10-11 部署，临时桥：org 仓 CI 止血用） |

注：

- runner-06~09 为 2026-09-28 重建实例（06:10~08:07 本地时段逐台注册；2026-09-25 首建、同日退役的完整闭环登记见 §3.1/§3.3），依据用户 2026-09-28 指令。
- runner-org-memory-10 为 2026-10-11 止血部署（org 仓 hdot123-org/memory PR #1277/#1278 卡队列），定性为**临时桥**：org 仓 workflows 重锚 ubuntu-latest 后应注销（注销走 §4 checklist）。串行执行属预期（单实例 repo 级池消化 job 慢是正常的）。
- muse-1（2026-09-28 08:0xZ 出现于引擎仓池的 owner 侧宿主外实例）：终局复探（同日 17:16Z）已不在任何池，本表按活体口径维持 9 实例；其出现→离场事件闭环与回写 checklist 记录见 §4。

---

## 2. 活体核实（gh api 只读实测 @ 2026-09-28 三次）

探针命令（任一时点可复跑对账）：

```sh
for repo in infraro-core runner-hub scheduler infraro; do
  gh api "/repos/hdot123/$repo/actions/runners" --jq '{total_count, runners: [.runners[] | {name, status, busy}]}'
done
```

### hdot123/infraro-core

```json
{
  "total_count": 5,
  "runners": [
    {"name": "runner-06", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-07", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-08", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-09", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "muse-1", "status": "online", "busy": true, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

（引擎仓专属池 4 台 runner-06~09 已于 2026-09-28 重建注册并恢复 online；同窗口 owner 侧宿主外实例 muse-1 亦在池（本快照时点），后于同日离场，事件闭环见 §4。重建后积压 5 条 queued runs 全部离开队列，拾取明细见 §3.3。终局复探 @2026-09-28T17:16Z：引擎仓 total_count=4（runner-06~09 全 online），全账号 9。）

### hdot123/runner-hub

```json
{
  "total_count": 4,
  "runners": [
    {"name": "runner-01", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-02", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-03", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]},
    {"name": "runner-04", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

### hdot123/scheduler

```json
{
  "total_count": 1,
  "runners": [
    {"name": "runner-05", "status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "pve-linux"]}
  ]
}
```

### hdot123/infraro

```json
{
  "total_count": 0,
  "runners": []
}
```

（infraro 无 runner：声明仓聚合 job 走 hosted 执行面。）

### 已注销仓（对照）

hdot123/consumer-a、hdot123/consumer-b：仓与 runners 端点均 404（2026-09-28 复测确认）。登记见 §3。

---

## 3. 注销登记

### 3.1 runner-06~09（引擎仓专属池，2026-09-28 退役）

| 项 | 记录 |
|----|------|
| 注销对象 | `runner-06`、`runner-07`、`runner-08`、`runner-09`（2026-09-25 注册于 hdot123/infraro-core，前序「自建化切流」mission 所建；用户 2026-09-28 裁定退役） |
| 时间线 | 2026-09-28 上午：本会话复测发现注册已解绑（runners API `total_count=0`）且宿主目录 `/root/actions-runner-infraro-core-06~09` 已删、Listener 进程无残留——清理动作由并行执行的 feature `v2-f0-runner-06-09-retire` worker 完成（会话日志 2026-09-28-sessions.md 10:00:35 起，用户同窗口下达「runner-06~09直接删掉干净」指令） |
| 宿主侧核验 | 实例目录已删（ls 404）；Listener 进程 5 个（01~05），06~09 零残留；runnerdata 挂载 `/var/lib/actions-runner`（98G）仅含 runner-hub 域历史 `_work`（infra-core/memory/mencbo，2026-09-17 后未再更新），无需清理 |
| 复核时点 | 2026-09-28 ~10:15 本地会话三仓 gh api 复测：infraro-core=0 / runner-hub=4 / scheduler=1，总口径 5 |
| 闭环 | 退役态由同日重建终结：runner-06~09 已按用户指令重建并注册（重建登记见 §3.3），本条仅留注销时间线为历史记录 |

### 3.2 consumer-a / consumer-b（2026-09-25）

| 项 | 记录 |
|----|------|
| 注销对象 | `pve-runner-consumer-a`、`pve-runner-consumer-b`（注册于各自仓命名空间，id=22） |
| 时间线 | 2026-09-25：两仓 runner 先解绑注销（删除前 `total_count=0` 实证）→ 两仓删除（gh api 404）→ scheduler PR #29「退役测试消费仓」squash commit `52a5d73`（2026-09-25T15:23:53Z）从白名单移除两行 |
| 宿主侧清理 | 实例目录已删除、注册令牌已 shred（前序退役记录实证）；无孤儿进程/目录残留 |
| 唯一副本归档 | Mac 本地 clone 曾持未推送 commit `e77971d`（`fix(workflow): PEP 668 venv wrapper for pip install`，分支 feat/selfhosted-runs-on-3；远端仓已删故为唯一副本）。2026-09-28 已以 `git bundle create --all` 全量归档至 `~/archives/consumer-a-e77971d.bundle`（11 refs 完整历史；`git bundle verify` 通过；从 bundle 裸克隆可读出该 commit）。归档位在引擎仓与 mission 目录之外，不公开、不进任何 git 仓 |

### 3.3 runner-06~09 重建（2026-09-28，用户指令例外二）

| 项 | 记录 |
|----|------|
| 重建对象 | `runner-06`、`runner-07`、`runner-08`、`runner-09`（首建于 2026-09-25「自建化切流」mission，2026-09-28 上午退役，注销登记见 §3.1；本条为退役-重建闭环的重建腿） |
| 指令依据 | 用户 2026-09-28 指令「你先自建 run-06 到 09 runner 吧」（边界文件例外二：仅此四台此一次；其余 runner 禁触碰；除注册动作外零远端写） |
| 注册目标 | hdot123/infraro-core（引擎仓专属执行池，`--url https://github.com/hdot123/infraro-core`） |
| labels | `self-hosted,pve-linux`（config.sh 显式指定；API 展开为 `[self-hosted, Linux, X64, pve-linux]`，契约值一致） |
| 执行过程 | 逐台：宿主建目录 `/root/actions-runner-infraro-core-0N`（属主 runner:docker 755）→ 解包 runner 2.337.0 linux-x64（sha256 70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613，与 actions/runner v2.337.0 release asset digest 一致，与宿主在役版本对齐）→ `./config.sh --unattended` 注册（registration token 经 shell 变量与命令行参数一次传递，零打印零落盘）→ `setsid nohup ./run.sh > /tmp/runner-0N.log` 启动（禁 systemctl，§5 口径）。06:10 首台注册，08:04 末台启动 |
| API 核验 | `gh api /repos/hdot123/infraro-core/actions/runners` @ 08:0xZ：total_count=5，runner-06/07/08/09 全 online、labels 含 pve-linux（+owner 侧 muse-1，事件闭环见 §4；终局复探 @17:16Z total_count=4） |
| 宿主侧核验 | Listener 进程 9 个（01~09）；06~09 四实例 `.runner` 的 gitHubUrl 均为 hdot123/infraro-core；01~05 实例目录与 `.runner` mtime 未动（零触碰） |
| 恢复拾取 | 重建前积压 5 条 queued runs 全部离开队列：Gate 2 Cross-repository Inspection（06:51Z）与 Engine Consumer Audit（07:57Z）由重建池 runner-06 执行 success（runner_name 有录，口径关注 run Evolution Scan 05:27Z 亦恢复拾取、muse-1 执行 success）；Actions Budget Guard 与 Branch Cleanup 被取消（非本 worker 动作，无拾取记录）。Droid Auto Review 03:02Z dispatch run 在拾取前已被 owner 侧取消（重派属发版腿范围，未动） |
| 备注 | 同窗口 owner 在引擎仓池自注册宿主外实例 muse-1（在线，宿主 VM 本机无目录/进程），非本次重建产物。终局（本轮回写）：muse-1 已离场（17:16Z 复探不在任何池），清册按活体口径维持 9；其出现→离场事件与回写记录见 §4；探针 T2-01/T2-02/T2-06 已收敛至活体口径（未登记外来实例一律 FAIL） |

---

## 4. runner 注册/注销回写机制（checklist）

本清册是快照性登记文档（registry of record）：注册真源 = 各仓 runners API + 宿主 VM 本机；容量基线 = 活体 gh。清册与真源之间唯一的合法关系是**回写对账**——任何 runner 注册/注销动作，未回写本清册即视为流程未完成。

### 注册 checklist

1. 按声明仓 runbook（docs/runner-registration-runbook.md）注册 runner；
2. runners API 核验：name / status / labels / total_count；
3. 宿主侧核验：实例目录、进程、日志落点；
4. **回写本清册**：§1 实例清单补行 + §2 活体核实段带时点戳更新。

### 注销 checklist

1. runner 解绑注销，API `total_count` 复核；
2. 仓删除/退役时清理宿主目录与凭证（按退役记录格式留存证据）；
3. **回写本清册**：§1 删行 + §3 追加注销登记（时间线、依据 commit/PR、宿主清理、唯一副本归档去向）。

### 回写记录

| 日期 | 实例 | 动作 | 依据 | checklist 落点 | 结果 |
|------|------|------|------|----------------|------|
| 2026-10-11 | runner-org-memory-10 | 注册（止血临时桥） | org 仓 hdot123-org/memory PR #1277/#1278 卡队列 | §1 表行 + §4 本记录 | 完成（注册与回写同窗；定性临时桥，重锚 ubuntu-latest 后按注销 checklist 退役） |
| 2026-09-28 | runner-06~09 | 注册（重建） | 用户指令（边界例外二） | §3.3 重建登记 + §1 表行 + §2 时点戳 | 完成（注册与回写同窗） |
| 2026-09-28 | muse-1（owner 侧宿主外） | 出现登记→离场闭环 | owner 侧自注册 + 终审裁决「清册补行」 | 本表 + §1 备注 + §2 终局复探戳（17:16Z） | 完成：离场后按活体口径回写（未在池 → 不占 §1 表行；再注册按注册 checklist 重走） |

> muse-1 补行口径说明：清册与真源的唯一合法关系是回写对账（本 §4 前言）；终局复探时 muse-1 已不在任何池，故 §1 实例表不新增行（表 == 活体 9），事件本身以本回写记录在案——这即「补行」的闭合形态：不留「待裁定」悬挂态。

### 周期对账

清册 ↔ 活体 gh 定期 diff（live-truth 探针覆盖：三 runner 仓逐仓比对 + 死行零残留 + 总数断言）。diff 非零时先修现实或先修清册并复探，禁止静默漂移。2026-09-28 三次校准即例：晨间为双维度漂移（注册 06~09 未回写 + 注销 consumer 未回写）的事后修复；午前为 06~09 退役后的口径收敛；同日重建后随即回写（本轮，注册与回写同窗完成——回写纪律首次全流程闭环）。

---

## 5. 运维注意事项

### 启停命令（禁用 systemctl）

runner 宿主 VM 上 runner 实例**禁用 systemctl**。正确启停方式：

- **停止**：`kill` 进程树（Listener 及其父 bash），然后确认无残留
- **启动**：以 **runner 用户**执行 `setsid nohup ./run.sh > <日志路径> 2>&1 < /dev/null &`
- **配置**：使用 `./config.sh` 命令，参数通过环境变量传入 token（不回显）

（遗留 systemctl 单元 inactive，无效且 disable 有误伤风险，勿用。）

### 边界约束（NEVER VIOLATE）

- runner-01~04（runner-hub 私有信任域）禁止触碰；引擎仓 runner 操作（含 06~09 重建）需走引擎仓变更流程 + §4 checklist 回写
- muse-1（owner 侧宿主外实例）为 owner 自管理，禁触碰；如重新注册，按 §4 注册 checklist 回写本清册（不留悬挂态）
- 已注销实例（consumer-a/b）不适用本表；如恢复注册必须走 §4 checklist
- pve 上其他 VM（mysql-01/apisix-gw-01/openwrt/Tailscale-Gateway/debian12-01）禁止操作
- token 值绝不落盘、不写日志、不进 PR diff

### 内网地址说明

本清册为脱敏版本，runner VM 内网地址（含跳板机地址及宿主 VM 内网 IP）详见私有清册。自 2026-09-28 起本文件为唯一拓扑登记正本：前序 mission 私有全量版 `library/ce01-runner-layout-full.md`（前序 missionDir 私有路径）标注 superseded——该版同为 2026-09-24 快照（同缺 06~09、同含 consumer 死行），其拓扑内容不再维护，仅存敏感细节参考。

---

## 6. 迁移记录（runner-05，历史）

| 时间 | 事件 | runner-hub | scheduler | runner 宿主 VM 状态 |
|------|------|------------|-----------|---------------------|
| 迁移前 | runner-05 注册至 runner-hub | 5 | 0 | `/root/actions-runner-infraro-core-05` |
| 迁移后 | runner-05 重注册至 scheduler | 4 | 1 | 目录不变，config.sh 重配 |

**迁移后验证**：

- `gh api /repos/hdot123/runner-hub/actions/runners --jq .total_count` = 4
- `gh api /repos/hdot123/scheduler/actions/runners --jq .total_count` = 1
- `gh api /repos/hdot123/scheduler/actions/runners` 显示 runner-05 labels 含 `pve-linux`
