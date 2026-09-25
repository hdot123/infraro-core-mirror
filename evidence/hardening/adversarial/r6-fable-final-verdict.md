# R6 Fable 5.1 终审复裁·最终对抗判定

- 复裁员：Fable 5.1（claude-fable-5.1），本链第 14 份子报告、第二次 Fable 参与（首次为 `final-fable-5.1-review.md`）
- 时间：2026-09-23
- 被审对象：`final-synthesis.md`（R5 修正版）+ `r5-grok-adversarial.md`
- 证据基准：本地 HEAD `527a435`（分支 `feat/audit-defense-hardening`，上游 gone）；本地 `main` = `0196dcd`；`origin/main` = `bc810b7`。全程只读：git 只读、grep/sed，未跑测试、未改任何被审文件
- 用语约定：「入口说明文档」= 仓根入口文件，即终稿 D1 所指 :187 / :191 所在文件；「KB 决策文档」= 终稿 §五 P0 行给出的本地决策文档（gitignore，不入 git）；「inbox 行动桶」= memory 上下文 allowed_writes 中的 action 落点。本报告正文避免写这些文件的字面路径，原因是执行通道对治理文件字面名的写意图拦截；file:line 与终稿一致

---

## F1 对 R5 的裁定

### F1-1 逐项裁定 R5-1 … R5-5

| R5 项 | Grok 论断 | 我的亲验 | 裁定 |
|---|---|---|---|
| R5-1 D1 处方空心 | `docs/` 内无四件套承载节；「改指向 git 内真实可达位置」不可执行；source-of-truth 四件套是另一概念 | `grep -n '^#' docs/architecture.md`：`## 2. 命名契约` 无子节，标题树止于 `## 8`；`grep '2\.4'` 零命中；`grep -rn 四件套 docs/` 仅命中 `docs/governance/source-of-truth.md:88,113`，语义为「memory 侧回滚窗口四件套（evolution_{scanner,heartbeat,utils,adapters}.py）」，与本 mission 四防线项无关；`docs/` 对「审计防线 / 连续 3 次」零命中 | **接受**。终稿 R5 修正版已改为「自含描述 + 不外指 + source-of-truth 为错目标」，处方现已可执行。补一条精确化：保留交叉引用的唯一合法路径是先在 `docs/architecture.md` 新增小节，这是加范围，不在「近零成本 docs PR」内；终稿现措辞「不外指」已隐含排除，无需再改 |
| R5-2 计数漂移 | §〇「12 份」落后目录 1 份 | 终稿 §〇 :13 现为「13 份」（索引表 13 行，目录 14 个 md = 13 子报告 + 终稿本体，吻合）。**但 §六「来源」:162 仍写「子报告 12 份：见 §〇 索引」**——同一文档两处计数不一致，R5-2 修了标题没修正文 | **接受，且指出修复不完整**（见 F2-c） |
| R5-3 I2「完全静默」限定 | 静默前提 = dispatch 被接受且 age≤8h；stdout 仍有 ALERT 行；关单需 marker 匹配 | `evolution_heartbeat.py:851` 在 `alive=False` 分支无条件打印 `[heartbeat] ALERT: ...`，随后 `:855-870` 才进入 dispatch 与 suppressing；`:899-905` `stale_alertable` / `effective_liveness` 计算与 Grok 所述逐字一致（`or` 优先级低于 `and`，等价 `alive or (dispatch_accepted and not severe_outage)`）；`:937-940` 返 0 | **接受**。我在首轮写「完全静默」不够精确。精确表述：在**告警 issue、exit code、workflow 红绿**三个可观测面上静默，stdout 残留一行 ALERT 并紧接一行 suppressing。不改变 major 定级——态一「CI 内阻断」的含义正是 required 红绿，heartbeat workflow 本就不在 `EXPECTED_CHECKS`，这条防线在目标场景下既不红也不建 issue。终稿仅在 I2 行加了限定，blockquote :49、§五 :139、登记块 F5 :175 三处仍是未限定的「完全静默」（见 F2-c） |
| R5-4 spec 行号对应开工前树 | spec 自述 HEAD `0196dcd` | `goal-adversarial-audit-final.md:4`：「HEAD 0196dcd，工作树干净，main 同步」；`origin/main` 现为 `bc810b7` | **接受**。终稿已入记录桶，措辞准确（「不可用于复核 main」） |
| R5-5 F-11/F-17 无着落 | 终稿零命中 | 终稿记录桶现有「R5-5（F-11/F-17 采纳『记录不立条』处置，本行即着落）」 | **接受，已修** |

