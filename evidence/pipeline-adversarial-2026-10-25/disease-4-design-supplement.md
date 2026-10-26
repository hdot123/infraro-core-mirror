# 病四设计草案 · 补充条款（2026-10-25）

依据：用户《病四设计草案（施工预备 · 未拍板）》+ 本轮 `gh` / 代码实读。  
口径：闭合草案文末「拿不准」中已能闭合者；其余给出推荐默认（仍待大哥拍板）。

---

## A. 已闭合：修复 PR 的键载体（草案 §6.1）

**结论：现行修复 PR body / 分支名 / 标签均不承载 `**Rule ID**` / `**Location**`。**

| 载体 | 实况 |
|------|------|
| evolution issue body | 有标准两行（例 #442） |
| 修复 PR body（#445/#443/#438/#432/#424） | 仅 `Fixes INFRA-NNNN` + 叙事；**零** Rule ID/Location 行 |
| 分支名 | `feat/INFRA-1500-dedupe-capture-run` — Linear 标识，非 dedup 键 |
| `closingIssuesReferences` | #445 为空（Fixes 指向 Linear，不自动关 GH issue） |
| droid-task 契约 | 要求 `Fixes ${P_IDENTIFIER}`（Linear）或另一路径 `Closes #<issue>`；**未要求**埋 Rule ID/Location |

因此草案 §1.2「预设 PR 正文埋同格式两行」不是发现已有能力，而是 **必须新增的写端契约**（改 droid-task / autofix / 人工模板三处）。未改写端之前，PR 面 ` _parse_issue_fields(pr.body)` 命中率 ≈ 0，在飞抑制与「evolution 链 PR 计数」都会空转。

### A.1 推荐的键解析优先级（施工定案用）

1. **直读**：PR body 含 `**Rule ID**` / `**Location**` → `_parse_issue_fields`（目标态；写端补齐后主路径）
2. **间接 · GH issue**：body/`Closes #N` → 打开该 issue → 解析两行
3. **间接 · Linear**：`Fixes INFRA-N` / 分支 `INFRA-N` →（若仓内有 Linear→GH issue 镜像表或 API）落到 issue → 解析两行  
   - 今日镜像链未在 scanner 内；**勿在 v2 首迭代依赖 Linear API**，除非另开任务
4. **提取失败**：不进抑制集（草案已定），但 `suppressed_by_inflight_pr_unkeyed++` 打点

**第 0 步可并行的最小写端**：规定「凡从 evolution issue 开出的修复 PR，body 必须复制 issue 的 Rule ID/Location 两行」——比等 Linear 桥更短。

---

## B. 必须补进草案：同文件 DUPLICATE 键不会自收敛

`_normalize_dedup_location`：**跨文件**折成文件对；**同文件对保留行号**（`evolution_utils.py` L38–49，有意为之）。

今日 open 的 `CODE_HYGIENE_DUPLICATE_BLOCK` @ `test_publish_findings_baseline.py` 是 **多把不同键**（L860↔L950、L860↔L1514、L846↔L860…），不是一把键九张卡。

故：仅做「精确 (rule_id, location) ∪ open PR」**挡不住**「同一文件上另一行对」继续铸卡/开 PR——正是 9+1 堆叠的主形态。

### B.1 推荐叠加层（病四铸卡侧，与精确键并存）

对 `rule_id == CODE_HYGIENE_DUPLICATE_BLOCK`（或更一般：同文件 DUPLICATE）：

- 定义 **文件核** = 归一化后 location 中的路径集合（同文件对 → `{path}`）
- 若已存在 open issue **或** 可识别的在飞修复 PR，其文件核与 finding 文件核相交 → **抑制新铸**（日志 `suppressed_by_inflight_file`）
- 精确键抑制仍保留（跨文件对、非 hygiene 规则走精确键即可）

此层不取代 FINAL-PLAN「不用文件集当主键」——主键仍是 rule+location；文件核是 **hygiene 同文件洪水的附加闸**，须在草案 §1 单列，避免验收时以为精确键已够。

---

## C. 在飞上限计数口径（草案 §6.2）推荐默认

| 口径 | 今日可算性 | 建议 |
|------|------------|------|
| 「能提取到 §1.1 键的 open PR」 | 写端补齐前 ≈ 0 | 作目标态，不作上线首日唯一帽 |
| 全仓 open PR 总数 | 可算 | 作观测副指标（草案已有） |
| **过渡识别**：分支匹配 `INFRA-\d+` 或 body 含 `Fixes INFRA-` / `Closes #` 指向 evolution-found issue | 今日可算 | **首迭代存量帽用此计数**，写端埋键达标后切到「可提取键 PR 数」 |

`max_inflight_prs` 加入 `REQUIRED_CONFIG_KEYS` + `.evolution/config.yml`（现行仅有 `max_issues_per_tick: 5` 等流量帽，**无存量帽键**——已实读）。

建议初值：与 `max_issues_per_tick`(5) 同量级或略大（如 8–10），避免帽 < 流量切片导致永久 0 铸。

---

## D. FIFO 排序 / 踢出再入队（草案 §6.3）推荐默认

