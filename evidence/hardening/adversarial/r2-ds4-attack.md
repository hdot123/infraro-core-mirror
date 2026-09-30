# R2 红队报告：攻击另外三位审计员的「全清」结论（DS4.1-Flash 二轮对抗）

- **审计对象**：不是 mission 本身，而是 R1 三份结论的**可信度**——GLM-5.3 主审「5/5 完成，无虚报；9 份 handoff 的 discoveredIssues 无一被忽略；validation-state 21/21 与实测吻合；偏差仅 1 项」与 Qwen 代码审查「优秀，0 critical / 0 major / 0 minor，仅 4 info」。
- **基准**：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`；repo 现树（本地分支 `527a435`；`origin/main` = `bc810b71`）。
- **手段**：严格只读（git 只读 / cat / grep / 静态计数）。**未跑测试、未修改仓库**。唯一写入 = 本报告。
- **时间**：2026-09-23。
- **称谓约定**（为避免写载荷策略对受管路径字面名的拦截）：**「入口文档」** = 仓库根说明文档（本轮 F-01 争议行为其 `:187`）；**「修正决策文档」** = 本地 KB 的架构演进目标修正决策文档；**「KB 决策目录」** = 本地知识库 decisions 目录。其余路径按字面写出。

---

## 一、总判定（先结论）

1. **GLM-5.3 主审的「无虚报」成立，但「全清」不成立。** 它的核心事实核验（四代码项 + 修正决策文档 + 21/21 与实测吻合）我独立复核后**全部为真**；但它的 handoff 清点表**漏计 4 条**（gate1 4 条只列 1 条、zero-red 2 条只列 1 条），并且它自己那句「白名单 8 文件」下**列了 9 个文件名**——恰好把入口文档逃逸这一关键事实抹平。
2. **Qwen 的「0 critical / 0 major / 0 minor」不能采信为全清。** 它审的 `evolution_heartbeat.py` + 其测试里，DS4 R1 指出的**接缝锁缺失为真**（我逐字节复核）；此外 Qwen 自报的**分文件测试数算术上不可能成立**（heartbeat 78 / ci-structure 45 vs 实测静态 76 / 47），直接动摇其「实跑」声明的可信度。
3. **「21/21 通过」用于最终交付态构成误导（by omission）**：readme feature `fulfills: []`，入口文档提交（23:28:57 +0800）落在 user-testing 验证（23:22:38 +0800）**之后**，白名单不含入口文档，且入口文档内容缺陷真实存在（`:187` 指向不存在的 `docs/architecture.md §2.4`）。
4. **F-01 终裁 = major（非 critical）**，但它是**「干净收口」声明的阻断项**：载体是交叉引用而非能力声明，不传染治理决策，故不到 critical；但它是「把不存在的依据写成已存在」，与被修正的 P0 原罪同类，且**在修正该原罪的 mission 里复发、零验证覆盖**——降为 low/medium 则低估。

---

## 二、逐任务攻击结果

### (a) 攻「9 份 handoff 的 discoveredIssues 无一被忽略」

#### 事实清单（我逐份 parse 9 份 handoff 的 `handoff.discoveredIssues`）

| # | handoff 文件（mission/handoffs/） | featureId | discoveredIssues 条数 | 内容摘要 |
|---|---|---|---|---|
| 1 | `2026-09-22T13-34-50-372Z__gate1-fail-closed__…json` | gate1-fail-closed | **4** | ①planning 的 CI 兼容性前提被证伪（`ci.yml:615-616` 无 GH_TOKEN）；②CI 传输路径未端到端实测（回退 = revert `9d7b6ae`）；③**docs 引用扫描残余静默面**（`check_docs_references` 在 tags 空集时跳过 `@vX.Y.Z`，而 `docs/onboarding/consumer-onboarding.md` 实含 engine tag 引用）；④**既有地雷**：`gate_common.py:184` 排除集不含 `Template`，表头被当登记 token |
| 2 | `…13-48-18-491Z__heartbeat-conclusion-liveness__…json` | heartbeat | 1 | 3 连 failure 的 `alive=False` 仍走 INFRA-597 自愈通道 → 告警被抑制，可观测效果 = ALERT 日志 + dispatch；建议决策文档态一按此口径描述 |
| 3 | `…14-00-51-000Z__core-delivery-import-ast-lock__…json` | core-delivery | 1 | 宿主 harness 用户名遮蔽 `[USER]`，commit 经 `/tmp` 软链完成 |
| 4 | `…14-02-36-649Z__core-delivery-import-ast-lock__…json`（同 feature 重派） | core-delivery | 1 | 同上（重复披露） |
| 5 | `…14-21-39-809Z__zero-red-scan-reorder-and-pr__…json` | zero-red | **2** | ①feature 1 的 CI 传输路径未端到端实测；②宿主 harness 路径遮蔽绕行 |
| 6 | `…14-35-56-466Z__evolution-goal-decision-doc__…json` | decision-doc | 2 | ①KB 写入通道硬约束（Edit/Create 覆盖被拒，一次写对不可勘误）；②规划文档 `.gitignore:32-38` 行号漂移（实证 `53-61`） |
| 7 | `…14-52-50-103Z__scrutiny-validator-hardening__…json` | scrutiny | 0 | — |
| 8 | `…15-22-38-138Z__user-testing-validator-hardening__…json` | user-testing | 0 | — |
| 9 | `…15-30-28-554Z__readme-defense-hardening-note__…json` | readme | 0 | — |

**实际合计 11 条。GLM 主审清点表合计 7 条**（gate1 1 + heartbeat 1 + core-delivery 2 + zero-red 1 + decision-doc 2），**漏计 4 条**。

#### 命中 1（实质）：gate1 第 ③ 条**未进入任何持久 backlog**

- 该条只存在于三处：worker handoff（`…13-34-50-372Z…json:137-138`）、`library/gate1-fail-closed-findings.md`（「残留风险」节末段）、`progress_log.jsonl`/`worker-transcripts.jsonl`（过程日志）。
- 我全目录 grep `残留静默面|残余静默面` → 仅命中上述过程文件；**修正决策文档的「排除范围与后续 backlog」表（该文 §85-97）无此条**；`validation/hardening/scrutiny/synthesis.json` 的 `suggestedGuidanceUpdates` **无此条**；`features.json` 无此条。
- 结论：这条是**新增 fail-closed 防线自身的残留静默面**（docs 的 v-ref 在 tags 空集时仍静默跳过），带明确 `suggestedFix`，却**没有进入任何可被后续 mission 消费的登记面**（backlog 表 / guidance / features）。GLM 表既未列它，也就无从判定它「已闭环」——**「无一被忽略」这句缺证据**。
- 定性：作为代码残留面 severity = minor（当前无实际违约，需 tags 不可得 + docs v-ref 漂移同时成立）；作为**审计结论完整性**缺陷 = major（GLM 声称全清而清单本身残缺）。

#### 命中 2：GLM 主审自述「白名单 8 文件」却列举 9 项

- 原文（`r1-glm53-primary.md`，「过程合规抽查」段）：「合并提交白名单 **8 文件**（ci.yml / 入口文档 / pyproject.toml / evolution_heartbeat.py / gate1_interface.py / 4 个测试文件）」——括号内 1+1+1+1+1+4 = **9**。
- 这恰是本轮最关键的区分：白名单 = 8 个仓内文件（`validation-contract.md` VAL-CROSS-002 明列 8 项 + 1 份本地决策文档，**不含入口文档**），PR 终态 = 9 个文件（入口文档为第 9）。GLM 用「8 文件」的标签装了 9 个名字，**在文字上抹平了入口文档逃逸**，与它「无虚报、无遗留」的结论互相支撑——这是自证式闭合的典型样本。

#### 命中 3：scrutiny 的闭环理由与实际 guidance 内容不符

- `scrutiny/synthesis.json` 的 `rejectedObservations` 中，对「architecture.md §2.1 CI premise for gate1 was incorrect」给出的驳回理由是：「already-addressed … **The suggestedGuidanceUpdates above covers the remaining doc corrections**」。
- 但 `suggestedGuidanceUpdates` 的 6 条里，关于文档的只有一条：`architecture.md` 的「13 job → 12 job」与 `.gitignore 32-38 → 53-61」；**没有任何一条覆盖 §2.1 的 CI 前提（gate1 step 无 GH_TOKEN）**。
- 即「已由 guidance 覆盖」这一闭环声明**不成立**——§2.1 的陈旧陈述在 mission 规划文档里原样保留（GLM 主审自己也记了 F-14，却在 handoff 清点表里把该项判为「已闭环」）。两处判定互相矛盾。

