# PR 关闭后取消关联 Actions run（止损边）

**状态**：已落地（实现 PR #457；契约测 tests/test_cancel_on_pr_closed.py 锁定）  
**动机实证**：2026-10-25 探针 `#452` 关 PR 后，同 `head_sha` 上 CI 等仍继续占 self-hosted runner；关 PR **不会**自动停掉已排队/已在跑的 PR 系 workflow。  
**合规**：GitHub 官方支持——`pull_request`/`pull_request_target` 的 `closed` 事件 + [Cancel a workflow run](https://docs.github.com/en/rest/actions/workflow-runs#cancel-a-workflow-run) API；与仓内已有 `cancel-on-ci-fail` 同族。

## 目标

关联 PR 变为 **closed**（含 merged 与未合并关闭）后，尽快 cancel 该 PR 最新 `head_sha` 上仍为 `queued` / `in_progress` 的 **PR 事件** run，释放 runner / 配额。

**不是**：「仓库里没有 open PR 就杀光所有 runner」。  
**是**：「这个 PR 已经没有了 → 停掉还在为它跑的流水线」。

## 非目标（禁止）

- 取消 `push` → `main`（及任何非 PR 触发）的 run  
- 取消 `schedule` / `workflow_dispatch` / `workflow_run` 且无关联 PR 的运维腿  
- Runner 进程内私自杀进程、改 runner 服务配置  
- 改 ruleset、改 required check 名、削弱零红语义  
- checkout PR head 代码（fork 面）

## 推荐落点

优先挂在现有 **Droid Review Watchdog** 旁，新增 job：`cancel-on-pr-closed`（命名对齐 `cancel-on-ci-fail`）。

| 选项 | 评价 |
|------|------|
| **A. Watchdog 新 job + `pull_request_target: types: [closed]`** | **推荐**。已有 cancel API、`actions: write` 下放、kill-switch / dry-run 护栏可复用 |
| B. 扩展 `branch-cleanup.yml`（已听 `closed`） | 可，但 cleanup 职责是删分支；混入 cancel 易糊边界 |
| C. 扩展 `pr-queue-controller.yml` | 可，但 Queue 只做标/合并态；cancel 写权限面会变大 |
| D. 全新独立 workflow | 可，但多一个 concurrency/命名面，无必要 |

实现时：**只读默认分支 YAML**（`pull_request_target` / 或独立 workflow 固定 `ref: default_branch`），**禁止** checkout `github.event.pull_request.head`。

## 触发与输入

```yaml
on:
  pull_request_target:
    types: [closed]
```

从事件取：

| 输入 | 来源 |
|------|------|
| `REPO` | `github.repository` |
| `PR_NUMBER` | `github.event.pull_request.number` |
| `HEAD_SHA` | `github.event.pull_request.head.sha` |
| merged? | `github.event.pull_request.merged`（仅日志；**merged 与 closed 一律 cancel PR 系 run**） |

Runner：`ubuntu-latest`（薄腿，勿占 pve）。  
`timeout-minutes`：≤ 5。  
权限：顶层保持只读基线；本 job `permissions: { actions: write }`（与 Watchdog G4 一致）。  
Token：`secrets.GITHUB_TOKEN` 即可（同仓 cancel）。

## Cancel 范围（硬过滤）

1. 列 run：  
   `GET /repos/{repo}/actions/runs?head_sha={HEAD_SHA}&status=in_progress`  
   以及 `status=queued`（各拉一页或 paginate，上界建议 ≤ 2 页 × 100）。  
2. 对每个 candidate，**同时满足**才 cancel：  
   - `status` ∈ {`queued`, `in_progress`}  
   - `event` ∈ {`pull_request`, `pull_request_target`}  
     （**排除** `push` / `schedule` / `workflow_dispatch` / 纯 `workflow_run` 运维）  
   - 可选白名单 workflow `name`（默认建议）：  
     `CI` / `QA` / `Quality Gate` / `Droid Auto Review` / `Auto Merge`  
     （名称须与 `.github/workflows/*.yml` 的 `name:` **字节一致**；增删要改契约测）  
3. 对本 job 自身 run：自排除（`GITHUB_RUN_ID`），勿 cancel 自己。  
4. cancel 失败（刚结束 / 403 瞬时）：`::warning::` 后继续，不红整 job（止损尽力而为）。

参考实现骨架（对齐 Watchdog `cancel-on-ci-fail`）：

```bash
set -euo pipefail
# 伪代码：按 head_sha 列 in_progress + queued，过滤 event + name，再 POST cancel
for status in in_progress queued; do
  gh api "repos/$REPO/actions/runs?head_sha=$HEAD_SHA&status=$status&per_page=100" \
    --paginate --jq '
      .workflow_runs[]
      | select(.id != ($ENV.GITHUB_RUN_ID | tonumber))
      | select(.event == "pull_request" or .event == "pull_request_target")
      | select(.name == "CI" or .name == "QA" or .name == "Quality Gate"
               or .name == "Droid Auto Review" or .name == "Auto Merge")
      | .id
    '
done | while read -r RUN_ID; do
  [ -n "$RUN_ID" ] || continue
  gh api -X POST "repos/$REPO/actions/runs/$RUN_ID/cancel" || echo "::warning::cancel $RUN_ID failed"
done
```

（落地时用显式 jq/`GITHUB_RUN_ID` 注入，勿照抄未测的 `$ENV` 片段。）

## 护栏（建议复用 Watchdog）

| 护栏 | 行为 |
|------|------|
| Kill-switch | 复用 `vars.WATCHDOG_KILL_SWITCH=enabled` → 整 job skip；或本功能独立 `CANCEL_ON_PR_CLOSED_KILL_SWITCH` |
| Dry-run | 复用 `WATCHDOG_DRY_RUN` / 独立 var：`observe` 只列 run 打日志，不 POST cancel |
| 分页上界 | 与 G2 同款，防 API 扫爆 |
| 不碰 main push | 靠 `event` 过滤硬保证；契约测锁定 |

## 与现有机制关系

| 机制 | 关系 |
|------|------|
| `concurrency` + `cancel-in-progress`（CI/QA/QG 在 PR 上） | 只对**同 ref 新 PR 事件**砍旧；**closed 不发 synchronize**，补不了本洞 |
| Watchdog `cancel-on-ci-fail` | CI 红 → 只砍 Droid；本边是 **PR 消失 → 砍该 SHA 上 PR 系全家** |
| Watchdog G3（closed 不 self-heal） | 只阻止 rerun；**不** cancel 已在跑的 CI |
| Branch Cleanup | 关 PR 删分支；**不** cancel Actions |
| `check_zero_red` / 门禁 | `cancelled` 已作灰/可忽略；本边不改变合入语义 |

## 验收清单

- [ ] 开一无害 PR，触发 CI（或 QA）进入 `queued`/`in_progress`  
- [ ] `gh pr close <n>`（或合并后 close）  
- [ ] 本 job 在 closed 后启动（ubuntu），日志列出并 cancel 目标 run  
- [ ] N 秒内（建议 ≤ 60s 观测窗）该 `head_sha` 上白名单 workflow 无新的长期 `in_progress`（允许 cancel 传播延迟）  
- [ ] 同时存在的 `push`→`main` run **未被** cancel（负向）  
- [ ] fork PR closed：不 checkout head；仅 API cancel（若策略对 fork 也启用）  
- [x] 契约测：workflow 含 `cancel-on-pr-closed`（或选定落点名）、`actions: write` 仅 job 级、触发含 `closed`、过滤含 `pull_request` event 排除 `push`  
- [x] 文档：本页状态改为「已落地」并链到实现 PR（#457）

## 回滚

1. Kill-switch 打开 → 立即停 cancel  
2. 或 revert 实现 PR；closed 行为回到「只 Cleanup / Queue，不砍 run」

## 实证背景（#452）

时间线（UTC）：

1. `13:52:19` 向探针分支推 RESULT → `synchronize` 再起 CI 等  
2. `13:52:46` `gh pr close 452`  
3. CI run 仍继续占 pve，直至人工 `gh run cancel`

根因：无「PR closed → cancel 关联 PR run」状态机边。本规格补该边。

## 交接备注

- 本集 pipeline cutover（`#451`）**不**要求本功能同车；单独小 PR。  
- 实现者：先读 `.github/workflows/droid-review-watchdog.yml` 的 `cancel-on-ci-fail` job，复制权限与 dry-run 模式，再换触发与过滤。  
- 有疑问先对规格开 PR 讨论，勿在 runner 侧另做丢弃逻辑。
