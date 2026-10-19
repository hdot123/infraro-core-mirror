# PR 队列控制器 — 对抗测试报告

> 被测方案见 [`pr-queue-controller-feasibility.md`](./pr-queue-controller-feasibility.md)。只评设计，未改代码。

## 总判

| 项 | 结论 |
|----|------|
| 是否仍建议上 | **建议上**，但属于「控制面 + 数据面双改」；只加控制器、门控做不全 = 比现状更乱 |
| 上线前必须修的对抗项 | 见文末 **P0 清单**（共 7 条） |

---

## P0 / P1 / P2 缺陷表

| 级别 | ID | 缺陷摘要 | 攻击/后果 | 本仓指证 |
|------|-----|----------|-----------|----------|
| **P0** | A1 | 出队只看某一个 `workflow_run.success` | 队首 CI 绿、Droid 仍跑 → 下一 PR 获 `processing` → **双 PR 占满 pve**，队列失效 | `auto-merge.yml` L49–54 四 workflow 并行；`ci.yml` L37–39 与 `droid-review.yml` L65–67 独立 concurrency |
| **P0** | A2 | 聚合未门控：`shards` skip + `droid-review` `always()` | 无 `processing` 的 PR 仍可出现 **绿色 `droid-review`** → ruleset 假绿合入 | `droid-review.yml` L108–110、L155–168（`shards` 为 `skipped` 仍继续） |
| **P0** | A3 | `ci-ok` 门控只做子 job、聚合仍 `always()` | 子 job 全 skip → `ci-ok` **失败** 或若误改逻辑为 skip → pending/假绿二选一 | `ci.yml` L503–526 只认 `needs.* == success` |
| **P0** | A4 | `cancelled` 的 `workflow_run` 当失败/成功误出队 | processing PR 上连环 push → CI/Droid 被取消 → 控制器 **摘标** → 下一 PR 抢池，队首检查未跑完 | `ci.yml` L37–39 `cancel-in-progress: true`；`droid-review.yml` L67 |
| **P0** | A5 | 出队不绑 **当前 PR head SHA** | force-push / amend 后仍按旧 SHA 绿 → **摘标**；新 commit **无 required** 或旧红新绿混乱 | ruleset strict（`fullstack-test-plan.md` L62–63）；`quality-gate.yml` L70 `head.sha` |
| **P0** | A6 | `workflow_dispatch` / 漏门控的 pve workflow | 任意人手动跑 Droid/Autofix **绕过队列**占 pve | `droid-review.yml` L49–58 dispatch；`droid-autofix.yml` L60 pve |
| **P0** | A7 | 控制器误配 `runs-on: pve` | 控制器与审查 **抢同一池**，且 `pull_request_target` 高频触发 → **饿死业务 job** | 方案要求 `ubuntu-latest`；本仓 pve 仅 23 workflow 文件内声明 |
| **P1** | B1 | 控制器并发非原子：双 run 各贴一个 PR | 短暂 **两个 `processing`** → 双 PR 同时起跑 CI/QA/Droid | 方案 `concurrency` 串行缓解但 **不消除**「读 open PR → 写 label」竞态；100 PR 风暴时排队延迟放大 |
| **P1** | B2 | 「失败保持 processing」+ 无逃生 | 队首一次红 → **全仓队列冻住**（DoS） | 方案既定；需 `do-not-merge`/人工撕标/「skip queue」运维 |
| **P1** | B3 | Dependabot / draft / docs-only 与出队列表不一致 | 队首永远达不到控制器要的 check 集合 → **永不摘标** | `droid-review-shards.yml` L307 dependabot+draft；`check_droid_review.sh` dependabot 豁免 |
| **P1** | B4 | Fork PR 占 `processing` | Droid fail-closed，required 永红 → **队列阻塞** | `droid-review-shards.yml` L238–244 |
| **P1** | B5 | 人工乱贴 `processing` | 与控制器争夺队首；控制器下次 run 才纠正 → **窗口内双 PR 跑** | 任何有 `pull-requests:write` 的人 |
| **P1** | B6 | `labeled` 风暴 + 控制器监听 `labeled` | 贴标触发 CI/Droid/控制器 **嵌套排队**；API rate limit、检查排队变长 | 门控后每次赋标触发 `pull_request`/`pull_request_target` |
| **P1** | B7 | Watchdog `cancel-on-ci-fail` 与队列语义 | processing PR CI 红 → Droid 被取消（省钱）→ 队首仍 processing、全红 → **冻队** | `droid-review-watchdog.yml` L226–238 |
| **P1** | B8 | Quality Gate 与 ci-ok 死锁类历史 | 出队若要求 QG 绿，但 QG 超时红而 ci-ok 仍 polling → 队首 **永不出队** | `quality-gate.yml` L21–28、`check_zero_red.sh` L40–48 |
| **P1** | B9 | `workflow_run.pull_requests[0]` 关联错误 | 极少数 run 无 PR 关联 → 控制器 **误操作别的队首** | `droid-autofix.yml` L77 同类用法 |
| **P2** | C1 | schedule 类 pve 与 PR 队列无关 | evolution/release 等仍占机 → **池子并非只服务 processing PR** | `evolution-scan.yml` schedule；`release-please.yml` pve |
| **P2** | C2 | `droid-task` `repository_dispatch` | 自动化任务旁路队列 | `droid-task.yml` L14–15、L38 |
| **P2** | C3 | 无 `schedule` 的控制器 | `workflow_run` 丢事件 → **长时间无队首标** | 对比 `auto-merge.yml` L58–59 兜底 |
| **P2** | C4 | Evolution Governance paths 未触发 | 若出队误要该 context → 假 deadlock | `evolution-governance.yml` L4–11 |
| **P2** | C5 | 100 PR opened 仅控制器排队 | 队尾 PR **长时间无标**（预期）但易被误报为「CI 坏了」 | 产品/运维问题非安全 |