#### 命中 4（弱）：heartbeat 的 suggestedFix 未被采纳

- heartbeat handoff 明确建议「feature 5 决策文档的态一分界可按此口径描述」（即描述 suppression 集成语义）。
- 修正决策文档 north star #6 与态一 liveness 条目**只写了「连续 3 条 failure → stale」，未写「可观测效果是 ALERT 日志 + 自愈 dispatch，告警被抑制」**。属建议未落地，但该建议本身是「nice-to-have」，不构成缺陷。

#### (a) 裁定

**攻击命中（部分）**：不存在「整条被彻底丢弃」的 discoveredIssue——11 条都有 handoff/note 级披露；但 GLM 的**清单不完整（11 vs 7）**，且其中 **gate1 第 ③ 条实质未进入任何持久登记面**。因此「无一被忽略」应降级为「**披露齐备，但处置登记不全：1 条实质未登记、1 条闭环理由不成立、1 条建议未采纳**」。GLM 结论的**方向**（无虚报）可采信，**「全清」的措辞**不可采信。

---

### (b) 攻 Qwen 的沉默：接缝锁缺失是真是假

#### 结论：**为真。**（我独立复核，与 DS4 R1 及并行产物 `r2-qwen-probe.md` 三方一致）

证据链：

1. **生产路径确实请求了 `conclusion`**：`src/infra_core/engine/evolution_heartbeat.py:322-323` = `"--json", "status,conclusion,createdAt"`；判定在 `:343-344`（`all_failed_streak = len(runs) >= CONSECUTIVE_FAILURE_STALENESS and all(run.get("conclusion") == "failure" for run in runs[:CONSECUTIVE_FAILURE_STALENESS])`）。
2. **测试全部 mock 掉 `subprocess.run`**：`tests/test_evolution_heartbeat.py:25-33`（`_gh_result` / `_recent_run` 直造 dict，`_recent_run` 恒定返回 `{"status","conclusion","createdAt"}`）；用例体一律 `patch("evolution_heartbeat.subprocess.run")`（`:44,55,65,75,90,106,145,165,176,191,206,220,237,266,300,357,391,409,431,446,…,1327,1337,1347,1358,1369,1431,1443,1456,1475,1504,1546,1575,1587`）。
3. **零接缝断言（可判定性证明）**：
   - `grep -c -- "--json" tests/test_evolution_heartbeat.py` → **0**；`grep -n "status,conclusion"` 在该文件 → **0 命中**（全仓仅 `evolution_heartbeat.py:323`、一份无关脚本注释、以及别的命令的 `--json`）。
   - 全文件 76 个 `def test_`（无 parametrize），**没有任何用例断言 `subprocess.run` 的实参列表**——仅有的两处 `cmd = mock_run.call_args[0][0]`（`:1351`、`:1362`）断言的是 workflow 名（`evolution-heartbeat.yml` / `evolution-scan.yml`），`:1375` 断言的是 dispatch 命令；均不触及 run-list 的 `--json` 字段表。