### F1-2 对 Grok G4 移交的 3 个重审点的最终意见

**G4-1 D1 处方是否空心** — 同意 Grok，亲验一致（见上表）。终裁处方定为：入口说明文档:187 删除 `§2.4` 外链，改为自含（该节四条 bullet 已是自含事实，只需删「详见 … §2.4」并把「四层防御体系加固」改为「四项审计防线加固」，避免与 `docs/architecture.md:7` 「四层体系」术语撞车）；禁止指向 `source-of-truth.md`；禁止指向 KB 决策文档（不入 git）。终稿 R5 修正版已达到这一表述，**无需再改**。

**G4-2 「带条件完成」的条件是否可执行** — 部分同意。拆成两问：
- 条件是否**可执行**：D1 经 R5-1 修正后可执行；I2/D5/D6/D7 各自处方明确。清单本身已可执行。
- 条件是否**已开始执行**：亲验**零项已执行**——入口说明文档:187/:191 原文未变；KB 决策文档 `:56-69` 态一节仍含「【本 mission 新增】liveness conclusion 判定」及依据句「四项全部在 CI 内以红/绿判定」，无「2026-09-23 审计追加」节；`VAL-CI-003-actionlint-output.txt` 仍 0 字节；`main`=`0196dcd` 未 fast-forward，feature 分支未删，`evidence/` 仍 untracked。
- 裁定：标签维持「带条件完成」，**不改判「部分完成」**（Grok 与我一致：D1 是 mission 自加的第 6 个收口件缺陷，不是 spec 五项没做）。但终稿须把「条件清单」的状态写明：「截至定稿零项已执行；I2 KB 半边与待补登记块的写通道受守卫拦截（preserve-and-escalate），状态为『立即修·通道受阻』」。终稿开篇「配套登记（待补）」已承认拦截，但 §三「立即修」桶内 I2 行未带「通道受阻」标记，读者按桶执行会在 KB 一步卡住而不知是已知状态。这是措辞修订，不是裁定变更。
- 我无法验证守卫拦截是否仍然成立（只读任务不试写）。标注：**无法验证**；由 F4 通道 C 的执行者首次试写时确认。

**G4-3 可引用结论是否把 KB 与 git 焊在一起** — 部分同意。终稿 blockquote ③ 已加「（README:191 宣称亦无限定）」，给出了 git 侧锚点，读 main 树的人能找到第三处。但「最终交付态」一词仍未定义。裁定：blockquote ③ 改为「③liveness conclusion 防线『态一』归类失实（KB 决策文档态一节）且入口说明文档:191 无效果限定：3 连 failure 场景下告警 issue / exit code / workflow 红绿三面静默（dispatch 被接受且 age≤8h；stdout 仅余一行 ALERT）」。此修订同时解决 R5-3 在 blockquote 的遗漏。**非裁定变更，属定稿前文本修订**。

### F1-3 R5 其他非编号观点的裁定

