# R2 终审裁决报告（GLM-5.3，第二轮独立重验）

**裁决员**：GLM-5.3 终审裁决员（r2）
**日期**：2026-09-23
**方法**：对 D1–D7 全部独立取证（git/grep/sed/gh 只读，不信 R1 报告文字、只信 file:line），再对照 Round 1 四份报告定分歧、下终裁。
**输入**：R1 四报告（r1-ds4-redteam / r1-glm53-primary / r1-glm53f-consistency / r1-qwen-code-review）+ spec 终稿（mission 7eedcdc4 reports/goal-adversarial-audit-final.md）+ 决策文档现文。
**被审**：mission `3dfa75a1`（PR #141，MERGED 2026-09-22T15:34:31Z，merge commit `bc810b7`）。

---

## 争议项终裁表

### D1 — README.md:187 悬空引用 §2.4 +「四层防御体系」措辞

**独立证据（本裁决员亲手取）**：
- `README.md:187`：「本仓库已完成四层防御体系加固，详见 [`docs/architecture.md`](docs/architecture.md) §2.4。」
- `docs/architecture.md` 全部标题（grep `^#`）：`## 1`（1.1/1.2）/ `## 2 命名契约`（**无任何 ### 子节**）/ `## 3`–`## 8` —— **不存在 §2.4**；`grep -n "2\.4" docs/architecture.md` 零命中（exit=1）。
- `docs/architecture.md` 全文 grep `fail-closed|conclusion|零红|Gate1|AST|防线`：仅命中既有 governance/门禁矩阵段落（:14、:20、:70、:89-93 等），**无本 mission 四项防线的任何描述**——被指向的文档既无该节、也无该内容。
- 同节四条 bullet 本身逐条属实（见本报告 spec 终表，均为本裁决员第一手复核）。

**R1 分歧**：DS4 major（建议按 critical 优先级修）；GLM-5.3F Medium+Low；Qwen 无发现（其审查范围是代码 diff，未审文档引用有效性——范围差异，非矛盾）。

**终裁**：
- 悬空引用：**major**。定性 = 在 P0 语境（「不得把不存在的写成已存在」）下最敏感的收口件里写入不可达的权威指向；与 P0 原罪同类根因、较轻载体（bullet 内容属实，仅指向悬空），故不到 critical。
- 「四层防御体系」措辞：**info**。与 v3「四层体系」（分层架构术语）撞车，实指四项防线；按 bullet 读不构成事实错误，属措辞精度问题。

**处置**：立即修（一行改动级）。改指向 git 内真实可达位置（自含描述 / architecture.md §5 门禁矩阵 / 新增小节），同 PR 顺带把「四层防御体系加固」限定为「四项审计防线加固」。注意决策文档在本地 KB 不入 git，README 不能指向它。

---

### D2 — README（第 9 文件）在验证之后落地、白名单外、fulfills 空

**独立证据**：
- `features.json` readme feature：`"fulfills": []`（第一手）；description 明文「README.md 为本 feature 唯一新增可改文件（白名单外旧约束不适用于本 feature 的 README.md）」；expectedBehavior 含「表述准确」与「改动仅 README.md 一个文件」。
- `validation-contract.md` VAL-CROSS-002 白名单：8 个受控 git 文件 + 本地决策文档，**不含 README.md**；grep readme 在 validation-contract.md 零命中。
- `evidence/hardening/cross/diff-stat.txt`：8 files / 827 insertions（第一手）；PR #141 实际 9 files（gh pr view 第一手，含 README.md）。
- 时序（第一手 git log）：readme commit `527a435` = 2026-09-22 23:28:57 +0800（=15:28Z），晚于 user-testing handoff（15:22:38Z）与全部验证证据 mtime（22:59–23:00 +0800）。

**R1 分歧**：DS4 major（验证覆盖缺口）；主审未覆盖（其范围限 5 个 spec feature）；GLM-5.3F 未单列此项定级（在一致性结论中认可时点演进）。