---

## 攻击场景（按对抗要求）

### 1. 并发风暴（100 PR opened/push）

**场景**：机器人或批量分支同时开 PR / push。  
**路径**：每个事件 → `pull_request_target` 触发控制器；`concurrency: infraro-pr-queue-controller` + `cancel-in-progress: false` → **百条控制器 run 排队**（分钟～小时级）。  
**竞态**：Run A、B 先后读到「无 processing」，各给 PR-1、PR-2 贴标 → **双 processing**（直到下一次 reconcile）。  
**池子**：若门控未上，双 PR 的 CI/QA/Droid 全起跑 → **打满 ~5 台 runner**。  
**缓解设计**：贴标前 **全量扫 open PR 清多余 processing**；单事务顺序「先 strip 全部 processing → 再赋一个」；可选 GitHub **Issues/PR 锁**（无原生锁则接受短暂双标 + 门控仍限 pve）。

### 2. 假绿 / 假红 / ruleset

| 子场景 | 结果 | 指证 |
|--------|------|------|
| 只 skip 子 job，聚合未门控 | **假绿 `droid-review`** | 见 A2 |
| 只 skip 子 job，`ci-ok` 仍跑 | **假红 `ci-ok`**，队首冻队 | `ci.yml` L503–526 |
| 全无 job 跑 | required **pending**，不能合（预期） | VAL-M2-107 `tests/test_workflow_permissions_contract.py` L98–103 |
| 全 skip 且错误地把 skip 当 success 出队 | **假绿合入** | 与 `auto-merge-pipeline.yml` L406–409 `any(SUCCESS)` 相悖 |
| `quality-gate` 未跑但其它绿 | pending **quality-gate** | `quality-gate.yml` L134–136「has not reported yet」 |

### 3. 出队条件

| 子场景 | 错误行为 | 正确行为 |
|--------|----------|----------|
| CI `workflow_run` = cancelled | 误清标 / 误出队 | 忽略或仅当 **head 上无 in_progress** 再判 |
| CI 绿、Droid 红 | 若只盯 CI → 误出队 | 等 `droid-review` check **completed/failure** |
| 部分 check 重跑 | 旧 failure 仍显示在 API | 同 `quality-gate.yml` L117–118 **按名取最新非 cancelled** |
| force-push 新 SHA | 旧 SHA 全绿仍摘标 | 出队前 `gh pr view --json headRefOid` 与 check 的 `head_sha` 一致 |
| QG workflow success 早于 check 写入 | 过早摘标 | 以 **check-runs API** 为准，不以 workflow conclusion  alone |

### 4. 插队 / 绕过

| 子场景 | 能否绕过队列 |
|--------|----------------|
| 人工给非队首贴 `processing` | **能**（短暂），直到控制器 reconcile |
| 撕掉队首 `processing` | **能**瘫痪或乱序，属运维/权限问题 |
| Fork PR | 不能 bypass Droid 门禁，但能 **占 processing 冻队**（B4） |
| `push` → `main` | **故意绕过** PR 门控；`ci.yml` L23–24、`quality-gate.yml` L40–41 |
| `workflow_dispatch` Droid | **能**（若未门控）— A6 |
| Autofix `workflow_run`(CI fail) | **能**（若未门控）— `droid-autofix.yml` L27–30、L60 |
| `droid-task` / evolution schedule | **能**占 pve，但不走 PR 标签面 — C1/C2 |

### 5. 死锁

| 子场景 | 机制 |
|--------|------|
| 队首红 + 失败保持 processing | **全仓无新 processing**，by design（B2） |
| QG 轮询不到 `ci-ok`（门控 bug） | QG 超时失败，队首永不满足出队（B8） |
| 出队要求不存在的 check（如 governance） | 永不出队（C4） |
| 控制器跑在 pve | 与长任务抢机，控制器 run 堆积（A7） |

