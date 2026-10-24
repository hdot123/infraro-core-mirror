# PR 队列对抗审清单（#359）

基准：`scripts/pr_queue_controller.sh`、`docs/guides/pr-queue-processing.md`、pve 门控 workflow。  
审阅时点：合入后等 main push 交棒语义已落地。

## 遗漏 Top 5（优先关注）

| # | 严重度 | 缺口 | 状态 | 建议 |
|---|--------|------|------|------|
| 1 | **P1** | `workflow_dispatch` 手动跑 Droid 审查不占 `processing` 仍占 pve | **未修** | 限制 maintainers 或 dispatch 前验 processing（break-glass 另议） |
| 2 | **P1** | `workflow_dispatch` autofix 手动路径不占队列 | **未修** | 与 B2 同策略；自动 workflow_run 路径已门控 |
| 3 | **P1** | auto-merge 与队列控制器双轨合入（全绿双尝试） | **未修** | triage 见 `processing` 则 skip，由队列独占 squash（跟进 PR） |
| 4 | **P1** | watchdog / `droid-task` / schedule 等非队列 pve 与队首叠机 | **未修（设计外）** | 接受运维叠机或单独容量规划；不纳入 processing 语义 |
| 5 | **P1** | 人工撕 `processing` 在 main push 未绿时提前交棒 | **文档 break-glass** | reconcile 自动赋/摘；手撕仅应急 |
| — | **P2** | main 交棒未显式含 `ci-ok` 名 | **可接受** | QG push 路径已聚合；若要字面对齐可加第三项 check |
| — | **P2** | 单 PR 多 shard 并行占多台 pve | **设计如此** | 队列互斥在 PR 间，非 shard 间 |

**P0 未闭合：0**（#358 回贴、main push 交棒、合入失败冻队、pve 门控假绿、check-runs 翻页已在本分支）。

## 交付设计核对（权威语义 1–6）

| 语义 | 结论 |
|------|------|
| 1 唯一通道 + PR 检查占位 | **已覆盖**（门控 + 控制器） |
| 2 三门绿 squash / 合入失败冻队 | **已覆盖** |
| 3 main 合入后检查全绿才交棒 | **已覆盖**（merge commit + QG/substrate；QG 代理 ci-ok） |
| 4 失败不放通道 | **已覆盖**（含 PR 红、合入失败、main push 红） |
| 5 queue-jump | **已覆盖** |
| 6 主流程 1–4 | **已覆盖** |

**偏差**：无 P0 级实现偏差；main 交棒判据为 **具名两 check**，非「merge commit 上任意 check-run 全 success」字面（通常更严/等价，见 processing 文档）。

**交付层仍缺（非状态机）**：下表 Top 5 + dispatch/叠机/双轨合入/手撕标。

## 结论摘要

| 级别 | 数量 | 说明 |
|------|------|------|
| P0 | 0 未闭合 | 原设计 P0（门控/出队绑 head/check-runs）本 PR 已覆盖；无新增阻断项 |
| P1 | 6 | 旁路占机、双轨合入、运维误操作等；见下表 |
| P2 | 5 | 产品/官方能力差距、监控与文档类 |

## 清单（按对抗维度）

### 1. 入口（open / ready / labeled / queue-jump / draft / do-not-merge / fork）

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| E1 | `ready_for_review` 触发 reconcile | — | **已修** | `pr-queue-controller.yml` 含该 type |
| E2 | `queue-jump` labeled 触发 | — | **已修** | `labeled`/`unlabeled` + assign 优先；**标签需 Setup Labels / 手动建**（合入前不在 main） |
| E3 | draft / do-not-merge / fork 不入队 | — | **已修** | `pr_eligible` + sanitize 撕标 |
| E4 | fork 人工贴 `processing` | P1 | **已知** | 下次 sanitize 撕标；窗口内可能占槽冻队 |
| E5 | 非维护者开 PR 触发 `pull_request_target` | P1 | **已知** | 仅能促使改标，非 RCE；依赖仓权限 |