**终裁**：**major（验证覆盖缺口，非代码缺陷）**。两点修正性认定：(a) README 变更并非漂移——orchestrator 在 feature 规格中**预先授权**了白名单豁免，属有意的 end-of-mission 收口 feature；(b) 但 fulfills=[] 意味着零断言覆盖，且经验事实是：全 mission 唯一被实证的缺陷（D1）恰好落在这一无断言的收口件上——因果链完整证明「21/21 断言通过」认证的是 8 文件状态而非最终交付态。

**处置**：follow-up（流程改进：收口类文档 feature 至少配一条 VAL-DOC 类断言，或白名单扩展须同步 validation-contract 增补）。不阻塞已合并交付。**未登记决策文档 backlog → 建议登记。**

---

### D3 — heartbeat 测试无 `--json`/conclusion 字段接缝锁

**独立证据**：
- `tests/test_evolution_heartbeat.py` grep `--json`：**零命中**（conclusion 仅出现于 :30-33 测试辅助 dict 构造与 :124-204 行为用例）。
- 源 `src/infra_core/engine/evolution_heartbeat.py:318-328`：真实调用 `gh run list --limit 5 --json status,conclusion,createdAt`；`:343-345` 据 `run.get("conclusion")` 算 streak。
- 失效路径成立：若 `--json` 列表删去 `conclusion`，`run.get("conclusion")` 恒 None → streak 恒 False → 防线静默失效，而全部测试（patch subprocess、直造 dict）保持绿。

**R1 分歧**：DS4 minor；Qwen 无发现。

**终裁**：**minor**。真实数据路径已接上（非仅 mock），行为逻辑锁充分；缺的是「数据接缝」一条断言——恰是本 mission 要防的「防线无声空转」失效类在新增防线自身的复现，但触发条件（有人改 `--json` 字段列表）较窄。

**处置**：follow-up（低成本测试增强：新增断言 gh 调用实参含 `conclusion` 字段）。**未登记决策文档 backlog → 建议登记。**

---

### D4 — `evolution_heartbeat.py:344` 仅认 "failure" 字面值

**独立证据**：
- `:343-345`（第一手）：`run.get("conclusion") == "failure"`，timed_out / startup_failure / cancelled 不计入 streak。
- 批准范围原文（第一手）：spec 终稿整改表 P1 行「heartbeat liveness 补 conclusion 判定（**连续 failure** 视为 stale）」；决策文档 Authority Refs 用户裁定「liveness 连续 3 次 failure 判 stale」；README :191 与决策文档修正 #8 均按 failure 字面表述。

**R1 分歧**：DS4 minor（残留缺口）；其余未提。

**终裁**：**符合批准范围，无问题**（实现 = spec = 用户裁定 = 四处文档口径四方一致，不构成失实或欠交付）。timed_out/cancelled/startup_failure 持续时不判 stale 属防线**完备性**残留缺口，定级 **info**。

**处置**：follow-up（完备性增强，是否值得做由用户裁定——需权衡 GitHub conclusion 枚举与误报面）。**未登记决策文档 backlog → 建议登记（标注：符合批准范围，作为可选增强）。**

---

### D5 — VAL-CI-003-actionlint-output.txt 0 字节

**独立证据**：`ls -la` 第一手 = 0 字节（2026-09-22 22:59）；`/opt/homebrew/bin/actionlint` 在位；对照同目录 `grep-local-one.txt` 有「Exit code: 1」标记惯例。

**R1 无分歧**（DS4+GLM-5.3F 均 minor/low）。

**终裁**：**minor**（证据完整性缺陷，非结论缺陷）。actionlint 通过时零输出属正常，但无 exit code 标记则无法与「未捕获/未跑」区分；缓解事实：ci.yml 被 GitHub 成功解析执行（PR #141 全绿）本身证明 workflow 语法有效。

**处置**：立即修（近零成本，本地工件）：补跑 `actionlint -color` 并捕获 `Exit code: 0` 标记写入该文件。

---

### D6 — 本地 git 收尾（main 落后 / feature 分支未删 / evidence/ untracked）

**独立证据**（第一手）：
- 本地 `main` = `0196dcd`，`origin/main` = `bc810b7`（落后 1 个 merge commit）。
- 当前 checked-out 仍为 `feat/audit-defense-hardening`，上游 `[gone]`（远端分支已删，`git branch -a` 实证）。
- `evidence/` untracked（`git status` = `?? evidence/`），`git check-ignore evidence/` exit 1（**未被 .gitignore 覆盖**），mission/决策文档均无处置声明。

