# 项目最高宪法（GOVERNANCE）

<!--
文件头决策记录（最高规则第 1 条第 1.2(a) 款之自举合规）
裁定日期：2026-10-06
裁定人：大哥（仓库所有者）
裁定内容：「先补最高宪法」——立最高规则第 1 条【自进化层与自愈层不可侵犯】，
采宪法先行路径：先立法，既有违宪态随后按第 1.2 节三条件流程处置（D-1）。
本文件由 feature governance-constitution 首次落地（立法 PR 即决策记录载体）。
-->

## 地位声明

本文件是本项目的最高治理文件，效力高于任何其他任务规则。任何下位规则与本
文件冲突的，以本文件为准。

## 最高规则第 1 条【自进化层与自愈层不可侵犯】

裁定日期：2026-10-06。裁定人：大哥（仓库所有者）。

### 1.1 层级宣言

自进化（self-evolution）与自愈（self-healing）是本项目最高层级的两个治理层。
所有任务规则——mission 指令、workflow 定义、agent 行为约束、自动化脚本、
临时运维操作——都不得破坏、禁用、绕过或实质性削弱这两层。

### 1.2 三条件合规路径

任何触及这两层的变更，必须同时满足以下三个条件；任一缺失即属违规：

- **(a) 显性带日期决策记录**：变更须在 PR 描述与对应文件头两个落点写明裁定
  内容与裁定日期；
- **(b) 大哥明确批准**：未经仓库所有者明确批准，不得变更两层资产；
- **(c) CI 断言同步更新为「新形态是预期的」**：两层形态演进时，必须把完整性
  断言改写为锁定新预期形态；不得以删除断言的方式消除断言。

### 1.3 Enforcement（执行机制）

CI 常驻两层完整性断言（`tests/test_governance_constitution_integrity.py`，
PR 与 main push 持续执行）。两层资产被破坏、禁用、绕过或实质性削弱的，一经
发现立即回滚，按 P0 事件处理。

---

## 附录 A：两层资产清单

> 清单核实基线：2026-10-06，main @ e989d9b（orchestrator 调研补全，worker 已逐项核实）。

### A.1 自愈层（self-healing）

| 资产 | 位置 | 说明 |
| --- | --- | --- |
| watchdog 主文件 | `.github/workflows/droid-review-watchdog.yml` | 事件门控入口；三模式面（quota-sweep / self-heal-rerun / cancel-on-ci-fail） |
| handlers 执行体 | `.github/workflows/droid-review-watchdog-handlers.yml` | reusable（workflow_call）；`mode` 输入选择 self-heal-rerun / cancel-on-ci-fail 双 handler |
| quota-sweep | watchdog 主文件内 job | BYOK 429 配额自愈（schedule 面；D-1 起随触发器恢复真实可达） |
| auto-merge 自愈面 | `.github/workflows/auto-merge-pipeline.yml` | BEHIND PR 的 update-branch self-heal |
| 运行时变量族 | 仓库 variables | `WATCHDOG_MAX_ATTEMPT` / `QUOTA_SCAN_WINDOW_HOURS` / `QUOTA_RECOVERY_WINDOW_SECONDS`（缺省回退内置默认） |

### A.2 自进化层（self-evolution）

| 资产 | 位置 | 说明 |
| --- | --- | --- |
| 扫描定时面 | `.github/workflows/evolution-scan.yml` | 引擎仓自扫 schedule（cron `17,47 * * * *`）+ workflow_call reusable |
| 心跳定时面 | `.github/workflows/evolution-heartbeat.yml` | 自扫 schedule（cron `53 */2 * * *`）+ workflow_call reusable；stale 时反向拉起扫描器 |
| 扫描引擎 | `src/infra_core/engine/evolution_scanner.py` | 演化发现全链（发现 → Issue → PR） |
| 心跳引擎 | `src/infra_core/engine/evolution_heartbeat.py` | 探活 + 自愈闭环 |
| 自审计引擎 | `src/infra_core/engine/evolution_self_audit.py` | 演化自审计 |
| 宿主侧生态 | 宿主机 memory hook 生态 | 会话记忆钩子（宿主侧资产，不在本仓 CI 断言面内） |

### A.3 违宪态登记与处置（宪法生效时点）

- `droid-review-watchdog.yml` 触发器曾为 dispatch-only 桩（M2 HOTFIX 中间态，
  上游导入，非本轮工作引入）——宪法生效后该形态即属违规态。**已处置
  （2026-10-06，大哥批准）**：消除动作 = D-1（恢复 workflow_run + schedule
  触发器并补 7 条运行时护栏，按第 1.2 节三条件走流程）；宪法完整性断言已
  按第 1.2(c) 款升级为触发器健康基线（触发器全开 + 三 job 与触发面无结构
  性互斥为新形态锁定）。两层资产自此不存在已知违宪态；再禁用即新违规。

## 附录 B：立法与修订记录

| 日期 | 事项 | 决策记录落点 |
| --- | --- | --- |
| 2026-10-06 | 立法：最高规则第 1 条 + 附录 A/B（宪法先行路径 B） | 本文件头注释 + 立法 PR 描述 |
| 2026-10-06 | 修订：A.3 违宪态处置完成——D-1 恢复 watchdog 触发器 + 7 护栏，断言升级为触发器健康基线（第 1.2(c) 款） | D-1 PR 描述 + watchdog 文件头决策记录 |
