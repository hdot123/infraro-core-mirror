# 2026-09-23 多模型交叉对抗审计·终审综合（R3 收敛版）

> **版本说明**：本版为 R6 定稿（Fable 终审 APPROVE WITH CONDITIONS 的三处修订已落实，冻结版）。审计链全貌：R1 四路独立 → R2 交叉对抗 → 质检修正 → Fable 5.1 终局第二意见 → R3（DS4 红队 + GLM-5.3 复核）双确认收敛 → R4（GLM-5.3 终稿质检）→ R5（Grok 4.7 链外红队）→ R6（Fable 5.1 终审复裁：APPROVE WITH CONDITIONS）。A5→I2/F5 升级经三方独立代码级推演 + 一个链外复核一致确认；正文与全部索引行已对齐（一稿两裁缺陷在 R3/R4/R5/R6 逐轮清理）。

- **被审对象**：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（架构演进目标修正沉淀 + 审计防线加固；交付 PR #141 `feat/audit-defense-hardening`，MERGED 2026-09-22T15:34:31Z，squash merge commit `bc810b7` ∈ origin/main）
- **spec 基准**：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`（三轮对抗终稿：P0/P1/P2 整改清单 + 三态现状分界 + 排除项）
- **审计结构**：DS4 / GLM-5.3 / GLM-5.3-Flash / Qwen 四模型两轮 → orchestrator 终裁 → 质检 → Fable 5.1 终局第二意见 → R3 双路交叉复核 → R4 终稿对抗质检 → R5 Grok 4.7 链外红队 → R6 Fable 5.1 终审复裁（6 个模型、14 轮次独立参与；含两个链外视角 Fable/Grok）
- **本报告性质**：orchestrator 终裁结论的忠实承载与汇总，不新增裁定；doc-worker 留档 2026-09-23，R3 收敛修订同日
- **配套登记（待补）**：follow-up 项的追加写入被 memory_kb 域平台守卫拦截（worker 与 orchestrator 两层通道均拒，preserve-and-escalate），决策文档 backlog **未**完成登记；补写内容见 §六末「待补登记块」（R3 版：A2/D3/D4/D2/F5/F6/T3 七项 + I2 同场裁定）

---

## 〇、子报告索引（截至 R5 共 13 份，同目录 `~/infraro-core/evidence/hardening/adversarial/`；Fable 终裁复裁报告随后追加为第 14 份）

| 轮次 | 报告 | 角色 | 一句话结论 |
|---|---|---|---|
| R1 | `r1-ds4-redteam.md` | DS4.1-Flash 红队（攻击「已完成」声明） | 部分完成（非整体虚报）：四代码项 + 决策文档真实落地；收口件两处须认领（F-01 悬空引用 / F-02 验证覆盖缺口） |
| R1 | `r1-glm53-primary.md` | GLM-5.3 主审（5 feature 严格核验） | 5/5 完成无虚报（160 用例实跑全绿、file:line 逐条对上） |
| R1 | `r1-glm53f-consistency.md` | GLM-5.3-Flash 事实一致性核验 | 总体一致（PASS with findings）：1 Medium（§2.4 悬空）+ 4 Low + 2 Info；7/19 证据抽查 |
| R1 | `r1-qwen-code-review.md` | Qwen(bailian) 代码审查 | 优秀，0 critical/major/minor + 4 Info（R2 后降级，见 §四） |
| R1 | `review-summary.md` | Qwen R1 摘要件 | 同上（+1 份） |
| R2 | `r2-ds4-attack.md` | DS4 二轮（攻击 R1 三份「全清」结论） | GLM 主审方向可采信、「全清」不可采信；Qwen 降级；F-01 终辩 major；README 逃逸三时间戳钉死 |
| R2 | `r2-glm53-adjudication.md` | GLM-5.3 终审裁决（D1–D7 独立重验） | 带条件完成；处置两桶（立即修 ×4 / follow-up ×5）；spec 整改清单终表 |
| R2 | `r2-qwen-probe.md` | Qwen 定点验证 | P1 接缝锁成立 / P2 成立+部分成立 / P3 时间线确证（+6m42s） |
| 终局 | `final-fable-5.1-review.md` | Fable 5.1 独立终审（第二意见，2026-09-23） | 审计链事实层可采信、「带条件完成」维持；6 项重验零矛盾；修订终裁：A5 升 major（3 连 failure 下告警被 self-heal 抑制、`main()` 返 0，却归类「态一」——分类失实）；D2 措辞改「零断言覆盖」；A2 单标签 minor |
| R3 | `r3-ds4-final-attack.md` | DS4 红队（攻 Fable 修订终稿） | A5 链条逐行成立（`evolution_heartbeat.py:837-940` 无反证分支），不回滚；定性收敛「态一归类失实 + 自愈抑制交互盲区」；X3 命中终稿 5 处一稿两裁（本版已修复）；清单增 F-13/F6、动作面写全 |
| R3 | `r3-glm53-final-verify.md` | GLM-5.3 独立复核（A5 + 终稿完备性） | 与 Fable 一致：A5 major 成立（双机制压 age、无跨 tick 逃生门）；补 2 漏项（F6 gate_common.py:184 `Template` token / R2 mission.md:51 GH_TOKEN 陈旧前提）；A5 拆 I2+F5 去重；修复预研（conclusion_streak 透传 ~3 行 + main() 级测试） |
| R4 | `r4-glm53-adversarial.md` | GLM-5.3 终稿对抗质检（攻 R3 回改本身） | 修订后可交付：承重结论与 R3 逐条吻合、无失真引述；抓出 3 阻塞项（B1 T3 锚点错引 / B2 计数残留 / B3 可引用结论漏第三项 major）+ 4 非阻塞改进——B1-B3 已修（本版） |
| R5 | `r5-grok-adversarial.md` | Grok 4.7 链外独立红队（第二个链外视角） | 三大 major 事实层亲验成立、维持「带条件完成」；R5-1 D1 修复处方空心（docs/architecture.md 无四件套节，须改自含描述）；R5-2 计数漂移；R5-3 I2「完全静默」限定（dispatch 被接受 + age≤8h；stdout 仍有 ALERT 行；关单需 marker 匹配）；R5-4 spec 行号对应开工前树；R5-5 F-11/F-17 无「记录不立条」着落 |
| R6 | `r6-fable-final-verdict.md` | Fable 5.1 终审复裁（最终对抗判定） | **APPROVE WITH CONDITIONS**：裁定层收敛、两个链外视角亲验无翻案，终稿可定稿；R5 全部接受（R5-2 修复不完整处本轮已补）；三处定稿修订 + 状态标注（立即修零项执行、KB 半边受阻）已落实（本版）；mission 标签维持「带条件完成」 |

---

## 一、mission 完成度终判：带条件完成（conditionally complete）

**终判依据（三方独立复验一致）**：

1. **5/5 feature 真实落地**——Gate1 fail-closed（`substrate/gates/gate1_interface.py:266-270,300-306,327-332`，LOCAL-ONE 零残留）、heartbeat conclusion liveness（`evolution_heartbeat.py:54,343-344,366-367`）、Core↛Delivery AST 锁（`tests/test_core_delivery_import_contract.py`，真 `ast.parse`）、零红聚合移序（`ci.yml:548` droid-review < `:563` 零红 + `TestCiOkStepOrder` 契约锁）、P0 决策文档（8 处修正 + 三态分界 + Truth Basis）。三方独立复验：**160 用例复跑全绿**（GLM 主审实跑 10+76+27+47；DS4 红队独立复跑 160 passed；Qwen 独立实跑总数 160 一致）、**file:line 核验**、**PR #141 全绿 squash 合并**（`bc810b7`；checks：quality-gate / droid-review / substrate-gate-suite / ci-ok 全 SUCCESS）。
2. **21/21 断言在其验证范围内真实通过**——VAL-GATE1-001..004 / VAL-HB-001..003 / VAL-IMPORT-001..002 / VAL-CI-001..003 / VAL-DOC-001..003 / VAL-PR-001..002 / VAL-CROSS-001..004 与 features `fulfills` 一一对应、无孤儿无缺失；验证范围 = 8 白名单文件态（边界澄清见 D2）。
3. **排除项零触碰、无虚报**——B1 governance 入链 / watchdog 触发器恢复 / F8 schedule 化 / P3 两项 / B5 全部未触碰（PR 9 文件清单实证），决策文档排除表如实登记，无排除项被伪装成完成。

**标签仲裁（orchestrator 终裁，本报告承载）**：R1/R2 存在 DS4「部分完成」与 GLM-5.3 终审「带条件完成」的分歧，终裁采纳**后者**，但同时采纳 DS4 强调的**「完成 ≠ 干净」**：

- 「21/21 通过」表述用于**最终交付态**构成 **by omission 误导**：README 收口件在验证之后落地（commit `527a435` 23:28:57 +0800 > user-testing synthesis 23:22:15，**+6m42s**；对 user-testing handoff 23:22:38 为 +6m19s）、落在 VAL-CROSS-002 白名单 8 文件之外、`fulfills: []` 零断言覆盖。
- 其 §2.4 悬空缺陷（D1）因此漏网，并已随 squash 合并进入 `origin/main`。
- 21/21 认证的确切含义是「8 文件状态通过」，不足以单独认证最终交付物（9 文件态）。

**可引用结论**（供后续 mission / 报告直接使用）：

> mission 的 spec 范围内交付（P0 决策文档 + 四个零成本防线项）全部真实落地、经测试与 CI 验证并以 PR #141 全绿合并，排除项零触碰、无虚报；但最终交付态含三处 major 缺陷——①README.md:187 指向不存在的 architecture.md §2.4；②收口件在 21/21 验证之后落地且零断言覆盖（该文档缺陷因此漏网）；③liveness conclusion 防线「态一」归类失实：3 连 failure 场景告警被 self-heal 抑制链静默（R5-3 限定：dispatch 被接受且 age≤8h 时无告警信号、main() 返 0；stdout 仍有 ALERT 行）——完成声明须以「立即修」桶闭环、「follow-up」桶登记后方为干净完成。

---

## 二、方法链（证据收集 → R1 四路独立 → R2 交叉对抗 → 收敛）

### 阶段 0 · 证据收集（各审查员独立取证）

- 输入：spec 终稿（整改基线与排除清单）、mission 工件（`features.json` / 9 份 handoffs / `validation-state.json` / library notes / flows + synthesis）、PR #141（gh 只读）、现树（本地 `527a435`；fetch 后 `origin/main` = `bc810b7`）。
- 测试复跑：四个受影响测试文件共 **160 用例**（test_gate1_fail_closed 10 / test_evolution_heartbeat 76 / test_core_delivery_import_contract 27 / test_ci_structure_contract 47）。全量 2401 未独立重跑（依赖 mission 自证 VAL-PR-002 双轮复跑，各报告一致声明此边界）。

### 阶段 1 · R1 四路独立（互不通气、全程只读）

| 路 | 模型 | 攻击面/核验面 | 产出 |
|---|---|---|---|
| 红队 | DS4.1-Flash | 攻击「mission 已完成」声明：(a) README vs 代码现实 (b) 自报证据链可信度 (c) 四代码项是否表面化 (d) overclaim + PR 合并后纪律 | F-01（§2.4 悬空）/ F-02（收口件逃逸验证）/ F-04（接缝锁缺失）/ F-05（conclusion 字面窄）等 19 项编号发现；「部分完成（非整体虚报）」 |
| 主审 | GLM-5.3 | 5 个 spec feature 严格核验：spec 要求 → file:line 实现 → 定向实跑；handoffs / validation-state 复核 | 「5/5 完成无虚报」+ 5 项偏差记录（均判定合理/已闭环） |
| 一致性 | GLM-5.3-Flash | 声明-事实交叉：入口文档逐句、决策文档 vs spec 逐条、mission 目录自洽、git/PR 元数据；7/19 证据抽查 | 「总体一致」+ 1 Medium（§2.4 悬空，与 DS4 交叉印证）+ 4 Low + 2 Info |
| 代码审查 | Qwen(bailian) | 正面质量确认：按文件审查、测试强度评估、tautology 检查 | 「优秀，0 critical/major/minor」+ 4 Info 建议 |

### 阶段 2 · R2 交叉对抗（互相攻击结论，而非再审 mission）

| 路 | 攻击对象 | 关键命中 |
|---|---|---|
| DS4 二轮 | GLM 主审「无一被忽略」+ Qwen「0 发现」 | handoff discoveredIssues 全量清点 **11 条、GLM 表列 7 条**（漏 4）；gate1 第③条未进任何持久 backlog（A2）；GLM「白名单 8 文件」下列 9 个文件名；Qwen 分项测试数算术不成立（78/45 vs 实测 76/47）；README 逃逸三时间戳钉死；F-01 终辩 major |
| GLM-5.3 终审 | D1–D7 全部争议项独立重取证（不信 R1 文字、只信 file:line） | 每项终裁 + 处置两桶 + spec 整改清单终表；完成度终判「带条件完成」 |
| Qwen 定点 | P1 接缝锁 / P2 字面覆盖 / P3 时间线 | P1 成立（DS4 发现确证）；P2 成立 + spec 语义部分成立；P3 时间线确证（synthesis 23:22:15 → README 23:28:57，+6m42s） |

### 阶段 3 · 收敛与终裁

- 两轮收敛后**事实层零分歧**：全部争议项均有 file:line 级独立取证，四方对事实认定一致；仅存标签级差异（如 DS4 major vs GLM-5.3F Medium——标签差异非事实分歧）。
- orchestrator 终裁（本报告承载）：完成度标签仲裁（§一）、发现 severity 终值与处置分桶（§三）、四模型审查质量互评（§四）、spec 整改清单终表（§五）。

---

## 三、终裁发现表（severity 终值，两轮收敛）

| ID | 发现 | 终裁 | 处置桶 |
|----|------|------|--------|
| D1 | README.md:187 引用不存在的 docs/architecture.md §2.4；「四层防御体系」措辞与 v3 分层术语撞车（info） | major | 立即修 |
| D2 | README 收口件零断言覆盖（时间线 527a435 23:28:57 > synthesis 23:22:15，白名单 8 文件外，fulfills:[]；CI 29 check 实跑在含 README 的 head 上，逃逸的是内容级断言核验） | major | follow-up 登记（流程改进） |
| A2 | gate1 handoff 第③条：check_docs_references 在 tags 空集时跳过 @vX.Y.Z 扫描，consumer-onboarding.md 实含 engine tag 引用，未进任何持久 backlog | minor | follow-up 登记 |
| D3 | heartbeat 测试无 --json/conclusion 接缝锁（evolution_heartbeat.py:323 删 conclusion 字段则防线静默失效测试仍绿；先例 tests/test_trigger_ci_droid_fallback.py:1093） | minor（高优先） | follow-up 登记 |
| D4 | conclusion 仅认 "failure" 字面值（:344），timed_out/cancelled 持续发生判 alive；与 spec 批准口径一致（auto-merge.yml:115、quality-gate.yml:127 覆盖更宽可参照） | 符合批准范围/无问题；完备性 info | follow-up（需用户裁定） |
| D5 | evidence/hardening/ci/VAL-CI-003-actionlint-output.txt 0 字节无 exit 标记 | minor | 立即修 |
| D6 | 本地 main(0196dcd) 落后 origin/main(bc810b7)；本地 feature 分支未删（远端已删）；evidence/ untracked 无处置声明 | minor | 立即修 |
| D7 | 决策文档:135 元数据时滞（记 a4d2ae3/8 文件 vs 实际 527a435/9 文件）；test_ci_structure_contract.py docstring「13 job」（实为 12）；mission architecture.md「13 job」仅在 transcript，planning 已更正 | info×2 + 无问题×1 | 前两项立即修顺带 |
| A3 | scrutiny synthesis 闭环理由与 guidance 实际内容不符 | minor | 记录 |
| I2（原 A5 分类面） | liveness conclusion 防线在 3 连 failure 场景下被 self-heal 抑制链静默（R5-3 精确限定：dispatch 被接受且 age≤8h 时 `stale_alertable=False` → `effective_liveness.alive=True` → `main()` 返 0 + 关闭既有 scanner_stale 告警（需 marker 匹配）+ 每 tick 永续；stdout 仍有 ALERT 行但无失败信号），决策文档却归类「态一：CI 内阻断」——**态一归类失实**（R3 三方代码级推演 + R5 链外复核一致确认） | major | 立即修（决策文档迁态一→态二 + README:191 加限定） |
| F5（原 A5 代码面） | 同上代码行为：自愈抑制与告警可观测性冲突（「范围内措辞偏好」判定被推翻） | major | follow-up（suppression 加 failure-streak 穿透 + main() 级测试锁；修复预研 `conclusion_streak` 透传 ~3 行） |
| F6 | `substrate/gates/gate_common.py:184` 排除集缺 `"Template"` token，而 `gate0-exemptions.md:22` Gate 1 表头首列即 `Template` → 表头被登记为有效 token，latent 软通过面（三方独立验证） | minor | follow-up |
| R2 | mission.md:51 保留错误前提「gate-tests job 持有 GH_TOKEN」（planning 更正只落 architecture.md:53） | info | 记录 |

### 处置分桶汇总（R3 收敛版，按终裁表逐项列出，不做独立计数以免漂移）

> **状态（R6 定稿标注）**：立即修条件清单已可执行（R5-1 后处方为自含描述，不再依赖不存在的指向目标），**截至定稿零项执行**；其中 KB 半边（I2 决策文档迁态二、D7 元数据回填）通道受阻（memory_kb 域守卫，两层通道均拒）。转「干净完成」的门槛 = 三通道（git docs PR / 本地 git 收尾+证据 / KB 登记）全部闭环 + follow-up 登记落盘。

- **立即修**（近零成本，仓内三处改动可并一个 docs PR，KB/本地项走各自通道）：
  - D1：README.md:187 悬空引用——R5-1 实证 `docs/architecture.md` 无承载四件套的小节、`source-of-truth.md` 四件套为错目标，修复须为**自含描述**（在该节内直接给出四件套清单与 file:line，不外指），顺带「四层防御体系加固」→「四项审计防线加固」；不可指向本地 KB 决策文档（不入 git）。
  - I2：决策文档「态一」中 liveness conclusion 条目迁至「态二」并注明抑制交互边界，**同时处理态一归类的依据句**（`:69`「四项全部在 CI 内以红/绿判定，无网络凭证依赖面」——该句是分类失实的载体，须一并修订或加限定，read-first-CRUD 增补节回填，走 KB 通道）+ README:191 同步加限定。
  - D5：补跑 actionlint 并捕获 `Exit code: 0` 标记写入证据文件。
  - D6：本地 main fast-forward 至 bc810b7 → 删本地 feature 分支（PR 已 MERGED、远端已删，删除安全）→ evidence/ 处置声明（操作前备份）。
  - D7 前两项顺带：test_ci_structure_contract.py docstring 13→12（与 D1 同一 docs PR）；决策文档:135 元数据以增补节回填（read-first-CRUD，不改写原文）。
- **follow-up 登记**：D2 收口件零断言覆盖流程改进 / A2 gate1 docs 扫描静默面 / D3 接缝锁断言 / F5 suppression 加 failure-streak 穿透 + main() 级测试锁（修复预研：`conclusion_streak` 透传 ~3 行 + 4 个 return 出口 + 5 条 main() 级测试）/ F6 gate_common.py:184 补 `"Template"` token / T3 证据信任根（evidence/ 出 sha256 manifest；「既有 3 决策文档 byte-identical」补可验证记录）/ D4 conclusion 覆盖面（**需用户裁定**；实证 100 次 run 中 timed_out/cancelled=0，触发不现实，可不急）。
- **记录**：A3（scrutiny 闭环理由失配）、R2（mission.md:51 GH_TOKEN 陈旧前提，info）、R5-4（spec §8 行号对应开工前树 HEAD 0196dcd，不可用于复核 main）、R5-5（F-11/F-17 采纳「记录不立条」处置，本行即着落）、D7 第三项无问题（planning 已更正，transcript 残留属不可变历史）；「全量 2401 未独立重跑」口径按 Fable T3-3 限定（CI 在 head 527a435 上 pytest/test-groups pass 已实质覆盖）。

---

## 四、四模型审查质量互评（终值）

- **DS4-auditor**：最有效攻击者——F-01/F-04/F-05/A2 全部经交叉验证成立；R2 自我修正 R1 漏清点 handoffs（11 条列 7 条：GLM 主审清单实际 11 条只列 7 条，DS4 R1 亦未做全量清点，R2 补齐）。
- **GLM-5.3 主审**：核验扎实（160 复跑、file:line 密集）但 R1 漏检 F-01、handoff 清点不完整、「白名单 8 文件下列 9 文件名」笔误。
- **GLM-5.3-Flash**：一致性核验有效（7/19 证据抽查），独立发现 §2.4 悬空引用与 DS4 交叉印证。
- **Qwen(bailian)**：正面质量确认有价值（tautology 检查、160 复跑）但漏检 D3/D4 两项真实缺陷 + 分项测试数算术错误（78/45 vs 实测 76/47）→ 降级「合格但不完整」。

### 方法论结论（R3 修订）

- **单模型单审必有漏**（Qwen、GLM 主审各自漏不同项）；四模型两轮收敛后事实层零分歧，仅标签级差异——但**推理层存在共享前提盲区**：A5（抑制链静默）在 R1/R2 被全部在场模型以「范围内 ⇒ 非缺陷」共同放过，最终由链外终审（Fable）发现、R3 双路独立推演确认。多模型交叉对「事实认定」有效，对「共享推理前提」需引入链外视角。
- 互补结构有效：攻击型（DS4）暴露漏了什么、正面确认型（Qwen）验证做对了什么、一致性核验（Flash）独立产出关键发现（§2.4 悬空）、终审重验（GLM-5.3 R2/R3）以第一手 file:line 收敛分歧、链外第二意见（Fable）打破共享前提。
- 教训样本：「mock 不自我断言」（Qwen 的 tautology 检查）不等于「mock 与真实 CLI 契约之间的缝被锁住」（D3）——接缝锁是独立于 tautology 的检查维度；「按 spec 交付」不等于「防线在运行时有效果」（I2/F5）——分类归属需以端到端行为验证为准。
- 终稿维护教训：多轮追加式修订须同步回改正文，避免「索引行 vs 正文」一稿两裁（本轮 X3 由 DS4 命中后修复）。

---

## 五、spec 整改清单终表

对照 spec 终稿整改基线（P0 决策文档、P1 Gate1 fail-closed、P1 liveness conclusion、P2 零红移序、P2 AST 锁；排除项 B1 / watchdog / F8 schedule / P3 / B5）：

| spec 项 | 终判 | 关键证据 |
|---|---|---|
| P0 决策文档沉淀 | **完成**（含已留痕合理偏差：本地 KB 交付而非 PR——用户 2026-09-22 裁定 + `.gitignore:53-61` 实证结构上不可能走 PR） | `memory/kb/decisions/architecture-evolution-goal-correction.md`：修正版目标 10 条、8 处修正逐条 file:line、三态分界、Truth Basis 四节、附加修正 A/B |
| P1 Gate1 fail-closed | **完成** | `gate1_interface.py:306`（`fail_closed = (not templates) or tags_unavailable`）、`:327-332` 非零退出；LOCAL-ONE 全仓零命中；10 passed |
| P1 heartbeat liveness conclusion | **完成**（R3 追加注记：交付符合批准口径，但该防线存在 I2/F5 自愈抑制交互缺陷——3 连 failure 场景告警被抑制链静默（R5-3 限定），修复留 follow-up，见终裁表） | `evolution_heartbeat.py:54`（N=3 常量锁定）、`:343-345` streak 判定；76 passed；范围恰为批准口径（D4） |
| P2 零红聚合移序 | **完成** | `ci.yml:548`（droid-review）< `:563`（零红）；`TestCiOkStepOrder` 契约锁；47 passed |
| P2 Core↛Delivery AST 锁 | **完成** | `test_core_delivery_import_contract.py` 真 `ast.parse`/`ast.walk`；27 passed（19 牙齿用例） |
| 排除 5 组（B1 governance 入链 / watchdog 触发器恢复 / F8 schedule 化 / P3 两项 / B5 Dependabot 互斥） | **留后续**（零触碰，决策文档 backlog 表已登记） | PR 9 文件清单（gh 第一手）零触碰 governance / watchdog / quality-gate / auto-merge / evolution-scan workflows |

---

## 六、证据与来源锚点

### 关键 file:line 索引（两轮多方可独立复核）

- D1：`README.md:187`（§2.4 悬空引用）；`docs/architecture.md` 标题结构止于 `## 8`、`## 2 命名契约` 无子节、grep `2\.4` 零命中。
- D2 时间线三锚：最后代码提交 `a4d2ae3` 22:15:48 +0800 → user-testing synthesis 23:22:15 → README 提交 `527a435` 23:28:57（+6m42s）；`features.json` readme feature `fulfills: []`；`evidence/hardening/cross/diff-stat.txt` = 8 files/827 insertions vs PR 终态 9 files。
- D3：`evolution_heartbeat.py:318-328`（`--json status,conclusion,createdAt`）、`:343-345`（streak 判定）；`tests/test_evolution_heartbeat.py` 全文件 `--json` 零命中；同类锁先例 `tests/test_trigger_ci_droid_fallback.py:1093`。
- D4：`evolution_heartbeat.py:344`（`== "failure"`）；宽口径参照 `auto-merge.yml:115`、`quality-gate.yml:127`、`actions-budget-guard.yml:9-10`。
- D5：`evidence/hardening/ci/VAL-CI-003-actionlint-output.txt` 0 字节（对照 `grep-local-one.txt` 有 `Exit code: 1` 标记惯例）。
- D6：本地 `main` = `0196dcd` vs `origin/main` = `bc810b7`；`feat/audit-defense-hardening` 上游 `[gone]`；`?? evidence/`（`git check-ignore` 退出 1）。
- A2：gate1 handoff `handoffs/2026-09-22T13-34-50-372Z__gate1-fail-closed__*.json` discoveredIssues 第③条；仅存于 handoff 与 library note，决策文档 backlog / scrutiny guidance / features.json 三处均无登记。