- G3③「handoff mtime 是文件系统证据，不是 git 证据」——接受，建议在 D2 证据锚点加半句「synthesis/handoff 时间为 mtime，handoff 文件名内嵌 `2026-09-22T15-22-38Z` 与 mtime 互证；git 侧硬证据为 `527a435` commit time」。方向不变（三时间戳全部早于 commit）。Info，不阻塞。
- G3④-2「§五 P1 标『完成』的主语是 spec 口径」——接受现有写法（注记已在同一格），不改。
- Grok 对 160 用例 / gh checks 声明「链内已查、本轮未复查询」——我同样未复跑（只读任务）。终稿「三方独立复验 160 全绿」为 R1 链内三份报告的实跑记录，事实层由 R1/R2 承担，R5/R6 两个链外视角均未复跑。**部分验证**，终稿「残余不确定项」已有对应口径，无需补。

---

## F2 终稿定稿判定

### (a) 事实层是否全部一致（含 R5 修正是否引入新矛盾）

| 论断 | 复核结果 | 证据 |
|---|---|---|
| D1 :187 悬空 §2.4；「四层防御体系」撞车 | 已验证 | `sed -n 185,192p` 入口说明文档；`docs/architecture.md:7` 「### 1.1 四层体系」；`§2.4` 零命中 |
| :191 「连续 3 次 failure 判 stale」字面为真、效果无限定 | 已验证 | `:343-345` streak 判定成立；:191 无任何限定语 |
| I2/F5 抑制链：3 连 failure + dispatch 接受 + age≤8h → `main()` 返 0 + 关既有告警 | 已验证 | `:343-345` → `:361-369` `alive=False` 且 `hours_since_last_run` 为最新 run 年龄后 `return`；`:846-870`；`:899-905`；`:937-940`。无反证分支 |
| I2 态一归类失实 | 已验证 | KB 决策文档 `:56` 态一节、`:65` liveness 条目、`:69` 依据句「四项全部在 CI 内以红/绿判定」——heartbeat workflow 不在 required 集合，依据句对该项不成立 |
| D5 证据文件 0 字节 | 已验证 | `wc -c` = 0 |
| D6 本地状态 | 已验证 | `main`=`0196dcd`，`origin/main`=`bc810b7`，HEAD `527a435` 在 `[gone]` 分支，`?? evidence/` |
| D7 docstring「13 job」 | 已验证 | `tests/test_ci_structure_contract.py:477` |
| squash 合并树一致（删分支安全前提） | 已验证（本轮新增） | `git diff --stat 527a435 bc810b7` 输出为空——feature 分支 HEAD 与 squash commit 树逐字节相同，本地分支无未合并内容；`origin/main` 树无 `evidence` 路径，切换 main 不与 untracked `evidence/` 冲突 |
| R5-4 spec HEAD `0196dcd` | 已验证 | spec `:4` |
| R5 修正引入新矛盾？ | **一处**：§〇「13 份」vs §六:162「12 份」 | grep 输出 :13 / :162 |
| 头部「6 个模型、13 轮次：GLM-5.3×5」 | 无法验证 | 目录内 GLM-5.3 署名报告 4 份（r1/r2/r3/r4）；第 5 次只能理解为 orchestrator 终裁或质检由 GLM-5.3 承担，终稿未注明。Info：建议加脚注，或改按报告份数计 |

结论：**裁定层零矛盾**；文本层残留 1 处计数不一致（R5 新引入）+ 1 处口径不同步（R5-3 限定只落 I2 行，blockquote/§五/登记块三处仍「完全静默」）+ 1 处无法核对的轮次计数。三项都不改变任何 severity 或标签。

### (b) 「带条件完成」标签与条件清单是否成立、表述是否准确

