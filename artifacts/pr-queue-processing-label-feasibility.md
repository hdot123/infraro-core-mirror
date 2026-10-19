# PR 队列：`processing` 标签 + 统一 concurrency — 可行性评估

> 评估对象：本仓（infraro-core）现有 GitHub Actions。仅方案说明，未改 workflow。

## 1. 可行吗？

**结论：方向可行，但不能「只给占 runner 的 job 加 `if`」就上线。** 与现有 ruleset、聚合 job（`ci-ok` / `qa-ok` / `droid-review` / `quality-gate`）和 `pull_request_target` 安全模型需要对齐，否则会出现 **假绿** 或 **永远 pending**。

**无硬冲突的方面：**

- 自建池标签 `[self-hosted, pve-linux]` 已集中在 PR 主链（CI / QA / Droid / Quality Gate 等）。
- `pull_request_target` 上 `github.event.pull_request.labels` 在 `labeled` / `unlabeled` / `synchronize` 等事件里可用；本仓 Droid 已有 fork 拒绝（`droid-review-shards.yml` 内 `head.repo.full_name` 比对），同源 PR 与现网一致。
- Reusable（`droid-review-shards.yml`、`auto-merge-pipeline.yml`）**禁止顶层 `concurrency`**（INFRA-626 / `test_droid_review_shards_workflow.py`），统一 concurrency 只能加在 **caller workflow**，与方案一致。

**必须先设计清楚的冲突点：**

| 风险 | 证据 |
|------|------|
| 仅 skip 子 job，`ci-ok` 仍会跑且 **失败** | `ci.yml` 中 `ci-ok` 为 `if: always()`，逐步要求 `needs.*.result == "success"`，**不接受 `skipped`**（约 L503–526） |
| 子 job 全 skip + 聚合仍跑 → **droid-review 可能假绿** | `droid-review` 聚合 `if: always()`；`shards` 为 `skipped` 时 guard 视为合法（L155–168），可走完成功路径 |
| 整 workflow 不跑 / 聚合也不跑 → **required check pending** | `quality-gate.yml` 头注释：required 名对不上 live check 会永远挡合并；`tests/test_workflow_permissions_contract.py::TestRequiredCheckReachability`（VAL-M2-107）明确 **ci-ok/qa-ok 不能被整 workflow skip** |
| 全仓同一 `concurrency` 组 | 会把 **同一 PR 的 CI 与 QA** 串成一队（见 §3），与「只限制跨 PR、PR 内可多 job」目标可能相悖 |
| 「同时只有一个 PR 有 `processing`」 | 仓库内 **无** 互斥贴标自动化；`setup-labels.yml` 默认标签列表也 **没有** `processing`（仅有 `do-not-merge` 等） |

---

## 2. 要改哪些文件？

### 2.1 PR 主链（建议纳入标签门 + `labeled`/`unlabeled`）

| 文件 | Workflow 名 | `runs-on` pve | 已有 concurrency | 触发（与 push/main） |
|------|-------------|---------------|------------------|----------------------|
| `.github/workflows/ci.yml` | CI | 全部 job | `ci-${{ workflow }}-${{ ref }}`，PR 上 `cancel-in-progress: true` | `pull_request` + **`push: main`** → main **不应** 标签门 |
| `.github/workflows/qa.yml` | QA | 全部 job | 同 CI 模式 | `pull_request` + `schedule` + `dispatch` → 仅 **PR** 需要门 |
| `.github/workflows/droid-review.yml` | Droid Auto Review | 聚合 + 调 reusable | 按 **PR 号** 组，`cancel-in-progress: **true**` | `pull_request_target`（现 **无** `labeled`）+ `dispatch` |
| `.github/workflows/quality-gate.yml` | Quality Gate | 单 job | 按 ref，PR 可 cancel | `pull_request` + **`push: main`** → main **排除** |
| `.github/workflows/evolution-governance.yml` | Evolution Governance | 单 job | PR ref 组，`cancel-in-progress: true` | `pull_request_target` + **paths 过滤**；无 push |

**Reusable（不单独加顶层 concurrency；标签门建议在 caller job 或 reusable 内 job）：**

| 文件 | 说明 |
|------|------|
| `.github/workflows/droid-review-shards.yml` | `workflow_call`；所有分片 job 为 pve；**无**顶层 concurrency（禁令） |
| `.github/workflows/auto-merge-pipeline.yml` | `workflow_call`；`authorization-gate` / triage / merge 等为 pve；门控在 caller |

### 2.2 仍会占 pve、但非 PR 审查主链（是否纳入标签门需单独决策）