### 2. 占用（唯一 processing / 多标 / 回贴 / 插队）

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| O1 | 至多一个 processing（含 MERGED 等 push） | — | **已修** | `fetch_processing_labeled_prs --state all` + dedupe |
| O2 | 全绿回贴 #358 | — | **已修** | `candidate_for_processing_label` |
| O3 | queue-jump 不抢占 | — | **已修** | 仅槽位空时 assign；无撕队首逻辑 |
| O4 | 控制器并发双标 | P1 | **已知** | `concurrency` 串行缓解，非原子；短暂可能双标 |
| O5 | 人工双 PR 贴 processing | P1 | **部分** | dedupe 保留最早；依赖下次 reconcile |

### 3. 出队（PR 绿 ≠ 结束 / squash / main push 交棒）

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| D1 | 出队绑 head check-runs 非单 workflow_run | — | **已修** | `required_checks_rc` |
| D2 | squash 合入后等 merge commit push CI | — | **已修** | `main_push_checks_rc` + 持标至 release |
| D3 | 与下一 PR 抢 pve（绿即交棒） | — | **已修** | 交棒前持 `processing` |
| D4 | `workflow_run: cancelled` 误出队 | — | **已修** | 控制器不因 cancel 单独出队；仍看 check API |
| D5 | main push 仅盯 QG+substrate 是否够 | P2 | **已知** | QG push 路径内已聚合 `ci-ok`；未单独盯 `ci-ok` 名 |

### 4. 合入失败（conversation / 冲突 / ruleset）

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| M1 | 合入失败保持 processing 冻队 | — | **已修** | 不再「失败仍出队」 |
| M2 | 评论锚点 | — | **已修** | `pr-queue-merge-failed-<sha>` |
| M3 | `GITHUB_TOKEN` 与 ruleset/PAT 差异 | P1 | **已知** | 合入失败则冻队+评论；与 auto-merge PAT 双轨 |

### 5. GitHub Merge queue / merge_group

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| G1 | 官方 Merge Queue | P2 | **不迁移** | 解决的是「合并串行+临时分支」，不替代本仓 **pve 互斥** 与 `processing` 门控 |
| G2 | merge_group 事件 | P2 | **未用** | 当前自研标签队列；迁 Merge Queue 需 ruleset/消费面大改 |

### 6. 旁路占 pve（push main / schedule / watchdog / droid-task …）

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| B1 | `push`→`main` CI/QG | — | **设计内** | 队首合入后**故意**跑 main push；槽位持有至绿 |
| B2 | `workflow_dispatch` Droid 审查 | P1 | **未修** | `droid-review.yml` dispatch 不验 `processing`（原 A6） |
| B3 | `workflow_dispatch` autofix | P1 | **未修** | 手动可占 pve；自动路径验 processing |
| B4 | `droid-review-watchdog` schedule/dispatch | P1 | **未修** | 卡死自愈，与队列正交 |
| B5 | `droid-task` repository_dispatch | P1 | **未修** | 自动化旁路 |
| B6 | evolution-scan / release-please / auto-merge-pipeline pve | P2 | **已知** | 非 PR 队列面；与队首 push 可能叠机 |

### 7. 权限 / pull_request_target / token / 假绿

| ID | 项 | 级别 | 状态 | 说明 |
|----|-----|------|------|------|
| S1 | 控制器零 checkout PR head | — | **已修** | 仅 default branch 脚本 |
| S2 | 控制器 `ubuntu-latest` | — | **已修** | 不占 pve |
| S3 | 无 processing 的 PR 跑 pve 聚合假绿 | — | **已修** | CI/QA/QG/Droid 门控 `processing` |
| S4 | check-runs 翻页 | — | **已修** | `latest_check_conclusion` 使用 `--paginate` |
| S5 | auto-merge 与队列双轨合入 | P1 | **未修** | 全绿时两路都可能尝试 merge；建议后续 triage 让位队列 |

## 建议后续（不在 #359 强行做）

1. **P1**：`workflow_dispatch` Droid/autofix 限制为 maintainers 或同样要求 processing（除紧急 break-glass）。
2. **P1**：auto-merge pipeline 跳过仍持 `processing` 的 PR，由队列独占 squash。
3. **P2**：告警 `count(label:processing)>1`；文档强调勿手撕 processing（除非 break-glass）。

## 参考

- 初版对抗：`artifacts/pr-queue-controller-adversarial.md`
- 运行语义：`docs/guides/pr-queue-processing.md`
