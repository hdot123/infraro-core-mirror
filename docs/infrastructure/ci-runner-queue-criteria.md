# CI Runner 路由判据与 PR 门语义（v2 通行签流程）

> **性质**：仓库运维判据文档（operations runbook 片段）。判据三点来自 owner 2026-10-18
> 裁定（PR 全流程与通行签规则 v2 · runner 池恢复版），本文是其仓库侧运维落点；
> 与规则 v2 冲突时以规则 v2 为准。

## 1. Runner 路由判据（v2，三点）

| # | 判据 | 运维含义 |
|---|------|----------|
| 1 | runner_name 前缀只认 `arc-pve-02-runner-` | 现行自托管池唯一合法前缀。旧 `arc-pve-01-runner-` 已随旧 scale set 删除，不再出现；历史日志/文档里的该前缀属遗留记录，不代表现行池 |
| 2 | 唯一路由判据 = **job 被领走** | job 的 `runner_name` 非空（或 listener 侧 assigned job 计数增长）即路由成立；job 排队中（runner_name 为空）只是等待，不构成路由故障证据 |
| 3 | runners API `labels=[]` **不可用作判据** | ARC 0.15.0 固有行为：ephemeral runner 在 runners API 上 labels 显示为空数组。以 runner 标签/在线状态判断路由健康会得出假阴性，禁止 |

速查：`gh api repos/<org>/<repo>/actions/runs/<run_id>/jobs --jq '.jobs[] | {name, runner_name}'`
—— 看 `runner_name` 前缀是否为 `arc-pve-02-runner-` 且非空，一分钟内可裁决「路由坏了还是还在排队」。

## 2. 已知残留（镜像生态）

不阻塞流程，也**不得误判为路由回退**；处置 = rerun 或镜像补工具，不 revert 已合并内容：

- `my-runner:v1.0` 缺 wget → lint-bundle 的 shellcheck fallback 步 exit 127
- 缺 gh → Auto Merge「Resolve candidate PRs」exit 127
- 缺 ensurepip → Evolution Scan venv 创建失败
- 偶发 checkout 网络超时

## 3. PR 门与绿判语义（合并管线整合 v3.2.2 拆门后）

- 标签门已全量拆除：PR 与 push 同面真跑（无标签即跑，不存在占签/交棒与
  人工贴/撕标环节）。
- PR 三门 = `quality-gate` / `substrate-gate-suite` / `droid-review`，每 PR 各自
  真跑出 check；三门真绿 = required checks 真实 SUCCESS，**skipped 不算绿**
  （required 判定中 SKIPPED 等价通过，任何 job 级过滤条件都构成假绿面，
  禁止回归——契约测试锁定）。
- 合入走 squash 队列路径（auto-merge 守卫腿），绿判以 head SHA 上的
  REST check-runs 为唯一入口。

## 4. 维护说明

- 判据三点若需变更：先改规则 v2（owner 侧），再同步本文；本文不是权威源。
- runner 前缀变更必然伴随 scale set 换名重装（ARC 0.15.0 下 labels 只在首次创建生效），
  届时同步本文 §1 表格与相关契约测试。
