# R5 Grok 4.7 链外对抗审查（对已收敛终稿的独立红队）

- 审查员：Grok 4.7（grok-4.7），独立于 R1–R4 与 Fable 5.1，全程只读
- 审查时间：2026-09-23
- 被审对象：`evidence/hardening/adversarial/final-synthesis.md`（自称 R3 收敛版 + R4 修复后）及其链外前作 `final-fable-5.1-review.md`
- spec 基准：mission `7eedcdc4-98d7-4c44-9770-43022debbe28` 的 `reports/goal-adversarial-audit-final.md`
- 证据基准：本地工作树 HEAD `527a435`；`origin/main` = squash `bc810b7`（2026-09-22 23:34:30 +0800）；未跑测试、未改被审文件
- 立场：不预设链内结论为真，也不为反对而反对。凡与 Fable/DS4/GLM 一致处，均经本轮代码/git 亲验后才采纳
- 文中「P0 决策文档」指本地 KB decisions 下的 architecture-evolution 目标修正稿（gitignore，不入 git）。本报告不写其绝对路径

---

## G1 三大 major 重验

### I2/F5 — 与三方一致，且比终稿写得更窄的地方我补了限定

**结论：scanner 持续 3 连 `failure` 时，告警面被 self-heal 抑制链完全静默；`main()` 返 0；既有 `scanner_stale` issue 会被当作已恢复关闭。与 Fable / DS4 / GLM-5.3 一致，不回滚。**

第一手路径（`src/infra_core/engine/evolution_heartbeat.py`，行号以当前树为准）：

1. `:54` `CONSECUTIVE_FAILURE_STALENESS = 3`。`:343-345` 只对 newest-first 的前 3 条做 `conclusion == "failure"` 全等。3 连 failure → `all_failed_streak = True`。
2. `:361-369`：年龄仍落在 2h 阈值内（fresh failure）时，`alive=False`，但 `hours_since_last_run` 是最新一次 run 的年龄（远小于 `:46` 的 `SCANNER_SEVERE_STALENESS_HOURS = 8`），然后 `return`。年龄不会被 streak 改写成「老」。
3. `:846-870`：`alive=False` 才进 dispatch。`trigger_scanner_dispatch()`（`:399-427`）只看 `gh workflow run` 的 returncode。对一个**存在但持续失败**的 workflow，GitHub 接受 dispatch → `dispatch_accepted=True`。`stale_hours` 不大于 8 → else 分支打印 suppressing，不告警。
4. `:899-902`：`severe_outage = False`（age 已测且 ≤8）。`stale_alertable = not alive and (not dispatch_accepted or severe_outage)` = **False**。
5. `:905`：`effective_liveness = {"alive": liveness["alive"] or dispatch_accepted and not severe_outage}`。运算符优先级是 `or` 低于 `and`，等价于 `alive or (dispatch_accepted and not severe_outage)` = **True**。
6. `:586-593` `compute_current_anomalies`：`alive=True` 则不含 `scanner_stale`。`:627+` `resolve_cleared_alerts`：recorded 含 `scanner_stale`、current 不含 → `cleared and not (recorded & current)` 成立 → **关闭既有告警**。覆盖数据不可靠时才 fail-closed 跳过（`:644-646`），3 连 failure 场景不走这条。
7. `:925` `anomaly_count` 不计 stale。`:937-940` 不进 `return 1`，打印 `All checks passed`，**返回 0**。
8. 消费面：`.github/workflows/evolution-heartbeat.yml:141-142` 是 `python -m infra_core.engine.evolution_heartbeat`，无 `continue-on-error`。exit 0 = workflow 绿。该 workflow 自带 `schedule: 53 */2 * * *`（`:71-72`），不在 `quality-gate.yml` 的 `EXPECTED_CHECKS`（`ci-ok substrate-gate-suite`）里。所以「态一：CI 内阻断 / required 链内红绿」不成立——heartbeat 红了也不挡合并，而本场景它甚至不红。
9. 永续：每个 tick 再 dispatch 一次，新 run 的 `createdAt` 把年龄钉在 8h 以下。`severe_outage` 在「每次都跑、每次都失败、dispatch 一直被接受」这条路径上结构上不可达。抑制条件被防线自己的自愈动作喂饱。