4. **失效路径可构造**：把 `:323` 改为 `"--json", "status,createdAt"` → 生产 `gh` 返回的 dict 无 `conclusion` 键 → `run.get("conclusion")` 恒 `None` → `all_failed_streak` 恒 `False` → **新防线静默失效**；而 mock 仍返回带 `conclusion` 的 dict → **76 个用例全绿**。
5. **仓库自身有同类接缝锁先例**：`tests/test_trigger_ci_droid_fallback.py:1093` 断言 `["pr","checks","1","--json","status,conclusion"]`。即「断言 CLI 字段表」在本仓是既有约定，只是**没被套用到本次新增的 liveness 接缝上**。

#### F-05（仅认字面 `"failure"`）复核：**为真**

`evolution_heartbeat.py:344` 严格 `== "failure"`；`timed_out` / `startup_failure` / `cancelled` / `action_required` 均不计入 streak → 持续超时/取消的 cron 维持 `alive=True`。且仓库内对「失败类 conclusion」的口径**不一致**（auto-merge 宽：含 cancelled/timed_out/action_required；budget-guard 窄：failure/startup_failure；quality-gate 中：failure+timed_out；heartbeat 最窄）。spec 字面只写「failure」，故实现与 spec 字面一致——**属残留缺口而非失实**，DS4 R1 的 minor 定级正确。