| 文件 | 触发 | concurrency | 说明 |
|------|------|-------------|------|
| `droid-autofix.yml` | `workflow_run`(CI 失败) / dispatch | `droid-autofix` | CI 被门控后可能根本不起火；若启用 autofix 仍可能抢池 |
| `droid-review-watchdog.yml` | `workflow_run` / schedule / dispatch | 按 upstream run id | 自愈、quota-sweep 用 pve |
| `droid-review-watchdog-handlers.yml` | 仅 `workflow_call` | — | 由 watchdog 调用 |
| `droid-task.yml` | `repository_dispatch` | team + task 二级 | 长任务占池 |
| `auto-merge.yml` | `workflow_run` / `pull_request_target` / schedule / dispatch | `auto-merge-pipeline` | caller 无 runner；**pipeline 内** pve |
| `branch-cleanup.yml` | schedule / PR closed / dispatch | `branch-cleanup` | 通常低争用 |
| `evolution-scan.yml` / `evolution-heartbeat.yml` | schedule + dispatch + `workflow_call` | job 级组 | 引擎自扫，与 PR 队列并行存在 |
| `release-please.yml` / `publish-pypi.yml` / `release-announce.yml` | push / schedule / release | 各有 | main/tag 发布面 |
| `actions-budget-guard.yml` / `engine-consumer-audit.yml` / `gate2-cross-repo-inspection.yml` | schedule / dispatch | 各有 | 后台任务 |
| `setup-labels.yml` | `workflow_call` | job 级 | **应扩展默认标签**，创建 `processing` |

`infra-watch.yml` 为 `ubuntu-latest`，可忽略。

### 2.3 Ruleset 侧（本仓文档口径，非模板全文）

`docs/guides/fullstack-test-plan.md`：**required = `quality-gate`、`substrate-gate-suite`、`droid-review`**（且 `strict` up-to-date）。`substrate-gate-suite` 是 `ci.yml` 里 `gate-tests` 的 **job 显示名**（L596–599 注释）。模板 `docs/governance/templates/ruleset.json` 仅列 `ci-ok` / `qa-ok`，以线上 ruleset 为准。

---

## 3. 坑与边界

### 3.1 `pull_request_target` 与标签 `if`

- **可靠**：`labeled` / `unlabeled` 后 payload 带当前 `labels`；`synchronize` 在 PR 已贴 `processing` 时也会带上该标签。
- **要补触发类型**：`droid-review.yml` / `evolution-governance.yml` 现仅为 `opened, synchronize, reopened, ready_for_review`（Droid），需加 `labeled`, `unlabeled`（否则「先开 PR 后贴标」只靠 `labeled` 一次，后续 push 仍靠 `synchronize`）。
- **fork**：分片 setup 已 fail-closed 拒绝 fork；无 `processing` 时不应让聚合 job 给出绿色 `droid-review`（见 §1 表）。
- **`workflow_dispatch`**：无 `pull_request` 上下文，需 **显式豁免**（与现网手动跑 Droid 一致）。

### 3.2 Quality Gate 被跳过与合入

- 聚合脚本对缺失 check 的处理是 **一直等到超时**（`has not reported yet` → `READY=0`，L134–136），不是 skip 当绿。
- 若 PR 无 `processing` 导致 **quality-gate job 从不跑**：该 commit 上 **无** `quality-gate` check → ruleset **pending**，合入 blocked —— 若队列设计就是「未轮到就不该合」，这是 **符合预期**；若希望「未排队也算一种明确状态」，需要额外 **极轻量** job 产出约定结论（需与 ruleset 是否接受 `skipped`/`neutral` 实测一致，勿假设）。

### 3.3 无 `processing` 时 skip 与 ruleset

- GitHub 对 branch protection：**从未出现的 required context = pending**（仓库契约测试 VAL-M2-107 强调的是 **整 workflow 因 paths 不触发** 的 pending）。
- Job 级 `if: false` → 该 job conclusion 常为 **skipped**；**skipped 是否满足 required** 因配置而异，不能当默认可合并。
- 本仓 `ci-ok` **不**把 `skipped` upstream 当通过；若只 skip 子 job 而跑 `ci-ok` → **失败**，比 pending 更糟（auto-merge / quality-gate 链也会红）。

### 3.4 统一 `concurrency: group: infraro-core-active-pr`

- **非严格 FIFO**；`cancel-in-progress: false` 时，后到的 run **排队**，队列满时 GitHub 可能 **丢弃 pending**（平台行为），多 workflow 同时改组时更难推理。
- 与现状 **per-ref / per-PR** 组并存或替换时需二选一：例如 CI 现 PR 上 `cancel-in-progress: true`（`ci.yml` L37–39），与全局 `false` **语义相反**。
- **同一 PR**：若 CI、QA、Droid、Quality Gate 共用 **同一全局组**，则同一时刻只有一个 **workflow run** 在执行，**PR 内 CI∥QA 并行会消失**；要「跨 PR 互斥、PR 内并行」，应靠 **标签门** 为主，concurrency 组建议带 **PR 号** 或仅用于「无标签 run 不启动」（通常不必四 workflow 共一组）。

### 3.5 同一 PR 内 matrix / shard

- 标签门在 **caller 的 `shards` job** 一层为真时，reusable 内 `review-shard` matrix 仍受 `SHARD_MAX_PARALLEL`（默认 3）限制 —— **PR 内多分片并行保留**。
- 勿在 reusable 顶层加 concurrency（死锁禁令）。

### 3.6 应排除标签门的事件