**我补的、终稿未写清的限定（不推翻结论，但终审应写上）：**

- 静默的前提是 dispatch **被接受**。token/403/workflow 缺失 → `dispatch_accepted=False` → `stale_alertable=True` → `return 1` + 建 issue。终稿「完全静默」应限定为「dispatch 接受且 age≤8h」。这正是目标场景（workflow 在、只是一直红），所以限定不削弱 major。
- `hours_since_last_run` 取的是**全部 5 条里最年轻的可解析时间戳**，不是「最新那条」。若最新 3 条失败但更老的一条时间戳解析失败被 `continue` 掉，年龄仍来自失败 run，结论不变。不构成反例。
- 日志行 `[heartbeat] ALERT: ... last 3 runs concluded failure`（`:852`）**会打印**。终稿「完全静默」指的是告警 issue / exit code / required check，不是 stdout。运维若只看 Actions log 能看到一行，看不到 issue、看不到红。这与「态一阻断」仍然矛盾。
- 关闭既有告警有一个窄豁免：issue body 不含 `_ANOMALY_SCANNER_STALE_MARKER`（`"evolution-scan workflow has not run recently"`）则 `recorded` 为空、`:656-657` `continue`、不关。marker 不一致的历史 issue 不会被误关；marker 一致的会被关。终稿应写「marker 匹配的既有 issue」。

P0 决策文档仍把该项放在态一（「【本 mission 新增】liveness conclusion 判定」），依据句是「四项全部在 CI 内以红/绿判定」。README:191 仍是「连续 3 次 failure 判 stale」，无限定。分类失实在当前树上仍在。

I2（分类面，立即修文档）与 F5（代码面，follow-up 改 `main()`）的拆分成立。把代码静默升成「mission 未交付 P1」是过重——spec 第 53 行批准口径就是「连续 failure 视为 stale」，`:343-345` 做到了。失实的是**把做到的判定写成态一阻断**。

### D1 — 亲验成立；「改指向 git 内真实可达位置」这条处方是空的

- `README.md:187` 原文：「本仓库已完成四层防御体系加固，详见 [`docs/architecture.md`](docs/architecture.md) §2.4。」
- `docs/architecture.md` 标题树：`## 1`（1.1 / 1.2）/ `## 2. 命名契约`（无子节）/ `## 3`–`## 8`。`§2.4` 不存在。`## 5.1` 是 QA 门禁，不是防线四件套。
- 全 `docs/` 对「四件套 / 审计防线 / 连续 3 次 failure」无承载小节。唯一「四件套」命中在 `docs/governance/source-of-truth.md`，指的是 memory 回滚窗口另一组四文件，**指过去是错的**。
- 「四层防御体系」与 v3「四层体系」（Evolution Core / Governance / Rule Packs / Delivery，`docs/architecture.md:7-16`）撞车。README 这一句把「本 mission 四个防线项」说成了「四层体系加固」。info 级措辞问题，major 来自不可达锚点 + 已进 main。

**终稿立即修处方「改指向 git 内真实可达位置」不能执行。** git 内没有该位置。可执行的只有终稿括号里的后半句：删掉 `§2.4`、改成自含四 bullet（正文已经自含），或在 `docs/architecture.md` **新写**一节再指向它。后者是加文档，不是「改指向」。把「或自含」写成可选项，会让下一个执行者先去找一个不存在的锚点。

severity 维持 major、不升 critical：无消费路径解析 `§2.4`，bullet 四条内容相对代码为真（Gate1 fail-closed、streak 判定、零红移序、AST 锁都在）。过头的是「详见 §2.4」和「四层防御体系」这两个词，不是四条事实。