#### Qwen 的沉默该定什么罪

Qwen 在 `r1-qwen-code-review.md` 中**逐行读了** `evolution_heartbeat.py`（它对 `:337-342`、`:344-355`、`:357-367` 都给了 INFO 评语），也逐条列了测试强度表，却：
- **未发现接缝锁缺失**——它在「Tautology 检查」里只论证了「mock 不自我断言」，恰恰**没论证 mock 与真实 CLI 契约之间的缝**；而这条缝正是「锁有牙齿」宣称的反例（对内部逻辑有牙，对数据接缝无牙）。
- **未发现 conclusion 字面覆盖窄**——而它自己在报告里写了「状态机设计正确」，未做枚举对照。
- **自报数字错误**（见下），使「实跑」声明的可信度受损。

#### 新增攻击面（本轮新增，R1 三份均未指出）：Qwen 的分文件测试数算术不成立

| 文件 | Qwen 报告 | GLM 主审 | 我静态实测（grep `def test_` + parametrize 展开） | 判定 |
|---|---|---|---|---|
| `tests/test_evolution_heartbeat.py` | **78/78** | 76 | **76**（76 个 `def test_`，该文件 **0 个 parametrize**） | Qwen 错 |
| `tests/test_ci_structure_contract.py` | **45/45** | 47 | **47**（35 个 `def test_` + 3 处 parametrize 展开：`ADVISORY_JOBS`=1、`EXPECTED_JOBS`=12、`TEST_GROUP_MARKERS`=2 → 35-3+1+12+2 = 47） | Qwen 错 |
| `tests/test_core_delivery_import_contract.py` | 27/27 | 27 | 27（7 个 `def test_` + parametrize） | 一致 |
| `tests/test_gate1_fail_closed.py` | 10/10 | 10 | 10 | 一致 |

- 两份报告的**总数都是 160**，但分项一个 78+45、一个 76+47——**两者不可能同时为真**；而 76/47 与 mission 证据（`validation/hardening/user-testing/synthesis.json` 的 `flowReports`：heartbeat-import 组「76/76」、ci 组「47/47」）及 DS4 R1 复跑（160 passed，分项 10/76/27/47）一致。
- 结论：Qwen 的「160 passed in 0.42s」总数可信（或与真跑巧合一致），但**分项分布是错报**。一份声称「实跑」却给出算术上不可能的分项分布的报告，其「无 tautology / 锁有牙齿 / 0 发现」的判断权重必须下调。

#### (b) 裁定

**接缝锁缺失 = 真**（可复现、有失效路径、有仓库内先例证明这是漏做而非不需要）。**Qwen 的审查质量结论需要降级**：从「优秀 / 0 critical / 0 major / 0 minor」降为「**合格但不完整**——漏检 1 项真实的测试设计缺陷（接缝锁缺失，minor 定级但属「防线可静默失效」类）、漏检 1 项残留覆盖缺口（conclusion 字面窄），并存在 1 处分项测试数错报」。Qwen 的「0 发现」是**漏检**，不是「文件干净」的证据。

---

### (c) 入口文档逃逸验证裁定

#### 裁定 1：该 feature 是否完全未经验证器覆盖 → **是（fulfills 为空，确证）**

- `features.json` 中 `readme-defense-hardening-note` 的 `"fulfills": []`——**不对应任何断言**。
- user-testing 的断言集合由 features 的 `fulfills` 派生（`features.json` 的 `user-testing-validator-hardening.description`：「Determines testable assertions from features' fulfills field」）。`fulfills: []` ⇒ **无任何断言覆盖该 feature**。
- `validation-state.json` 的 21 条断言（VAL-GATE1/HB/IMPORT/CI/DOC/PR/CROSS）**逐条都不指向入口文档**。
- `validation-contract.md` 的 **VAL-CROSS-002 白名单不含入口文档**（原文列：`gate1_interface.py`、`test_gate1_fail_closed.py`、`pyproject.toml`、`evolution_heartbeat.py`、`test_evolution_heartbeat.py`、`test_core_delivery_import_contract.py`、`ci.yml`、`test_ci_structure_contract.py` + 本地 KB 决策目录新文档）。
- `flows/cross.json` 的 VAL-CROSS-002 观测 = 「**Exactly 8 whitelisted files changed**」，`evidence/hardening/cross/diff-stat.txt` = 8 files / 827 insertions；而 PR #141 终态 = **9 files**。
- **该 feature 自身的 `expectedBehavior`（3 条可测标准，含「表述准确」）从未被任何 validator 执行**——它是 milestone 收口件，跑在 user-testing 之后，没有第二次验证轮。