| 事件 | 处理 |
|------|------|
| `push` → `main`（`ci.yml` / `quality-gate.yml`） | **排除**；合并后全绿验证不能依赖 `processing` |
| `schedule` / `workflow_dispatch`（QA nightly、watchdog、evolution 自扫等） | **排除**；否则夜间任务与 PR 队列耦合 |
| `repository_dispatch`（`droid-task`） | 排除或独立策略 |
| `release` / tag push | 排除 |

门控表达式建议形态（示意，非落地代码）：

`github.event_name == 'pull_request' && contains(...) || github.event_name == 'pull_request_target' && contains(...) || github.event_name == 'workflow_dispatch'`

（`push` / `schedule` 等走 OR 另一支为 true。）

---

## 4. 最小落地路径（推荐顺序）

### 阶段 A：标签与语义（先不做四 workflow 共组）

1. **`setup-labels.yml`**（及实际跑一次的 Setup Labels caller）增加 `processing` 标签定义；文档约定 **人工或 bot 全局只贴一个 PR**（本仓暂无 bot，需流程或后续 workflow）。
2. **PR 触发器** 为 `ci.yml`、`qa.yml`、`quality-gate.yml`、`droid-review.yml`、`evolution-governance.yml` 增加 `types: [labeled, unlabeled, ...]`（保留现有 types）。
3. **标签门 + 聚合协同**（核心，比加 `if` 更重要）：
   - 所有占 pve 的 **执行 job** 加 `processing` 条件；
   - **`ci-ok` / `qa-ok` / `quality-gate` / `droid-review` 聚合** 必须约定其一：  
     - **(推荐)** 无 `processing` 时 **整 job skip**，并接受该 SHA **required pending**（队列 PR）；或  
     - **(备选)** 无 `processing` 时跑 **仅 checkout 的占位 job**，产出明确 **neutral/skipped** 且与 ruleset 实测兼容（需先在一个试验 PR 上验证 GitHub UI）。  
   - **禁止** 当前这种「`shards` skip + `droid-review` always 成功」在无标签时发生 —— 必须给聚合加同条件或 fail-closed。
4. **`droid-autofix`**：与门控联动（无 processing 不修复），避免旁路占池。

### 阶段 B：统一 concurrency（可选增强）

5. 在阶段 A 稳定后，再考虑 PR 侧共享组；更稳妥的组名示例：`pve-pr-${{ github.event.pull_request.number }}`（仅带标签的 PR 会起跑）或组织级 mutex 标签 + **不**合并 CI/QA 到单全局串行组。
6. 保持 reusable **无**顶层 concurrency；`droid-review` 现有 per-PR `cancel-in-progress: true` 与全局 `false` 冲突时，以 **PR 内省队列、跨 PR 靠标签** 为准则调解。

### 阶段 C：运维

7. 写清 SOP：贴/撕 `processing`、与 `do-not-merge` 关系、active PR 跑完后谁给下一个 PR 贴标。
8. 契约测试：至少覆盖「PR 触发器无 paths 误伤」（延续 VAL-M2-107 精神）及 Droid 三契约不被破坏。

---

## 5. 一句话结论

**能上**，但上线前必须先解决：

1. **Blocker — required check 语义**：无 `processing` 时不能让 `droid-review` / `ci-ok` **假绿或误失败**；要与 `quality-gate` / `substrate-gate-suite` / `droid-review` ruleset 一致地选择 **pending（队列）** 或 **实测可用的占位 check**。  
2. **Blocker — 单 active PR**：仅有标签定义不够，要有 **保证全仓最多一个 `processing`** 的操作或自动化；否则多 PR 贴标仍抢池，方案失效。

统一 `infraro-core-active-pr` concurrency 是 **次要优化**，且默认实现会牺牲 **同 PR 上 CI 与 QA 并行**，需单独拍板后再做。

---

## 附录：关键代码锚点

```37:39:.github/workflows/ci.yml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

```503:511:.github/workflows/ci.yml
    if: always()
    steps:
      - name: Check all required jobs
        run: |
          ...
          [[ "${{ needs.pytest.result }}" == "success" ]] || { echo "FAIL: pytest"; FAILED=1; }
```

```46:48:.github/workflows/droid-review.yml
  pull_request_target:
    types: [opened, ready_for_review, reopened, synchronize]
```

```65:67:.github/workflows/droid-review.yml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || inputs.pr_number || github.ref }}
  cancel-in-progress: true
```

```1:6:.github/workflows/quality-gate.yml
# ... required context that does not match any live check name blocks merges forever.
```

```98:103:tests/test_workflow_permissions_contract.py
    ci-ok and qa-ok are required checks. If their workflow triggers include
    paths or paths-ignore filters, the workflow may be skipped for certain PRs,
    leaving the required check permanently pending and blocking all merges.
```

```62:64:.github/workflows/setup-labels.yml
              { name: "do-not-merge", color: "b60205", description: "仅开 PR，不合并：auto-merge 跳过（移除后恢复候选）" },
```

```34:38:.github/workflows/droid-review-shards.yml
# 禁止顶层 concurrency（INFRA-626）：reusable 携带顶层 concurrency 会与 caller
# 顶层组名组合触发 GitHub run 级自死锁检测
```