### D2 — 时间线与 fulfills 亲验成立

| 锚点 | 本轮实测 |
|---|---|
| `527a435` commit time | `2026-09-22 23:28:57 +0800`（`git show -s --format=%ci`） |
| user-testing `synthesis.json` / `validation-state.json` mtime | `2026-09-22 23:22:15 +0800` |
| user-testing handoff 文件 mtime | `23:22:38` |
| readme handoff 文件 mtime | `23:30:28` |
| 差值 | synthesis → README commit = **+6m42s** |

mission `3dfa75a1` 的 features.json 中 `readme-defense-hardening-note.fulfills` 为 `[]`。其余实现 feature 的 `fulfills` 均非空（VAL-GATE1 / VAL-HB / VAL-IMPORT / VAL-CI / VAL-DOC）。readme feature 的 `preconditions[0]` 原文就是「milestone hardening 已验证（21/21 断言 passed）」——orchestrator 自己把 21/21 放在 README **之前**的门，不是最终 9 文件态的认证。

同意 Fable 的措辞收窄：README 是 PR #141 最终 head 的一部分，逃逸的是**断言级内容核验**，不是 CI。终稿 D2 行已写成「零断言覆盖……CI 29 check 实跑在含 README 的 head 上」。本轮没有重跑 `gh pr checks`；CI check 数字沿用 Fable 已记录的第三方查询，不把未复查询的数字再升级。

### 抽验锚点

**F6 — 成立，且比「表头被登记」更具体。**

- `substrate/gates/gate_common.py:184` 排除集是 `{"", "---", "Entry", "Path prefix", "Item"}`，没有 `"Template"`。
- `substrate/gate0-exemptions.md:22` Gate 1 表头首列就是反引号包裹的 `Template`。解析器 `cells[0].strip` 掉反引号后得到 `Template`，不在排除集 → 进入 `entries`。
- 消费面在 `substrate/gates/gate1_interface.py`：`if any(token and token in err for token in registered)`。子串包含，不是整 token 相等。因此任何错误字符串里出现 `Template`（大小写敏感）都会被标成 REGISTERED STOCK，从 `new` 里拿掉，从而不触发 `if new: return 1`。
- 今天的表体是 `watchdog.yml` / `droid-review.yml` 等，错误文案未必含单词 Template，所以是 **latent**，不是当前必红变绿。定 minor 正确，不要升 major。修复是排除集加 `"Template"`（`Break` 若也会撞英文错误串，本轮未逐条对错误文案，留给修复者，不另立条）。

**D3 — 成立。** `tests/test_evolution_heartbeat.py` 对 `--json` 零命中。生产 `:322-323` 请求 `"--json", "status,conclusion,createdAt"`。删掉 `conclusion` 字段，mock 若仍回 conclusion，测试仍绿、防线静默失效。minor（高优先）合理：这是接缝锁缺失，不是生产逻辑错误。

**R4 修复质量 — 三处阻塞项已修，但「12 份」和新的索引裂缝还在。**

| R4 项 | 本轮 |
|---|---|
| B1 登记块 T3 锚点 | 已改为「Fable T3-4」。T3-4 确实是证据信任根。**已修** |
| B2 计数残留 | §六已是「子报告 12 份」，不再是「7+1」。**已修** |
| B3 可引用结论 | blockquote 已列三处 major（§2.4 / 零断言覆盖 / 态一归类失实）。**已修** |
| 旧口径 | 正文处置表不再把 A5 标成 minor/记录；A2 单标签 minor。作为**当前裁定**的旧口径已清。历史叙述里仍有「四模型两轮」「立即修 ×4 / follow-up ×5」，但是在 §〇 索引行和 §四方法论里**指 R1/R2 当时**，不是把终裁写回去。不构成一稿两裁 |

未清干净的：

