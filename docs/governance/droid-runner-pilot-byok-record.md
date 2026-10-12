# Droid Runner Pilot 治理归档：Runner 上 Droid 执行链路冒烟（仅 BYOK）

> **文档性质**：治理记录归档（governance record）。内容与基线 `.github/workflows/droid-runner-pilot.yml` 文件头注释（基线 `5fa9d9877ef9ad53c2206b6ce0779c39f3ba620f` 第 4-14 行）逐点一致，只加来源指针，无事实增删；附录收录基线注释原文。
>
> **背景**：pilot 工作流已完成历史使命，原试点定位为多工作器并发方案（GitHub 调度 → Droid 执行 → PR 交付）的链路验证。本文件把原先仅存活于该文件头注释与对象库不可达 commit 的治理数据点落入持久载体，保证后续从 workflows 目录移除该文件后仍可检索与回放。

## 1. BYOK-only 用户裁定（2026-09-08）

规则（2026-09-08 用户裁定）：Runner 上 droid 只允许 BYOK 路径（百炼 coding 端点 + BAILIAN_API_KEY），禁止 factory_credits 计费路径。

Factory 计费 job（smoke-test）已完成历史使命（run 34178894533 实测 13,677 credits/次后按裁定移除）。

## 2. 计费 job 移除指针

- 上游移除 commit：`e7ef5557cb05904bee998b6569fd9d7a2e806236`（hdot123，2026-09-08 10:45:11 +0800）
- 提交信息：`chore(ci): runner pilot BYOK-only per ruling, remove factory-credits smoke job (#254)`
- 指针性质说明：该 commit 属上游历史，不在本仓 ref 可达历史（squash 导入前发生），但完整存在于本仓对象库不可达上游历史，`git cat-file -p e7ef5557cb` 可直接读取回放。
- 本仓导入形态闭环：移除 commit 产出 blob `ed25aa1f7da36c980439579af4be86febbee3558` 与本仓导入 commit `3695445b1d8df8997de35d39256ffe596d229c8c`（2026-09-13，#2）中的 pilot 文件 blob 字节级相同。

## 3. 治理数据点（runs）

| run | 数据点 | 说明 |
|---|---|---|
| 34178894533 | 13,677 credits/次 | 上游 run，本仓 Actions API 已 404；数据点由基线文件头注释与移除 commit diff 双重记载 |
| 34180161953 | 0 factory_credits | BYOK 双端验证：本机 fresh-HOME + 云端 run 均 0 factory_credits |

## 4. 事实依据（2026-09-08 对照官方文档 + 本机 CLI 0.213.0 核验）

- 安装命令 `curl -fsSL https://app.factory.ai/cli | sh` 为官方文档记载
- CLI 安装路径未在文档钉死 → 装后探测（`command -v` 优先，常见目录兜底）
- droid exec 默认只读模式；冒烟只需输出语句，无需 `--auto` 提权
- 退出码契约：0=成功，非零=失败（权限违规/工具错误/目标未达成）
- BYOK 双端验证：本机 fresh-HOME + 云端 run 34180161953 均 0 factory_credits

## 5. 来源指针

- 基线文件头注释原文（第 4-14 行治理记录）：`git show 5fa9d9877ef9ad53c2206b6ce0779c39f3ba620f:.github/workflows/droid-runner-pilot.yml`
- 上游移除 commit（含裁定摘要与计费 job diff）：`git cat-file -p e7ef5557cb05904bee998b6569fd9d7a2e806236`
- 本仓导入 commit（pilot blob 闭环）：`git cat-file -p 3695445b1d8df8997de35d39256ffe596d229c8c`

## 附录：基线文件头注释原文（第 3-14 行）

```text
# 多工作器并发方案（GitHub 调度 → Droid 执行 → PR 交付）的链路验证。
# 规则（2026-09-08 用户裁定）：Runner 上 droid 只允许 BYOK 路径
# （百炼 coding 端点 + BAILIAN_API_KEY），禁止 factory_credits 计费路径。
# Factory 计费 job（smoke-test）已完成历史使命（run 34178894533 实测
# 13,677 credits/次后按裁定移除）。
#
# 事实依据（2026-09-08 对照官方文档 + 本机 CLI 0.213.0 核验）：
# - 安装命令 curl -fsSL https://app.factory.ai/cli | sh 为官方文档记载
# - CLI 安装路径未在文档钉死 → 装后探测（command -v 优先，常见目录兜底）
# - droid exec 默认只读模式；冒烟只需输出语句，无需 --auto 提权
# - 退出码契约：0=成功，非零=失败（权限违规/工具错误/目标未达成）
# - BYOK 双端验证：本机 fresh-HOME + 云端 run 34180161953 均 0 factory_credits
```