**R1 无分歧**（均 minor/low）。

**终裁**：**minor**。归因是时序：mission state 结束于 15:30:28Z，早于合并（15:34:31Z），「合并后三件事」本地半边无人接手（readme feature 规格只在路径 b 才触发三件事，实际走了路径 a）。属可解释的收尾遗留，非虚报，但当前工作树不处于同步态。

**处置**：立即修（机械操作，全程可逆）：本地 main fast-forward 至 bc810b7 → 删本地 feature 分支（PR 已 MERGED 且远端已删，删除安全）→ evidence/ 处置声明（提交为 artifacts / 加入 .gitignore / 归档，三选一需规则裁定；操作前备份）。

---

### D7 — 小项打包裁定

| 项 | 独立证据 | 终裁 | 处置 |
|---|---|---|---|
| D7a 决策文档:135 元数据时滞 | `:135`「HEAD `a4d2ae3`…8 个白名单文件」（第一手）；PR 实际 head `527a435`、9 files（gh 第一手）；a4d2ae3 时点 diff-stat 确为 8 files/827（第一手） | **info**。快照时点真实；KB read-first-CRUD（overwrite_allowed=False）只增不改是既定策略，时滞受策略保护 | 仅记录；下次触碰该文档时以增补节回填，不改写原文 |
| D7b TestCiOkStepOrder docstring「13 job」 | `tests/test_ci_structure_contract.py:477`「13 job 集合」（第一手）；`EXPECTED_JOBS` 实数 **12**（逐项清点第一手） | **info**。注释级、无功能影响；但它在**已合并进 main 的仓内文件**里（区别于 mission 内部文档），且所在测试恰是契约测试，docstring 精度有意义 | 立即修（一行，与 D1 同一 docs PR 顺带） |
| D7c mission architecture.md 残留「13 job」 | mission.md:28 与 architecture.md:102 **均已更正为 12 且带更正注记**（第一手）；「13 job」残留仅在 worker-transcripts.jsonl 与 handoff JSON（不可变历史记录） | **无问题**。R1 表述「残留」不成立——planning 文档已更正，历史 transcript 保留旧值属正确行为 | 无 |
| D7c 附注 mission architecture.md:31 残留 `.gitignore:32-38` | architecture.md:31 仍写 32-38，同文件 :148 与 mission.md:19 已更正为 53-61（第一手；实际 `.gitignore:53-61` 实测吻合） | **info**。mission 内部文档、单处残留、同文档他处已更正 | 仅记录（mission 文档非仓内交付物） |

---

## spec 整改清单逐项终表

对照 spec 终稿 §6/§7 整改基线（P0 决策文档、P1 Gate1 fail-closed、P1 liveness conclusion、P2 零红移序、P2 AST 锁；排除项 B1 / watchdog / F8 schedule / P3 / B5）。全部为本裁决员第一手证据：