1. **§〇 标题「12 份」与目录不符。** 同目录现有 13 个 markdown：索引 12 份 + 已存在的 `final-fable-5.1-review.md`。索引表第 9 行单独列了 Fable，正文多处引用它，但「12 份」把 Fable 算进 12 又没把文件名放进闭合计数——读者按标题去列目录会对不上。R4 的 B2 修的是 7+1→12，修完又落后于目录现实 1 份。这是审计链自己的计数漂移，severity = 文本 minor，不是 mission 缺陷。
2. **R3-DS4 索引行仍写「清单增 F-13/F6」，R4 已指出应写成 F-13(=F6)/T3-4。** 终稿自称「B1-B3 已修」，R4 的非阻塞措辞建议未吸收。不阻塞，但「已修」字样覆盖范围大于实际。
3. **F-11 / F-17 仍无着落句。** 终稿检索零命中。R4 已标「不阻塞」。本轮确认它们还在 `r1-ds4-redteam.md`，终稿既没入桶也没写「记录不立条」。

---

## G2 终判独立评判

**独立判定：维持「带条件完成」，但把终稿的条件从「立即修可闭环」改判为「条件尚未可执行」。不改判「部分完成」，也不改判「完成但需立即返工」——后一个标签会把文档债和一行代码 follow-up 说成交付主体没落地。**

判据（与 Fable 同一把尺子，我重新量了一遍）：

- spec 第 6 节整改项里，本 mission 认领的是 P0 + 四个零成本项。Gate1 fail-closed、streak 判定、零红移序、AST 锁、P0 决策文档，代码与决策文档正文都在。排除项（B1 governance 入链 / watchdog / F8 schedule / P3 / B5）决策文档 backlog 有表、PR 文件面未装成完成。这一层「5/5 spec 项落地」成立。我没有重跑 160 测试；本轮不把「160 全绿」当作自己的实测，只把「代码里有这些判定、决策文档引用了这些行」当作已验。
- 「部分完成」过重。它把 mission 自加的第 6 个 feature（README，`fulfills: []`，不在 spec 整改表）的悬空锚点，算进 spec 交付主体。D1 是收口件缺陷，不是 P1 Gate1/heartbeat 没做。
- 「完成但需立即返工」也过重。返工暗示行为错误必须改代码才能叫完成。I2 的立即修是**改分类**（态一→态二 + README:191 加半句），不是把 streak 判定撕掉。F5 改 `main()` 是效果层 follow-up，spec 没要求「3 连 failure 必须建 issue」。把 F5 拉进「立即返工」等于事后加需求。

**我不同意终稿的地方：它把「带条件完成」写成好像条件是一份可执行清单。不是。**

1. D1 的「改指向 git 内真实可达位置」没有目标（见 G1）。执行者按字面做会失败或指错文件。
2. I2 的「决策文档增补节回填」本轮仍未写入。决策文档态一节原文还在。终稿自己承认 KB 域守卫拦截、登记块未落。所以「立即修」不是「差一个 commit」，是「差一个被守卫挡住的写通道 + 一个空心指向」。
3. README:191「连续 3 次 failure 判 stale」**字面为真**（`:343-345` 确实判 stale）。过头的是效果层（不告警、不红）和态一归类。限定句必须写效果，不能只改分类标签却让 README 继续被读成「会告警」。

因此我的条件版本：

- **可执行的立即修**只有三件：README:187 改为自含（删除 `§2.4`，四层→四项）；README:191 加效果限定；P0 决策文档增补节把 liveness 从态一迁到态二并写「dispatch 接受且 age≤8h 时不告警、不红」。KB 通道若仍被守卫拦截，条件就继续开着，不得宣称已闭环。
- **不得假装能做的**：在 `docs/architecture.md` 里「找到」§2.4。找不到。要指向就必须先写那一节，那是加范围，不是修悬空。
- **follow-up 保持**：F5（suppression 加 failure-streak 穿透 + `main()` 级测试）、D3 接缝锁、F6 `"Template"`、D2 流程（收口件至少一条内容锚点断言）。这些不阻挡「spec 项已落地」的标签，但阻挡「干净完成」。