创建序 + 踢出后仍按 `createdAt` 排位 → **被踢者可能立刻仍是队头**（草案已意识到）。

**推荐补一条踢出规则（仍用 label 介质、零新载体）：**

- 踢出时：去 `merge-queue`，打上 `merge-queue-cooldown`（或注释时间戳）
- 冷却期内（如 1 个 schedule 周期 / 显式 N 分钟）**不得重新入队**
- 冷却结束后按 §2.2 重新入队；若仍坚持创建序，至少避免「踢出→下轮又是队头」死循环

若大哥改选 **就绪序**：入队时用 GraphQL `labeled` 事件时间或入队 comment 时间作排序键——比创建序多一读，但与「先绿先合」一致。未拍板前草案保留创建序 + **必须带冷却**。

---

## E. critical peel × PR 抑制（草案 §6.5）推荐默认

| 情形 | 推荐 |
|------|------|
| critical 回归 ∧ 同键 open issue | 维持 VAL-DUP-004 reopen |
| critical 回归 ∧ 同键 **在飞 PR** | **PR 面抑制优先**：不新铸、不第二 PR；可评论催审现 PR |
| critical 回归 ∧ 仅 closed-window issue、无在飞 PR | 维持 peel/reopen |

否则 critical 绕过会打穿病四在飞闸。

---

## F. `dedup_intra_tick` 未归一化（草案 §6.6）推荐默认

属疏漏风险面（同 tick 内行漂移对可能双计）。**建议在病四铸卡 PR 内顺手统一为双侧 `_normalize_dedup_location`**，并加单测；保留「同键最高 severity」语义。风险低、与 §1 同路径。

---

## G. ③ 工位与排队 check（草案 §3 / §5 / §6.7–8）

### G.1 并发「2」的表达

附议草案：不用共享 concurrency 组装「2」。  
补充：**首迭代允许上限 = 1（工位发放）**，与合并 `build concurrency=1` 同构，少竞态；「2」列为第二迭代（占位 label 原子性验证后再升）。

### G.2 排队可读 ≠ 必须立刻 Checks API

硬门 3 要「可读 pending」，label + 置顶评论可作 **v2a**（auto-merge 现行 `checks: read` 零改权）。  
Checks 排队名（`droid-review-queue` / `merge-queue`）作 **v2b**，与病三贴 check 身份（factory-droid / 专用 App / 改权）**同一次决策**落地，避免病四单独开权限洞。

### G.3 组键 fallback

附议：与病一同 PR 改 `droid-review.yml` L80，禁回落 `head_branch`；病四只登记依赖，施工可挂病一 PR。

---

## H. 工位超时 vs `timeout-minutes: 10`（草案 §6.9）推荐默认

**沿用 10 分钟为工位上限**（单钟）。踢出检测挂「下一轮 sweep」——接受小时级墙钟（schedule 稀疏）；快路径 `workflow_run` success 仍可加速感知。不在病四承诺分钟级踢出。

---

## I. merged PR 窗口（草案 §6.10）推荐默认

**v2 不查 merged PR。** 依赖 issue 仍 open + closed 7 日窗。加契约测试：模拟「PR merged、issue 仍 open」时精确键仍被 issue 面抑制。若测试暴露空窗，再加 `merged` 短窗（如 24h）为补丁。

---

## J. ① 入口落点（草案 §4）补充措辞

铸卡存量帽是源头闸，**等效**限 ① 洪水，但不是 workflow 层拒绝 `pull_request`。  
PR 创建侧（droid-task / 工人）应消费「在飞已满」信号（读 config 计数或 `gh` 查询）——**单列 follow-up 任务**，首迭代不声称 ① 已钉死。

---

## K. 建议写入草案正文的「拍板菜单」（5 题）

1. PR 键写端：是否强制 body 复制 Rule ID/Location 两行？（推荐：是）  
2. 同文件 hygiene：是否加文件核附加闸？（推荐：是）  
3. 存量帽计数过渡期：INFRA-分支启发式 vs 等埋键？（推荐：先启发式）  
4. FIFO：创建序+冷却 vs 就绪序？（推荐：创建序+冷却）  
5. 排队展示：v2a label+评论 vs 等病三一起上 check？（推荐：v2a 先）

---

## L. 改动清单增量（相对草案）

| 项 | 路径/位置 |
|----|-----------|
| PR 正文埋键契约 | `droid-task.yml` prompt；autofix/工人模板；契约测试 |
| `get_open_prs_dedup_keys()` | `evolution_scanner.py` / `evolution_utils.py` |
| 文件核附加闸 | `deduplicate` 之后或之内，限 DUPLICATE 同文件 |
| `max_inflight_prs` | `.evolution/config.yml` + `REQUIRED_CONFIG_KEYS` |
| `merge-queue` / `merge-queue-cooldown` labels | `ensure_labels` 或 setup-labels |
| mergeable 仅队头 | `auto-merge-pipeline.yml` + triage 后置门 + 契约 |
| 单 PR 快路径须入队 | 契约测试锁定行为变化 |
| critical×在飞 PR | `_peel_critical_regressions` 与 PR 键集交集 |
| `dedup_intra_tick` 归一化 | `evolution_utils.py` + 测试 |
