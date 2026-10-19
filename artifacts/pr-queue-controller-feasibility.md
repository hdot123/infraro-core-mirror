# PR 队列控制器方案 — 可行性复核

> 相对上一份 [`pr-queue-processing-label-feasibility.md`](./pr-queue-processing-label-feasibility.md)（人工贴 `processing`）的更新评估。仍不改代码、不开 PR。

## 1. 比人工标签更贴合本仓吗？硬 blockers？

**更贴合。** 上一份的两个 blocker——「全仓最多一个 `processing`」和「聚合防假绿」——控制器负责前者；后者仍要在各 pve workflow 里做标签门（控制器不能替代）。

| 维度 | 人工版 | 控制器版 |
|------|--------|----------|
| 互斥贴标 | 靠人/外部 SOP | `pr-queue-controller.yml` + API（`ubuntu-latest`，不占 pve） |
| CI∥QA∥Droid | 易误用全局 concurrency | 方案明确 **不** 合仓 `infraro-core-active-pr`，保留 `ci.yml` / `qa.yml` / `droid-review.yml` 现有 per-ref 或 per-PR 组 |
| 安全模型 | 同 | `pull_request_target` + **零 checkout PR 头** 与本仓 Droid 壳一致（`droid-review.yml` 注释：定义面取 BASE） |

**仍存在的硬 blockers（上线前必须设计进控制器 + 门控）：**

1. **pve 入口 + 聚合必须验 `processing`**（上一份结论不变）  
   - 否则 `droid-review` 在 `shards` 为 `skipped` 时仍可能走 `always()` 聚合成功（`droid-review.yml` L108–168）。  
   - `ci-ok` 对 `needs.*` 只认 `success`，不认 `skipped`（`ci.yml` L503–526）。

2. **出队条件必须按 check-run 聚合，不能只看单个 `workflow_run.conclusion`**  
   - 本仓 PR 上 CI、QA、Droid、Quality Gate **并行**；`auto-merge.yml` 已因 INFRA-212 不能用 `check_run`，改用 `workflow_run`（L12–18）。  
   - 若控制器在 **仅 CI** `completed/success` 时就摘标 → QA / Droid / `quality-gate` 仍在跑 → 下一 PR 抢池。  
   - 应对齐 ruleset 口径：`docs/guides/fullstack-test-plan.md` 写明 required ≈ **`quality-gate`、`substrate-gate-suite`、`droid-review`**（`substrate-gate-suite` 即 `ci.yml` 里 `gate-tests` 的 job 名，L596–599）。

3. **失败 / 取消时队列策略**  
   - 方案写「失败保持 processing」→ 队首 PR 红或 `cancelled` 时 **全仓阻塞**，需接受或加 `do-not-merge` / 手动撕标逃生（本仓已有 `do-not-merge` 与 auto-merge 逃生，`auto-merge-pipeline.yml` L334–359、`setup-labels.yml` L63）。

4. **特殊 PR 形态**  
   - **Dependabot**：分片 `plan-shards` 直接不跑（`droid-review-shards.yml` L307），`ci-ok` 内对 droid 有豁免逻辑（`scripts/check_droid_review.sh` L65+）。控制器出队列表不能把「无 droid-review」一律当未完成。  
   - **Draft**：分片 skip draft（同上 L307）；队首是否含 draft 要定规则。  
   - **Fork**：Droid fail-closed（`droid-review-shards.yml` L238–244）；可占 `processing` 但永远合不了，需排队策略（跳过或单独队列）。  
   - **Evolution Governance**：`pull_request_target` + **paths**（`evolution-governance.yml` L4–11）；未改治理路径时 workflow 可能根本不跑——**不应**把其当作全局出队必要条件（除非 ruleset 强制该 context）。

5. **标签与权限**  
   - `processing` 仍未在 `setup-labels.yml` 默认列表里，需先建标签。  
   - 控制器 `pull-requests:write` 贴标可行；**不要**把本 workflow 放进 `auto-merge.yml` 的 `workflow_run` 监听列表（L49–54），避免与合并器逻辑纠缠；也 **不要** 监听自身 completed 造成空转（除非 deliberately 做自愈）。

6. **契约 / 测试**  
   - 新 workflow 名勿与现有 `workflow_run` 名单冲突。  
   - 延续 `tests/test_workflow_permissions_contract.py` VAL-M2-107 精神：门控不能导致 required **假绿**。

**非 blocker、但建议：** 控制器加 `schedule` 兜底（对齐 `auto-merge.yml` L58–59），防 `workflow_run` 丢事件；`concurrency.queue` 若仓库策略支持可加，非必须。

---