「21/21 用于最终交付态构成 by omission 误导」我同意，且同意 Fable 的收窄：误导发生在有人拿 21/21 为 9 文件态背书时，不是验证流程造假。features.json 把 21/21 写在 README 的 precondition 里，orchestrator 没有把 README 伪装成 VAL-* 已覆盖。缺陷是**没有为收口件补断言**，不是**伪造断言通过**。

---

## G3 共享盲区

六个模型实例 + Fable 仍共享、本轮能钉死的盲区如下。不把「我没读到所以可能有」算发现。

### ① spec 被当成正确前提 — 有一处真实过时，审计链无意识沿用

spec 终稿头部自述仓库状态：「HEAD `0196dcd`，工作树干净，main 同步」。本轮 `origin/main` 已是 `bc810b7`。spec 第 8 节证据索引的行号（`evolution_heartbeat.py:325-350`、`ci.yml:548-573`、`gate1_interface.py:179-182`）是 **mission 开工前的树**。

审计链正确地把 spec 当「批准口径」（连续 failure 视为 stale、排除 B1 等），这没问题。盲区是：没有人标一句「spec 的 file:line 与现状树已分叉，不得把 spec 行号当 main 行号复核」。Fable 复核的是终稿引用的**当前**行号，不是 spec 索引。若下一轮终审用 spec §8 的行号去 `origin/main` 对代码，会对不齐，然后误判「实现漂移」。这是过时，不是自相矛盾。spec 正文的三态/八条修正与 v3 方向，本轮未发现内部自相矛盾；我没有逐条重证 spec 的 8 处修正（那是上一 mission 的对象，超出本轮被审物）。

### ② 「立即修」可执行性未被验证 — 成立，且是本轮最重的新点

D1 处方的前半句空心，见 G1。`docs/architecture.md` 没有四件套小节。`docs/governance/source-of-truth.md` 的「四件套」是另一个概念，指过去会制造第二个错指。

终稿写「仓内三处改动可并一个 docs PR」时，把 D1 算成仓内可改。README 可以改；「指向真实可达位置」不可以，除非同时改 `docs/architecture.md` 新增一节。新增一节不在终稿的「近零成本改指向」里，范围被写小了。

I2 的 KB 半边同样不可执行于当前通道：终稿开篇已记录守卫拦截。后续报告仍把 I2 放在「立即修」而不是「立即修·通道受阻」。标签没有反映阻塞。

### ③ 对 mission 产物的信任仍高于代码 — 部分成立，没有新的事实级翻案

本轮能看到的具体点：

- D2 的 +6m42s 依赖 synthesis.json / handoff 的 **mtime**。commit 时间是 git 对象，硬；mtime 可被 touch 改。本轮 mtime 与文件名时间戳（`2026-09-22T15-22-38Z` 对应 23:22:38 +0800）一致，且早于 `527a435`，方向对。但审计链把 mtime 写成与 git 同等的「三时间戳钉死」。应降一句：handoff 时间是文件系统证据，不是 git 证据。结论方向不因此翻转。
- features.json 的 `fulfills: []` 是 mission 自述。我读了文件，它确实是 `[]`。这一条可以信，因为它是被审物本身，不是被审物的证人。
- 160 passed / PR check 全绿：本轮**没有**重跑、没有重查 `gh`。终稿与 Fable 把它们当第一手。我把它们标成「链内已查、本轮未复查询」，不拿来支撑任何新裁定。若终审要维持「160 全绿」作为完成度证据，应自己再查一次，不要引用本报告。

没有发现「handoff 写了、代码没有」的新增翻案。I2 恰好是反例：handoff 披露了抑制，代码证实了抑制，审计链一度不信代码只信「范围内」。那一处已经被 Fable 打破，不是本轮新盲区。

### ④ 「R3 收敛版」声明与文本仍有未发现的不一致