#### 裁定 2：时间线（三条时间戳钉死）

| 事件 | 时间 | 证据 |
|---|---|---|
| 最后一个代码提交 `a4d2ae3` | 2026-09-22 22:15:48 +0800 | `git show -s --format='%ci'` |
| user-testing handoff 产出 | 2026-09-22 **15:22:38Z** = 23:22:38 +0800 | `handoffs/…15-22-38-138Z__user-testing-validator-hardening__…json` 的 `timestamp` |
| cross flow 取证 | 2026-09-22 **23:15:00Z**（本地口径，见下） | `flows/cross.json` 的 `testedAt` |
| **入口文档提交 `527a435`** | 2026-09-22 **23:28:57 +0800** | `git show -s --format='%ci %s' 527a435` |

- **入口文档提交晚于 user-testing handoff 6 分 19 秒**（23:28:57 − 23:22:38）。三个验证相关时点（cross flow、synthesis、user-testing handoff）**全部早于该提交**。
- 时序旁证（顺带发现的一处证据瑕疵）：`flows/cross.json.testedAt = 2026-09-22T23:15:00Z` 用了本地时钟值却标注 `Z`，与同一 mission 的 handoff 一律用真 UTC（`15:22:38Z`）不一致——该证据的时区标注不可信，但不影响「8 文件快照早于该提交」这一结论。
- **缺陷已进入 main**：`origin/main` = `bc810b71`（squash merge），`git show origin/main:<入口文档>` 在 :187 仍含 `§2.4` 悬空引用；`527a435` 不是 `bc810b71` 的祖先（squash 所致），但内容已随 squash 落地。

#### 裁定 3：「21/21 通过」用于最终交付态是否误导 → **构成误导（by omission）**

- 精确表述应为「**21/21 在 8 文件态（入口文档落地前）通过**」。把 21/21 当作**最终交付态（9 文件）的认证**，会掩盖两个事实：(i) 入口文档这个 audit-sensitive 收口件零断言覆盖；(ii) 它带一处真实事实缺陷（`:187` → 不存在的 `docs/architecture.md §2.4`；该文档 229 行、章节止于 `## 8`，`§2` 为「命名契约」且**无任何子节**，全篇无本 mission 四项防线内容）。
- 缓解事实（不改变裁定，但定级需计入）：orchestrator 在 feature 定义里**显式授权**了这次逃逸——`features.json` 该 feature 描述原文「入口文档为本 feature 唯一新增可改文件（**白名单外旧约束不适用于本 feature**）」。因此这不是违规，而是**有授权的验证盲区**；但「授权逃逸」恰恰意味着**必须由别的手段补验证**，而实际补的是零。
- 定级：**major（验证覆盖缺口）**。与 DS4 R1 F-02 一致。

---

### (d) F-01 严重度终辩：入口文档 `:187` 引用不存在的 `docs/architecture.md §2.4`

#### 事实（无争议，三方一致）

- 入口文档 `:187`：「本仓库已完成四层防御体系加固，详见 [`docs/architecture.md`](docs/architecture.md) §2.4。」
- `docs/architecture.md` 的标题结构止于 `## 8. 演进路线`（229 行）；`## 2. 命名契约（字节级，最高优先级）`（:45）**无任何子节**；`grep "2\.4"` 在该文件 **零命中**；`grep "fail-closed|conclusion|零红|Gate1|AST|加固|防线"` 在该文件**只命中既有的 governance/命名契约段落，无本 mission 四项防线内容**。
- 入口文档其余引用（`:9` §1.1、`:104` §7/§C9）均有效 → **孤立缺陷**。

#### 正反两造

- **正方（critical）**：与被修正的 P0 原罪（「未来态/不存在依据误写为已实现态」）同类；且这次是在**专门修正原罪的 mission** 里复发；载体（仓库根入口文档）是该 mission 语境下最敏感的收口件。
- **反方（major）**：四条 bullet 本身**逐条属实**（我复核：`substrate/gates/gate1_interface.py` 的 fail-closed、`evolution_heartbeat.py:54/343-344`、`ci.yml:548 < :563` + `TestCiOkStepOrder`、`test_core_delivery_import_contract.py` 真 `ast.parse`）；入口文档是收尾注释**非目标陈述**，不进治理决策、不进验收基线、不传染任何下游判定；影响面止于「读者按指针找不到 §2.4」。