## 2. `workflow_run` 推进 vs 本仓时序坑

| 坑 | 说明 | 本仓证据 |
|----|------|----------|
| **并行四 workflow，四次 `workflow_run`** | 一次 push 可依次触发 CI、QA、Droid、QG 的 completed；控制器 concurrency `cancel-in-progress: false` 会 **排队多次**，需 **幂等**（状态：当前 processing PR、head SHA、是否已满足出队）。 | `ci.yml` / `qa.yml` 独立触发；`auto-merge.yml` L49–54 四工作流监听 |
| **不能「谁先 success 谁出队」** | 必须对 **当前 head SHA** 查 check-runs（同 `quality-gate.yml` L94–120 思路），等 `quality-gate` + `substrate-gate-suite` + `droid-review` 均 `completed/success`（另视 ruleset 是否要 `qa-ok`；QG 脚本已聚合 `qa-ok`，L78–81）。 | `quality-gate.yml` L21–28 慢尾说明；`check_zero_red.sh` L40–48 与 QG 死锁史 |
| **CI `cancel-in-progress: true`** | processing PR 上新 push → 旧 CI **cancelled** → 会来一次 `workflow_run` completed=cancelled；控制器 **不能** 据此出队或清标。 | `ci.yml` L37–39 |
| **Droid `cancel-in-progress: true`** | 同 PR 新 push 取消旧 review run；可能出现 **cancelled** 的 Droid workflow_run + 新 run 在跑；出队只看 **最新 head** 的 check 结论。 | `droid-review.yml` L65–67 |
| **Droid 尾延迟** | 分片 + 聚合可达 80min+；processing 会贴很久，**符合预期**；`workflow_run`(Droid) success **早于** check `droid-review` 落盘 的窗口要用 API 以 check 为准。 | `quality-gate.yml` L21–28；`droid-review-shards.yml` SHARD 预算注释 |
| **Quality Gate 晚于 CI** | QG 轮询 `ci-ok` 等，常是链上 **最后一个** green；`auto-merge` 已把 **Quality Gate** 列入 workflow_run（`auto-merge.yml` L50–52）。控制器出队也应 **以 QG/workflow 或 check 终态为准**，不是 CI alone。 | 同上 |
| **贴标 → 风暴** | 控制器 `labeled` → 触发 `pull_request`/`pull_request_target`（含 Droid、evolution）；控制器自身若也监听 `labeled`，要 **防抖**（例如：仅当标签集合与期望不一致时才写 API）。 | Droid 触发类型现缺 `labeled`（`droid-review.yml` L47–48），落地门控时必须补 |
| **watchdog 旁路** | `droid-review-watchdog.yml` 仍占 pve，**不受 processing 门**；卡死自愈可能与队列并行，一般可接受。 | `droid-review-watchdog.yml` `runs-on` pve |
| **Autofix** | `droid-autofix.yml` 跟 CI failure 的 `workflow_run`；无 processing 时 CI 可能不跑或 skip，autofix 不应旁路抢池 → 纳入 processing 门（上一份已列）。 | `droid-autofix.yml` L27–30 |

---

## 3. 「等标签」时 pending 对合并 / 展示是否 OK？

**对「未轮到」的 PR：OK，且与目标一致。**

- Ruleset / strict up-to-date 下，无 `processing` → pve job 不跑或整 job skip → 该 SHA 上 **`quality-gate` / `droid-review` / `substrate-gate-suite` 不出现或保持 pending** → **不能合并**（上一份 §3.2–3.3；VAL-M2-107）。
- **已有 `processing` 的那一条 PR** 不受影响：checks 正常跑，合并态仅取决于自己是否全绿。
- **展示**：GitHub PR 列表会显示其它 PR「部分 checks 未跑」——是队列语义，不是 bug。
- **auto-merge**：triage 对 rollup 未完成会 `pending/wait`（`auto-merge-pipeline.yml` L339–354），不会误合并排队 PR。

注意：若错误实现成「无标也跑聚合假绿」，会触发 `auto-merge` 的 `any(SUCCESS)` 与全 check 扫描（L406–409）——仍可能挡，但污染检查历史；故必须坚持 **门控 + 不出假绿**。

---

## 4. 最小落地步骤（清单，无代码）

### 4.1 新建控制器