1. 标题「R3 收敛版」+「R4 修复后」，但 §〇 仍写「12 份」，目录在 Fable 报告写入后已是 13 个文件（不含本 R5）。计数修了一次，立刻再漂。
2. 可引用结论把 I2 说成「最终交付态含三处 major」。I2 的分类文本在 gitignored 的 P0 决策文档里，不在 PR #141 的 9 文件里。交付态（git）里真正在场的是 README:191 无限定 + README:187 悬空。把 KB 归类错误算进「最终交付态三处 major」会让只看 main 的人去 git 里找第三处、找不到。应拆开：git 交付态 major = D1 + README:191 效果层无限定；KB 交付态 major = I2 态一；D2 是流程 major，不是一行坏代码。
3. R4 说「旧口径清剿干净」。历史索引行仍保留「立即修 ×4 / follow-up ×5」作为 **R2 当时结论**。这是对的写法，但标题没有「历史行 vs 终裁行」的分隔，下一个只读索引表的人会把 R2 行当成终裁。severity = 文本 info。
4. 终稿 §五把 P1 heartbeat 标「完成」，注记里才说告警被静默。注记在，不算一稿两裁；但「完成」的主语是 spec 口径，不是效果口径。G2 已要求终审不要把这两个「完成」并成一个词。

---

## G4 移交 Fable 的重审点 + 新增发现

### 最希望 Fable 5.1 复裁重新审视的 3 点

1. **D1 立即修处方是否空心。** 请在 `docs/` 内亲自找四件套承载节。我的结果是没有。若同意，终裁应把处方改成「删除 §2.4、改为自含；禁止指向 source-of-truth 里的另一组四件套；若要保留交叉引用，必须先在 `docs/architecture.md` 新增小节（这是加范围，不是改链接）」。
2. **「带条件完成」的条件是否可执行。** I2 的 KB 半边仍被 KB 域守卫挡住，P0 决策文档态一原文还在。请裁定：通道未通时，标签是否必须附「条件未开始执行」，而不是只列清单。不要为此把 mission 改判部分完成。
3. **可引用结论里的「最终交付态三处 major」是否把 KB 与 git 焊在一起。** README:191 字面为真、效果过头；态一归类在 gitignore 的决策文档。请拆开写，避免下一次审计用 main 树复核时把 I2 判成「终稿虚构」。

### 新增发现（终清单之外）

| ID | 内容 | 建议级别 | 是否进入终清单 |
|---|---|---|---|
| R5-1 | D1「改指向 git 内真实可达位置」不可执行；`docs/` 无四件套节；source-of-truth 的四件套是错目标 | major（处方缺陷，不是新的产品缺陷） | 是。它改变立即修怎么做，不增加第四个产品 major |
| R5-2 | 终稿「12 份」与目录 13 文件（含 Fable 报告、不含本 R5）不一致；R4 B2 修完即再漂 | 文本 minor | 可并入终稿维护，不必进 mission backlog |
| R5-3 | I2「完全静默」应限定为：dispatch 被接受且 age≤8h；stdout 仍有 ALERT 行；关闭告警还要 marker 匹配 | 限定，不升不降 | 写入 I2/F5 行的证据栏，不新开 ID |
| R5-4 | spec §8 file:line 与现状树已分叉（spec 自述 HEAD `0196dcd`） | info | 终审注记一句即可，防用 spec 行号复核 main |
| R5-5 | F-11 / F-17 仍无「记录不立条」 | info | R4 已说，本轮只确认未修 |

无其他终清单外的产品缺陷。没有发现第四个 major。没有发现 I2 的反例分支能在「3 连 failure + dispatch 接受 + age≤8h」下把 `main()` 打成非 0。

---

## R5 结论

三大 major 的事实层经代码与 git 亲验成立，与 Fable/DS4/GLM 一致；「带条件完成」标签维持，但其立即修处方有一处空心（D1 无 git 内可指向小节）且 I2 的 KB 半边仍未落盘，条件清单还不能当作已可执行。