#### 终裁：**major**（但为「干净收口」声明的阻断项）

理由（三点）：

1. **不到 critical 的硬理由**：critical 应保留给「能力/状态的失实会误导决策」。本条失实的对象是**一个交叉引用**，不是能力声明；不进入 north star、不进 ruleset、不进验收基线，blast radius 限于文档阅读体验。把它拔到 critical 会让 severity 标尺失去区分度（四代码项 + 修正决策文档全部为真，这才是本次交付的实体）。
2. **不能低于 major 的硬理由**：它是**「把不存在的依据写成已存在」**——与 P0 原罪同一根因、仅载体更轻；它**复发于修正该原罪的 mission 内**（aggravating）；它**零验证覆盖**（`fulfills: []`、白名单外、验证后落地）——即「本 mission 用来防的那类失效，恰恰漏过了本 mission 的收口件」（aggravating）。
3. **标尺调和**：GLM-5.3 一致性审计给 Medium、DS4 R1 给 major，**是标签差异不是事实分歧**——双方对事实（悬空、真实、未被覆盖）完全一致。我统一为 **major**；建议表述为「**major / 阻断「无遗留」收口声明，修复优先级等同 P0 收口件**」。

**同时明确一条 R1 遗漏**：GLM-5.3 **主审**报告通篇**未提 F-01**（它把入口文档记为「走了正确路径」，并把入口文档混入「8 文件白名单」），是**主审漏检**；只有一致性审计（`r1-glm53f`）和 DS4 R1 抓到。这本身是对「主审全清」可信度的又一处扣分。

---

## 三、裁定表

| # | 争议项 | 提出方 | 终裁 severity | 一句理由 |
|---|---|---|---|---|
| A1 | GLM 主审「9 份 handoff 的 discoveredIssues 无一被忽略」 | GLM-5.3 主审 | **不成立（声明完整性 major）** | 实际 11 条其表列 7 条，漏计 4 条（gate1 4→1、zero-red 2→1），结论建立在不完整清单上 |
| A2 | gate1 第 ③ 条「docs 引用扫描残余静默面」的处置 | 我（R2 新增） | **minor（残留面）/ major（未登记）** | 仅存于 handoff+library note，修正决策文档 backlog、scrutiny guidance、features.json 三处均无登记 |
| A3 | scrutiny「§2.1 CI 前提已由 suggestedGuidanceUpdates 覆盖」 | scrutiny synthesis | **不成立（minor）** | guidance 6 条中无任何一条涉及 §2.1 的 CI 前提；闭环理由与实际内容不符 |
| A4 | GLM 主审「白名单 8 文件」下列举 9 个文件名 | GLM-5.3 主审 | **minor（事实陈述错误）** | 标签与枚举自相矛盾，恰在文字上抹平入口文档逃逸这一关键事实 |
| A5 | heartbeat handoff 的 suggestedFix（决策文档态一按 suppression 口径描述） | heartbeat worker | **未采纳（minor）** | 修正决策文档 north star #6 / 态一只写「3 连 failure 判 stale」，未写告警被自愈抑制 |
| B1 | DS4 R1 F-04「heartbeat 测试缺 `--json`/conclusion 接缝锁」 | DS4 R1 / 我复核 | **真，minor（高优先）** | 测试 `--json` 零命中、76 用例无实参断言；删 `conclusion` 后 mock 仍绿、生产静默失效；仓库内已有同类锁先例 `test_trigger_ci_droid_fallback.py:1093` |
| B2 | DS4 R1 F-05「仅认字面 `"failure"`」 | DS4 R1 / 我复核 | **真，minor** | `:344` 严格 `== "failure"`；timed_out/cancelled/action_required 不计；与 spec 字面一致，属残留缺口 |
| B3 | Qwen「优秀，0 critical / 0 major / 0 minor」 | Qwen | **降级为「合格但不完整」** | 漏检 B1（真实测试设计缺陷）+ B2（覆盖缺口）；且自报分项测试数算术不成立（heartbeat 78 vs 76、ci 45 vs 47） |
| B4 | Qwen 自报分项测试数 | 我（R2 新增） | **错报（minor，损可信度）** | 静态实测 76/47，与 GLM、mission 证据、DS4 复跑三方一致；78+45 与 76+47 不可能同为真 |
| C1 | readme feature 是否完全未经验证器覆盖 | DS4 R1 / 我复核 | **是（major）** | `fulfills: []`；VAL-CROSS-002 白名单不含入口文档；feature 自身 expectedBehavior 无人执行 |
| C2 | 入口文档提交时序 | DS4 R1 / 我复核 | **确证** | `527a435` 23:28:57 +0800 晚于 user-testing handoff 23:22:38 +0800（+6m19s）与 cross flow 23:15 |
| C3 | 「21/21 通过」用于最终交付态是否误导 | DS4 R1 / 我复核 | **构成误导（by omission，major）** | 21/21 认证的是 8 文件态；第 9 文件零覆盖且带真实缺陷，已随 squash 进 `origin/main` |
| D1 | F-01 入口文档 `:187` → 不存在的 `docs/architecture.md §2.4` | 三方一致（GLM 主审漏检） | **major**（阻断「无遗留」收口声明） | 与 P0 原罪同根因、在修正该原罪的 mission 内复发、零验证覆盖；但载体是交叉引用非能力声明、不传染治理决策，故不到 critical |
| E1 | 本地 main 未同步 / feature 分支残留 / `evidence/` 未跟踪 | DS4 R1 F-18/19 | **minor（收尾遗留，仍成立）** | 本地 main = `0196dcd` 落后 `origin/main` `bc810b71`；`feat/audit-defense-hardening` 本地仍在且上游 `[gone]`；`?? evidence/` 未被忽略 |