### 6. 安全（`pull_request_target`）

| 面 | 风险 | 说明 |
|----|------|------|
| 执行 PR 分支代码 | **低**（若零 checkout PR head） | 与 Droid 壳同模型；`droid-review.yml` L7–8 |
| `GITHUB_TOKEN` + `pull-requests:write` | **中（滥用非 RCE）** | 任意触发者可促使 workflow 给 **任意 open PR** 改标签（在 token 可写范围内）；需 **CODEOWNERS/仅维护者可开 PR** 或接受「内网私有仓」 |
| 伪造 `workflow_run` | **不可** | 事件由 GitHub 签发 |
| PR 标题/体注入 shell | **中** | 若控制器 `run: echo ${{ github.event.pull_request.title }}` 类写法 → 应用 `env:` + `github-script`（本仓 autofix/ci 已有 env 注入教训，`droid-task.yml` L56–57） |
| 读 checks/actions | **低** | 仅泄露 CI 结论元数据，私有仓可接受 |

### 7. 与现有 cancel / shards / autofix

```
processing PR 新 push
  → ci.yml cancel-in-progress 取消旧 CI (A4)
  → droid-review cancel-in-progress 取消旧 Droid (A4)
  → watchdog: CI failure 时 cancel 同 SHA 上 Droid (B7)
  → 新 run 需仍带 processing 标签 (synchronize 事件 labels 在 payload 里)

shards matrix 并行 (SHARD_MAX_PARALLEL 默认 3)
  → 与队列正交；门控只在 job 级，**单 PR 仍可多 shard 占多机**（符合目标）

autofix: CI fail + workflow_run
  → 若无门控，**第二路 pve** 与队首并行 (A6)
  → 若有门控且队首 CI 红，autofix 可能改代码 push → 再触发 synchronize → 控制器/CI 再跑
```

---

## 是否仍建议上？

**建议上**，理由：

- 现状是 **多 PR 无互斥** 直接抢 pve（`ci.yml` / `qa.yml` / `droid-review.yml` 均 per-PR/per-ref concurrency）。
- 控制器 + 门控把互斥收到 **标签 + 不占机**，与本仓已有 `auto-merge` `workflow_run` 模式一致（INFRA-212）。
- 对抗面大多 **可设计关闭**（P0），非根本性推翻。

**不建议**在仅实现「半个控制器」、未改聚合门控时上线——会比现在更难排障（假绿 + 双 processing + 冻队）。

---

## 上线前必须修的对抗项（P0 清单）

1. **出队**：仅当队首 PR **当前 `headRefOid`** 上，ruleset 相关 check（至少 `quality-gate`、`substrate-gate-suite`、`droid-review`；Dependabot 等走分支表）全部 `completed/success`；**禁止**单看某一个 `workflow_run.conclusion`。  
2. **忽略**用于推进的 `workflow_run` 事件里 `conclusion == cancelled`（除非明确「队首已无 in_progress 且 head 已终态」策略）。  
3. **所有 pve PR job + 聚合**（`ci-ok`、`gate-tests`、`qa-ok`、`quality-gate`、`droid-review`、shards 入口、evolution、autofix）统一 `processing` 条件；dispatch 仅维护者或同样验标。  
4. **修复 A2**：`droid-review` 聚合与 `shards` 同门控，禁止 `shards` skip 时聚合成功出绿。  
5. **控制器仅 `ubuntu-latest`**；脚本 **禁止 checkout PR head**；写标签用 API/script，PR 字段走 `env:`。  
6. **Reconcile 算法**：先移除所有多余 `processing`，再赋唯一队首；处理 `closed` PR 撕标并推进。  
7. **出队/入队策略文档化**：fork/draft/dependabot/失败冻队 + `do-not-merge` 逃生，避免 B3/B4 假死锁。

**P1 强烈建议（非阻断但对抗常打中）**：控制器 `schedule` 兜底；fork/dependabot **不入队或单独规则**；维护者以外禁止 `workflow_dispatch` 审查；监控「processing 计数 >1」告警。

---

## 参考文件速查

| 文件 | 对抗相关点 |
|------|------------|
| `ci.yml` L37–39, L503–526 | cancel；ci-ok 不认 skipped |
| `droid-review.yml` L65–67, L108–168 | cancel；聚合 always |
| `droid-review-shards.yml` L238–244, L307 | fork；dependabot/draft |
| `quality-gate.yml` L70–81, L94–120 | head SHA；轮询聚合 |
| `droid-review-watchdog.yml` L226–238 | CI 红杀 Droid |
| `droid-autofix.yml` L27–30, L60 | workflow_run 旁路 |
| `auto-merge-pipeline.yml` L406–409 | 防全 skip 假绿 |
| `tests/test_workflow_permissions_contract.py` L98–103 | required pending |
| `docs/guides/fullstack-test-plan.md` L62–63 | required 三元组 |