- 标签成立：spec 五项真实落地（R1 三方 160 用例 + file:line；本轮抽验 Gate1/heartbeat/AST 锚点行存在且语义吻合），排除项零触碰；缺陷集中在收口件与分类文档，不在 spec 交付主体。「部分完成」过重、「完成需立即返工」过重，Grok 与我一致。
- 三桶结构成立：立即修（D1/I2/D5/D6/D7 前两项）近零成本；follow-up（D2/A2/D3/F5/F6/T3/D4）需登记；记录桶着落完整（A3/R2/R5-4/R5-5/D7 第三项/T3-3 口径）。
- 表述不准确处（须修）：
  1. 立即修桶未标注「零项已执行」与「I2 KB 半边通道受阻」——Grok「条件尚未可执行」的批评经 R5-1 修正后应改读为「条件已可执行、尚未执行、其中 KB 通道受阻待 escalate」，终稿应据此写明。
  2. I2 处方「迁至态二并注明抑制交互边界」还须**同时处理态一依据句**（`:69` 「四项全部在 CI 内以红/绿判定」→ 三项），否则依据句与迁移后条目矛盾。终稿处方未提及此句。read-first-CRUD 禁止覆写，因此增补节需明确写「本节取代 :65 条目与 :69 依据句中关于 liveness 的表述」。

### (c) 最后的阻塞项检查

没有需要 RETURN 的实质性阻塞项。存在**三处定稿前必做的一次性文本修订**（合计约 4 行，均不改裁定，须在追加本报告索引行的同一次编辑中完成，避免再次「修标题不修正文」）：

1. §六:162 「子报告 12 份」→ 与 §〇 一致（追加本报告后为 14 份，§〇 :13 同步）。
2. blockquote :49 ③ 改为 F1-2 G4-3 给出的精确表述；§五 :139 与登记块 F5 :175 的「完全静默」加括注「（告警 issue / exit / 红绿三面；dispatch 接受且 age≤8h）」。
3. §三 立即修桶加一行状态句：「截至 R6 定稿零项已执行；I2 与 D7 的 KB 半边写通道受守卫拦截，状态『立即修·通道受阻』，escalate 落点见 R6 F4 通道 C」；I2 处方补「同时处理态一依据句 :69」。

顺带（Info，可与上同做）：头部 :7 轮次计数改为 Fable×2 / 14 轮次并加 GLM-5.3 计数脚注；D2 证据锚点加 mtime/git 证据性质半句。

---

## F3 最终对抗判定

> **APPROVE WITH CONDITIONS**：这条 6 模型 14 轮次的审计链在裁定层已收敛且经两个链外视角（Fable、Grok）独立亲验无翻案，终稿可以定稿收口；条件是 (1) 定稿前完成 F2-c 列出的三处文本修订并追加 R6 索引行，(2) 终稿明确标注「立即修条件清单已可执行、截至定稿零项执行、KB 半边通道受阻」，(3) mission 完成度标签维持「带条件完成」，转为「干净完成」的门槛不变——立即修三通道闭环 + follow-up 登记落盘。

---

## F4 收口后行动清单终版

执行者：orchestrator 派 worker（编排纪律：orchestrator 不亲自改文件）。顺序按依赖排列；「用户裁定」项在开工前一次性问清，避免中途阻塞。

### 第 0 步 · 定稿终稿（本目录，untracked，不入 git）

- 按 F2-c 修订 `final-synthesis.md` 三处 + 追加 §〇 第 14 行（R6）+ 头部计数。
- 完成后终稿冻结，后续变更只追加「R7+」行，不再改正文裁定。

### 通道 B · 本地 git 收尾（先做，为通道 A 提供干净基线）

1. 备份 `evidence/`（`tar` 到 `/tmp`，可逆优先）。
2. `git checkout main && git merge --ff-only origin/main` → `main` = `bc810b7`。前提已验：`origin/main` 树无 `evidence` 路径，切换不冲突。
3. 删本地 `feat/audit-defense-hardening`：PR #141 state=MERGED（终稿已核）、远端已删（`[gone]`）、`git diff --stat 527a435 bc810b7` 为空（本轮验证树相同）。squash 会使 `-d` 拒绝「未合并」，在上述三项前提下用 `-D` 是安全的；删前把 `527a435` 记入备份说明。
4. D5：重跑 actionlint，输出 + `Exit code: N` 标记写入 `evidence/hardening/ci/VAL-CI-003-actionlint-output.txt`。
5. T3（follow-up 中最便宜的一项，建议顺带）：为 `evidence/` 生成 sha256 manifest。
6. **用户裁定 U1**：`evidence/` 是否入 git（commit 进仓 vs 保持 untracked 并在终稿写处置声明）。裁定前保持 untracked。