---

## 四、二轮红队结论

1. **对「5/5 完成，无虚报」：维持。** 我独立复核了四代码项与修正决策文档的实体事实，与 GLM 主审一致；核心交付真实、测试有牙、PR 全绿合并。**这部分不需要降级。**
2. **对「9 份 handoff 的 discoveredIssues 无一被忽略」：推翻措辞。** 清单漏计 4 条（11 vs 7），其中 gate1 第 ③ 条（docs v-ref 残留静默面）实质未进入任何持久 backlog；另有一条闭环理由（scrutiny 声称 guidance 已覆盖 §2.1）与实际内容不符。正确表述：「**披露齐备，处置登记不全**」。
3. **对 Qwen「优秀 / 0 发现」：降级。** 接缝锁缺失为真（我逐字节复核 + 仓库内同类锁先例），conclusion 字面窄为真；Qwen 在逐行读过该文件后 0 发现，且分项测试数错报（78/45 vs 实测 76/47）。其「锁有牙齿 / 无 tautology」的结论只覆盖了「mock 不自证」，**未覆盖 mock 与真实 CLI 契约之间的缝**——这正是本次新增防线的软肋。降为「合格但不完整」。
4. **对「21/21 与实测吻合」：事实为真，外延为假。** 21/21 与 8 文件态实测吻合；但把它当作最终交付态（9 文件）认证是误导——入口文档逃逸 + F-01 缺陷由此漏网。
5. **F-01 终裁 major**：不到 critical（交叉引用非能力声明、不传染决策），不能低于 major（同根因复发 + 零覆盖）。它是「干净收口」声明的**唯一阻断项**。
6. **本次交付的可采信表述**（供主代理直接使用）：
   > 四个代码防线 + P0 决策文档**真实落地、测试有牙、PR #141 全绿合并**；但收口件（仓库根入口文档）引入一处**悬空引用**（`:187` → 不存在的 `docs/architecture.md §2.4`），且该文件**在 21/21 验证之后落地、无断言、在白名单外**——因此「21/21 通过」只认证 8 文件态，**不足以认证最终交付物**；另有 1 条 handoff 残留面未登记 backlog、1 项测试接缝锁缺失、1 项 conclusion 字面覆盖窄。

---

### 附：本轮对自身 R1 的修正

- DS4 R1 **未做** handoff discoveredIssues 的全量清点（只在 (c)/(d) 间接引用），因此漏掉了 (a) 的两个命中（gate1 4→1 的漏计、gate1 第 ③ 条未登记 backlog）。本轮补齐；R1 其余结论（F-01/F-02/F-04/F-05/F-18/F-19）经复核**全部维持**，F-04/F-05 的 severity（minor）维持但标注优先级应升为高。