| # | spec 项 | 终判 | 证据（第一手） |
|---|---------|------|----------------|
| P0 | 决策文档沉淀 | **已完成**（含已留痕的合理偏差：本地 KB 而非 PR 提交——用户 2026-09-22 裁定 + `.gitignore:53-61` 实证结构上不可能走 PR，决策文档附加修正 B 显式记录） | `memory/kb/decisions/architecture-evolution-goal-correction.md` 在位：修正版目标 10 条、8 处修正逐条带 file:line、三态分界（四项标【本 mission 新增】）、Truth Basis 四节、附加修正 A/B |
| P1 | Gate1 fail-closed | **已完成** | `substrate/gates/gate1_interface.py:306` `fail_closed = (not templates) or tags_unavailable`、`:327` 显式 FAIL 分支；全文件 `LOCAL-ONE` 零命中（第一手 grep） |
| P1 | heartbeat liveness conclusion | **已完成** | `evolution_heartbeat.py:54` `CONSECUTIVE_FAILURE_STALENESS = 3`、`:343-345` streak 判定（第一手）；范围恰为批准口径（见 D4） |
| P2 | 零红聚合移序 | **已完成** | `.github/workflows/ci.yml:548`（Check droid-review status）< `:563`（Zero-red aggregation）（第一手） |
| P2 | Core↛Delivery AST 锁 | **已完成** | `tests/test_core_delivery_import_contract.py` 在位（第一手 ls，7 个 test 函数经参数化构成 R1 双方一致的 27 用例；R1 主审/红队对「真 ast.parse 非字符串匹配」结论一致） |
| 排除 | B1 governance 入链 | **明确排除（留后续）** | 决策文档「排除范围与后续 backlog」表在列（P1）；PR 9 文件清单（gh 第一手）零触碰 governance workflow |
| 排除 | watchdog 触发器恢复 | **明确排除（留后续）** | backlog 表在列（P1）；PR 零触碰 droid-review-watchdog |
| 排除 | F8 改 schedule | **明确排除（留后续）** | backlog 表在列（P2）；PR 零触碰 |
| 排除 | P3 两项（quality-gate 分页 / .evolution 持久化） | **明确排除（留后续）** | backlog 表在列（P3） |
| 排除 | B5 Dependabot 互斥 | **明确排除（留后续）** | backlog 表在列（产品决策） |

---

## mission 完成度终判

**带条件完成。**

可引用结论：**「mission 的 spec 范围内交付（P0 决策文档 + 四个零成本防线项）全部真实落地、经测试与 CI 验证并以 PR #141 全绿合并，排除项零触碰、无虚报；但最终交付态含一处 major 文档缺陷（README.md:187 指向不存在的 architecture.md §2.4）与一项 major 验证覆盖缺口（收口件在 21/21 验证之后落地且零断言覆盖，该缺陷因此漏网），完成声明须以本报告『立即修』桶闭环、『follow-up』桶登记后方为干净完成。」**

（对 R1 分歧的裁定：DS4「部分完成」过重——全部 spec 范围工作确已落地，缺陷在收口件而非交付主体；主审「5/5 通过无遗留缺陷」过宽——其范围限 5 个 spec feature，未覆盖 readme 收口 feature 的实际质量。终判取中：带条件完成。）

---

## 处置建议（两桶）

### 立即修（近零成本；1–2 可合并为一个小 docs PR，3–4 为本地操作）

1. **README.md:187 悬空引用**（D1，major）：改指向 git 内真实可达位置或改自含描述；顺带「四层防御体系加固」→「四项审计防线加固」（D1b, info）。注意不可指向本地 KB 决策文档（不入 git）。
2. **TestCiOkStepOrder docstring 13→12**（D7b，info）：同一 docs PR 一行顺带。
3. **actionlint 证据补 exit code 标记**（D5，minor）：本地 evidence 工件，一条命令补捕获。
4. **本地 git 收尾**（D6，minor）：main fast-forward → 删本地 feature 分支（安全：PR 已 MERGED、远端已删）→ evidence/ 处置声明（操作前备份，处置方式需规则裁定）。

### follow-up（需新 mission 或用户裁定；均未登记决策文档 backlog，建议下次 KB 增补时入表）

| # | 项 | 定级 | backlog 登记状态 |
|---|---|------|------------------|
| 1 | 收口类文档 feature 的验证覆盖机制（VAL-DOC 断言或白名单同步增补） | major（流程面） | 未登记 → 建议登记 |
| 2 | heartbeat `--json`/conclusion 接缝锁测试 | minor | 未登记 → 建议登记 |
| 3 | conclusion 字面值完备性（timed_out/startup_failure/cancelled 计入与否） | info（符合批准范围的可选增强） | 未登记 → 建议登记（标注：非缺陷，完备性选项） |
| 4 | 决策文档:135 元数据回填 | info | 策略性延后（read-first-CRUD 只增不改，下次触碰时增补） |
| 5 | mission architecture.md:31 残留 `.gitignore:32-38` | info | 仅记录（mission 内部文档，同文档 :148 已更正） |

（决策文档既有 backlog 表已登记的是 spec 排除项 B1/watchdog/P3×2/B5/F8/消费仓接回，与本表 1–3 无重叠。）