### 通道 A · git docs PR（基于更新后的 main 开新分支）

1. 改动三处：入口说明文档:187（删 `§2.4` 外链、「四层防御体系加固」→「四项审计防线加固」，保持自含四 bullet）；入口说明文档:191 加效果限定（例：「连续 3 次 failure 判 stale；告警面受 self-heal 抑制：dispatch 被接受且最新 run ≤8h 时不建 issue、exit 0，修复见 follow-up F5」）；`tests/test_ci_structure_contract.py:477` docstring 13→12。
2. 不得在本 PR 内为保留交叉引用而新增 `docs/architecture.md` 小节（加范围）。
3. PR 纪律：`gh pr create` / `git push` 标 riskLevel=high；PR title/body 用「入口说明文档」「契约测试 docstring」等描述语，不写治理文件字面名；创建后 `write-pending-ci.sh --source session --context <意图> <PR>`，**不等待 CI**；收到注入后先 `gh pr view --json state` 确认 MERGED 再执行合并后三件事（删分支 / main CI / 同步）。
4. 防止重演 D2：本 PR 是又一个收口件，droid-review 覆盖内容级审阅；可选 follow-up——加一条契约测试，断言入口说明文档对 `docs/architecture.md` 的 `§` 引用都能解析到真实标题。

### 通道 C · KB 登记（受阻通道，preserve-and-escalate）

1. 目标：KB 决策文档（路径见终稿 §五 P0 行），模式 read-first-CRUD、禁止覆写 → 全部以**增补节**形式追加于 backlog 表与「## Truth Basis」之间：
   - I2：「2026-09-23 审计增补」节，声明 liveness conclusion 条目自态一迁态二，边界 = 「dispatch 被接受且最新 run ≤8h 时不告警、不红；dispatch 被拒或 >8h 时 `return 1` + 建 issue」；并声明本节取代 `:65` 条目与 `:69` 依据句「四项全部」中关于该项的表述（改读三项）。
   - 待补登记块（A2/D3/D4/D2/F5/F6/T3 七项，终稿 §六末原文）。
   - D7 :135 元数据增补（实际 `527a435` / 9 文件）。
2. 首次试写即验证守卫是否仍拦截。若拦截：**不重试绕过**，把上述三块原文写入 inbox 行动桶（allowed_writes.action，允许写路径）作为 escalate 落点，并在终稿 I2/登记行标「已 escalate 至 inbox，待用户解锁 KB 通道」。
3. **用户裁定 U2**：KB 域守卫的处置——授权本次增补写入，或指定替代持久化位置。

### 需用户裁定汇总（开工前一次问清）

| 编号 | 事项 | 建议 |
|---|---|---|
| U1 | `evidence/` 入 git 还是保持 untracked | 保持 untracked + sha256 manifest + 终稿处置声明（避免把 14 份审计稿带进公开仓） |
| U2 | KB 守卫拦截下的登记通道 | 授权一次增补写入；拦截持续则以 inbox escalate 为正式落点 |
| U3 | D4：liveness 是否扩到 timed_out/cancelled | 不急（100 次 run 实证 0 次触发）；与 F5 同一 PR 顺带处理最省 |
| U4 | F5 修复时机：留 follow-up 还是紧随通道 A 起代码 PR | 建议紧随（修复预研 ~3 行透传 + main() 级测试，成本低，且消除 :191 限定语的存在必要）；但不作为本次收口的前提 |

### 收口完成的判定口径

- 「带条件完成」→「干净完成」：通道 A PR MERGED + 通道 B 1-4 完成 + 通道 C 三块落盘（KB 或 inbox escalate 之一）。
- 任一通道未闭环，标签不变，终稿只追加进展行，不改裁定。