### 来源

- spec 基准：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`
- 被审 mission：`~/.factory/missions/3dfa75a1-b692-4426-9edf-5db7578339fa`
- 子报告 13 份：见 §〇 索引（本目录，截至 R5；R6 复裁报告 `r6-fable-final-verdict.md` 为第 14 份）
- follow-up 登记去向：`memory/kb/decisions/architecture-evolution-goal-correction.md`（拟新增「2026-09-23 审计追加 follow-up」节；**当前未写入**，追加被 memory_kb 域守卫拦截——补写块见下方）

### 待补登记块（R3 版，拟插入决策文档 backlog 表与「## Truth Basis」之间）

> ## 2026-09-23 审计追加 follow-up（多模型交叉对抗终审·R3 收敛）
>
> | ID | 项 | 级别 | 证据锚点 |
> |---|---|---|---|
> | A2 | gate1 `check_docs_references` 在 tags 空集时跳过 `@vX.Y.Z` 引用扫描（consumer-onboarding.md 实含 engine tag 引用） | minor | gate1 worker handoff discoveredIssues 第③条 |
> | D3 | heartbeat 测试无 `--json`/`conclusion` 接缝锁（删字段防线静默失效，mock 仍绿） | minor（高优先） | `evolution_heartbeat.py:323` vs `tests/test_evolution_heartbeat.py` `--json` 零命中；先例 `test_trigger_ci_droid_fallback.py:1093` |
> | D4 | liveness 仅认字面 `"failure"`，timed_out/cancelled 持续发生仍判 alive（与批准口径一致，完备性需用户裁定；实证触发不现实） | info | `evolution_heartbeat.py:344`；宽覆盖先例 `auto-merge.yml:115`、`quality-gate.yml:127` |
> | D2 | 收口件零断言覆盖（README 在 user-testing 后落地、白名单外、fulfills 空，「21/21」不含最终态） | major（流程改进） | 527a435 23:28:57 > synthesis 23:22:15；`r2-qwen-probe.md` P3 |
> | F5 | 3 连 failure 场景 liveness 告警被 self-heal 抑制链静默（R5-3 限定：dispatch 被接受 + age≤8h → main() 返 0 + 关闭既有告警（需 marker 匹配）+ 每 tick 永续；stdout 仍有 ALERT 行）；修复 = suppression 加 failure-streak 穿透 + main() 级测试锁 | major | `evolution_heartbeat.py:846-940`；Fable/DS4/GLM-5.3 三方独立推演 + Grok 链外复核一致 |
> | F6 | `gate_common.py:184` 排除集缺 `"Template"` token（gate0-exemptions.md:22 表头首列即 Template），latent 软通过面 | minor | 三方独立验证（gate1 handoff 第④条 + 源码 + 登记表） |
> | T3 | 证据信任根：`evidence/` 未跟踪无 sha256 manifest；「既有 3 决策文档 byte-identical」主张不可验证 | minor | Fable T3-4（T3-3 为「全量未重跑」口径收窄，另见 §三记录桶） |
>
> 同场裁定（非 follow-up）：liveness conclusion 防线「态一」归类失实（I2），立即修 = 本文档该条目迁「态二」+ README:191 加限定——本登记块执行时一并处理。
- 残余不确定项（不构成终裁输入）：Gate1 CI 内模板解析走 tarball 还是 gh api 无日志级直接证据（PR #141 substrate-gate-suite 全绿已事实闭环）；全量 2401 测试未独立重跑（依赖 mission 自证双轮复跑）——详见 `r1-ds4-redteam.md`「无法定论项」。