| 项 | 内容 |
|----|------|
| 文件 | `.github/workflows/pr-queue-controller.yml`（新） |
| `on` | `pull_request_target`：`opened,reopened,synchronize,closed,labeled,unlabeled`（按需加 `ready_for_review`） |
| | `workflow_run`：`workflows: ["CI","QA","Droid Auto Review","Quality Gate"]`，`types: [completed]` |
| `runs-on` | `ubuntu-latest`（**禁止** pve） |
| `concurrency` | `group: infraro-pr-queue-controller`，`cancel-in-progress: false`（可选 `queue`） |
| `permissions` | `contents: read`，`pull-requests: write`，`checks: read`，`actions: read` |
| 实现约束 | 仅 `main` 上脚本 / `gh api` / `actions/github-script`；**禁止** `checkout` PR head；逻辑：扫 open 非 draft（待定）→ 至多一个 `processing` → 队首赋标 → 重复标清理 |
| 出队 | 仅当队首 PR **当前 head** 上 required checks 全 `success`（列表与 ruleset 一致，处理 dependabot / 无 governance 路径）；失败或 `cancelled`/`failure` **保持 processing**（或显式文档化人工撕标） |
| 可选 | `schedule` 每 15–30min 与 `auto-merge` 错峰，防丢事件 |

### 4.2 改现有 workflow（processing 门 + 触发类型）

与上一份相同集合，**所有占 pve 的 job（含 `ci-ok`、`qa-ok`、`quality-gate`、`droid-review` 聚合、`gate-tests`/substrate、evolution job、autofix）** 增加 PR 路径条件；`push`/`schedule`/`workflow_dispatch`/`workflow_run` 触发的非 PR 腿 **放行**。

| 文件 | 必改点 |
|------|--------|
| `ci.yml` | PR job + `ci-ok`/`gate-tests` 门控；`pull_request.types` 加 `labeled,unlabeled` |
| `qa.yml` | 同上（仅 `pull_request` 事件） |
| `quality-gate.yml` | PR job 门控；types 补 `labeled,unlabeled` |
| `droid-review.yml` | `shards` + `droid-review`（及 `auto-resolve-threads` 若占 pve）门控；`pull_request_target.types` 补 `labeled,unlabeled` |
| `droid-review-shards.yml` | 或在 caller `shards` job 一门控（推荐 caller，避免 consumer 双份逻辑） |
| `evolution-governance.yml` | paths 触发不变 + 门控 + `labeled,unlabeled` |
| `droid-autofix.yml` | `autofix` job 门控（仅 PR 修复路径） |
| `setup-labels.yml` | 默认增加 `processing` 标签元数据 |

**明确不改：** 各 workflow **顶层 concurrency 组**（不引入 `infraro-core-active-pr`）；reusable **不加** 顶层 concurrency（INFRA-626，`droid-review-shards.yml` L34–38）。

### 4.3 标签约定

| 标签 | 含义 |
|------|------|
| `processing` | 唯一队首，允许占 pve 跑 PR 检查 |
| `do-not-merge` | 已有；控制器 **不得** 给带此标的 PR 赋 `processing`（与 auto-merge 最高优先级一致） |

可选：文档写清与 draft、dependabot、fork 的优先级。

### 4.4 验证方式（建议顺序）

1. **双开 PR**：仅队首有 `processing`；另一 PR required 长期 pending，Merge 按钮灰。  
2. **队首 push**：`ci` cancel 后重新跑，processing 不丢；控制器不因 `cancelled` workflow_run 误出队。  
3. **全绿链**：看 head 上 `substrate-gate-suite`、`droid-review`、`quality-gate` 均 success 后 processing 转移；下一 PR 自动获标并触发 CI/QA/Droid。  
4. **失败保持**：队首故意红，processing 不转移，第二 PR 仍 pending。  
5. **Dependabot PR**：出队/入队不死锁（droid 跳过但 CI/QG 可绿）。  
6. **安全**：控制器 run 日志无 PR head checkout；fork PR 行为符合预期。  
7. **契约**：跑 `tests/test_workflow_permissions_contract.py`、`tests/test_droid_review_shell_contract.py` 等，必要时为控制器加「无 checkout」「workflow 名不在 auto-merge 监听误增」断言。

---

## 5. 一句话结论

**比人工贴标更适合本仓**：用 `ubuntu-latest` 控制器互斥 `processing`，pve 主链保持并行 concurrency；但 **仍必须先落地「processing 门 + 聚合不假绿」**，且控制器 **必须用 check-run/规则一致的出队条件**，不能单靠某一个 `workflow_run` success，否则会在 CI/QG/Droid 并行和 cancel 语义下提前放行下一 PR。

---

## 附录：与上一份报告的引用关系

- 门控 / 假绿 / pending 语义 → 上一份 §1、§3。  
- 文件清单 → 上一份 §2；本方案 **新增** `pr-queue-controller.yml`，**删除** 对「人工 SOP」的依赖。  
- **明确采纳** 上一份「不要四 workflow 共用一个全局 pve concurrency」的建议。
