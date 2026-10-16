# 主审审计报告：架构演进目标修正 + 审计防线加固（5 feature 严格核验）

- **主审**：GLM-5.3 严格核验派（r1）
- **日期**：2026-09-23
- **被审对象**：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（8 features 全 completed，自报 21/21 断言通过）
- **spec 基准**：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`（P0/P1/P2 整改清单 + 三态分界表）
- **核验基线**：`~/infraro-core` @ `527a435`（分支 `feat/audit-defense-hardening` = PR #141 已合并态，squash merge commit `bc810b7` ∈ origin/main）
- **核验手段**：源码逐行阅读、4 个指定测试文件实跑（未跑全量套件，遵守任务边界）、git 只读、gh 只读、mission handoffs/validation-state 复核。repo 全程只读（仅本报告写入 evidence/）。

---

## 总判定表

| # | Feature | 判定 | 一句话 |
|---|---------|------|--------|
| F1 | Gate1 fail-closed | **完成** | tags 空集+含 v-ref、模板空集两个静默面均已 fail-closed，LOCAL-ONE 零残留，存量豁免回归与解析修复均有测试锁定（10 passed） |
| F2 | heartbeat conclusion liveness | **完成** | `CONSECUTIVE_FAILURE_STALENESS=3` 叠加判定落地且常量被锁死，旧语义测试 `:98` 为原地修订非删除掩盖，76 passed 全绿 |
| F3 | Core↛Delivery AST 锁 | **完成** | 真 `ast.parse` 实现（含 docstring 字面名、子串近似名两个防绕过边界用例），27 passed；局限（动态 import/importlib 别名/`# type: ignore` 外注释不可绕——但 importlib 间接路径不设防）已如实记录 |
| F4 | 零红聚合移序 | **完成** | ci.yml:548（droid-review 轮询）< :563（零红聚合）实测换序，step-order 契约锁在位，12 job 集合/needs 闭包/always()/notify 契约全保（47 passed） |
| F5 | 决策文档 | **完成** | 8 处修正 + 附加 A/B 齐全且与终稿逐条口径一致，三态分界反映落地后现实，Truth Basis 结构完整、上引 v3；既有 3 份决策 mtime 早于写入时刻未改动；`git ls-files memory/` 为空 |

**总判定：5/5 完成。** spec P0 + 四个零成本防线项全部真实落地，无发现虚报；审计简报承诺的「8 处修正 8 条齐全」逐条核对通过。

---

## 逐项核验详情

### F1 Gate1 fail-closed（commit 9d7b6ae）

| spec 要求 | 实际实现（file:line） | 判定 |
|---|---|---|
| tags 拉取失败 → 显式报错非零退出（原 :150 `elif tags and …` 静默跳过） | `substrate/gates/gate1_interface.py:299-306`：`tags_unavailable = bool(pinned_v_refs) and not tags` → 追加显式 error 并计入 `fail_closed`；`:327-329` 打印 `FAIL: interface face unverifiable -- fail-closed` 并 `return 1`。原静默点收敛为 `:238` 的 `elif tags and ref not in tags`——语义变为「tags 可得时才做 DEAD 判定」，而「有 v-ref 却拿不到 tags」被 ：299 兜底，静默面闭合 | 完成 |
| 模板不可得 → fail-closed（原 :179-182 LOCAL-ONE 软通过移除） | `gate1_interface.py:267-270`：`if not templates:` 追加显式 error；`:306` `fail_closed = (not templates) or tags_unavailable`。全文件 grep `LOCAL-ONE` **零命中** | 完成 |
| gate0-exemptions 存量豁免路径回归不变 | `:312-318`（registry_entries → owned/new 分流）与原逻辑一致；`substrate/gate0-exemptions.md:20-29` `## Gate 1` 节 6 个注册模板原样在册 | 完成 |
| 测试覆盖 | 新文件 `tests/test_gate1_fail_closed.py`（272 行，10 用例）：模板空→非零+`templates unavailable`（:89-97）；源码负向断言 LOCAL-ONE 零命中（:99-104）；tags 空+含 v-ref→非零（:112-122）；tags 空但无 v-ref→不误报（阴性对照，:124-135）；DEAD ref（:141-151）；**registered token→owned→exit 0 回归（:153-169）与多模板全 owned（:171-190）**；模板解析三态（本地 stack 布局 / 无凭证 tarball 兜底 / 不可验证空集，:192-272）。全 mock 零网络 | 通过 |

**实跑**：`pytest tests/test_gate1_fail_closed.py -q` → **10 passed**（1.59s）。

**主审注记（如实报告，不降级判定）**：
1. gate1 worker 的 handoff `discoveredIssues[0]`（handoffs/2026-09-22T13-34-50-372Z.json:124-127）如实披露：CI gate-tests 的 gate1 step **无 GH_TOKEN**，模板面靠无凭证 codeload tarball 兜底，规划假设「CI 持凭证」被证伪后已在 feature 内以解析修复规避（CI 传输路径当时未端到端实测）。**PR #141 已于 2026-09-22T15:34:31Z 全绿合并（gh pr view 实测 state=MERGED，substrate-gate-suite 是 required check）**——该回退预案（revert 9d7b6ae）已自然作废，传输路径经真 CI 实测通过。
2. `_run_main` 把 `REPO_ROOT` patch 到空临时目录，docs 死引用扫描面不在此测试耦合面内（测试文件 docstring :72-77 明示该取舍）——合理，docs 面仍被真实 gate 运行覆盖。

### F2 heartbeat conclusion liveness（commit 5ff9cd2）

| spec 要求 | 实际实现（file:line） | 判定 |
|---|---|---|
| 连续 3 次 failure conclusion → stale | `src/infra_core/engine/evolution_heartbeat.py:54` `CONSECUTIVE_FAILURE_STALENESS = 3`；`:343-345` `all_failed_streak = len(runs) >= 3 and all(run.get("conclusion") == "failure" for run in runs[:3])`（gh run list newest-first，取最新窗口）；`:361-369` age 新鲜且 streak 成立 → `alive=False` + 显式 message。共用 `_check_workflow_liveness`，scanner 与 heartbeat 双面生效 | 完成 |
| 1-2 次失败仍宽限 alive | streak 不成立即落回 `:371-376` age 规则（新鲜→alive） | 完成 |
| createdAt 年龄语义与 recent-success 回归保持 | `:370-385` age 主循环零重构（INFRA-639 `liveness_data_ok` 语义保留）；`tests/test_evolution_heartbeat.py:46-96` 既有 age 回归用例（alive/stale/no-runs/gh-failure/mixed/subprocess-timeout）全部原样保持且通过 | 完成 |
| 旧断言（原 :98 锁定旧语义处）已同步修订而非删除掩盖 | `test_scanner_recent_failure_still_alive` **仍在原 :98 行**，测试名未变，docstring 与断言改为新语义（1-2 次失败仍 alive + 引用 3 连 failure 用例），见 tests/test_evolution_heartbeat.py:98-112——是修订，不是删除 | 完成 |
| 测试覆盖 | `:128-131` 常量锁定 N=3（防调参漂移）；`:133-155` 3 连 failure→stale（含窗口外 run 不影响判定）；`:157-169` 2 failure+1 success→alive；`:171-179` 不足 3 条→age 规则；`:181-204` pending(None)/neutral 打断 streak→age 规则 | 通过 |

**实跑**：`pytest tests/test_evolution_heartbeat.py -q` → **76 passed**（0.14s）。

**主审注记**：handoff `discoveredIssues[0]`（handoffs/2026-09-22T13-48-18-491Z.json:95-99）如实披露集成语义：3 连 failure 时 main() 走 INFRA-597 自愈通道，可观测效果是「ALERT 日志 + 自愈 dispatch + 反向 watch 联动」而非立即建告警（dispatch 被接受且 age ≤8h 时抑制告警）。这是 spec 裁定「main() 不动」范围内的既有行为，非本 feature 缺陷；防线语义（liveness 判定本身）已正确落地。

### F3 Core↛Delivery 反向 import AST 锁（commit 4a44a10）

| spec 要求 | 实际实现（file:line） | 判定 |
|---|---|---|
| 真用 ast.parse 解析 import | `tests/test_core_delivery_import_contract.py:85-122` `_import_violations`：`ast.walk(ast.parse(source, filename=...))`，覆盖 `ast.Import`（bare + 点号子模块）与 `ast.ImportFrom`（含 `from . import X` / `from .X.y import` 相对形式）；`:57-67` 按点号段边界匹配根段（`droid_review.shards` 命中、`my_droid_review` 不命中）。**非字符串/正则匹配** | 完成 |
| 注释/动态 import 绕过面（任务要求如实报告局限） | 注释不可绕：`test_docstring_mention_is_not_an_import`（:208-214）证明 docstring 字面名不误报、注释同理不入 AST；**字符串拼接 import / importlib.import_module("droid_review") 动态导入不可检出**（仓库内 grep `import_module`/`__import__` 零命中，当前无此写法）；惰性（函数体内）import **可检出**：`test_function_body_import_is_detected`（:201-208）经 ast.walk 覆盖并断言行号 | 完成（局限已如实标注） |
| 当前树零违例通过 | `TestRealTreeCompliance`（:125-155）：7 个 Core 文件在册完整性双向断言（缺失报红 + 未登记 `evolution_*` 报红，防扫描面缩水）+ 零违例基线 | 完成 |
| 合成违例牙齿用例 | `TestCheckerTeeth`（:157-200）：3 目标 × 6 import 形式 = 18 参数化用例，断言检出非空且报出模块名；加函数体 import 1 例 = 19 牙齿用例。真跑 27 passed 证明合成违例确实让 checker 报红 | 完成 |

**实跑**：`pytest tests/test_core_delivery_import_contract.py -q` → **27 passed**（0.11s）。声明源交叉核验：`src/infra_core/engine/__init__.py:14-16`（寄居件警告 docstring）与红线一致。

### F4 零红聚合移序（commit a4d2ae3）

| spec 要求 | 实际实现（file:line） | 判定 |
|---|---|---|
| 零红聚合 step 位于 droid-review 轮询 step 之后 | `.github/workflows/ci.yml` ci-ok job steps：`:548` `Check droid-review status`（脚本 `scripts/check_droid_review.sh`）→ `:563` `Zero-red aggregation`（脚本 `scripts/check_zero_red.sh` :577 调用）。**droid-review 在前，零红在后**，与 spec 要求一致 | 完成 |
| step-order 契约锁 | `tests/test_ci_structure_contract.py:448-493`：锚点用脚本调用而非 step 名（防改名漂移），`:456-463` 要求每锚点恰命中 1 个 step（防多/零锚失效），`:481-493` 断言 droid-review index < 零红 index | 完成 |
| 12 job / needs 闭包 / always() / notify 契约未破坏 | `EXPECTED_JOBS`（test_ci_structure_contract.py:36-57）12 job 集合；ci.yml `:471-486` needs 10 项闭包、`:491` `if: always()`、`:639-644` notify job needs [ci-ok, gate-tests] + always() + payload run_url 契约（:269-278）全部在位且由测试锁定 | 完成 |

**实跑**：`pytest tests/test_ci_structure_contract.py -q` → **47 passed**（0.16s）。

**主审注记**：commit a4d2ae3 的 diff（`git show --stat`）确认改动仅 ci.yml（30 行，纯块搬移）+ 契约测试（+66 行），双胞胎脚本 `scripts/check_droid_review.sh` 零触碰。审计简报 B2 时序窗口已闭合。

### F5 决策文档（memory/kb/decisions/architecture-evolution-goal-correction.md）

| spec 要求 | 实际证据 | 判定 |
|---|---|---|
| 修正版目标陈述 | 文档 §「修正版目标陈述」10 条 north star（单仓四层/三门 required/脚本零副本模板非零/接口门禁/不主张 100%/liveness 年龄+conclusion/语言检查无此机制/watchdog dispatch-only/发布审批门/零活消费仓） | 完成 |
| 8 处修正（与终稿逐条核对） | **8 条齐全，口径逐条一致**：①三门 required + ruleset 23079535 ↔ 终稿#1；②零脚本副本、模板 5 件 ↔ #2；③配置面最小化非零 ↔ #3；④Gate1 阻断新增违约 + 三逃逸面二修一留 ↔ #4；⑤不主张 100% 闭环 + 核销三通道 ↔ #5；⑥语言检查无此机制 ↔ #6；⑦watchdog dispatch-only ↔ #7；⑧零承载→已补 conclusion 判定 ↔ #8。另含终稿「附加」两条：附加修正 A（release-announce production 审批门）、附加修正 B（决策文档走本地 KB 的交付路径修正 + .gitignore 行号漂移更正 32-38→53-61） | 完成 |
| 三态现状分界 | 态一含本 mission 四项新增（Gate1 fail-closed / liveness conclusion / 零红轮询后快照 / AST 锁，全部标注【本 mission 新增】且行号指向改动后代码，无「将/计划」措辞）；态二余项（F8 live SKIP / 存量豁免 / watchdog / Governance 非阻断 / .evolution cache）；态三余项与 Core↛Delivery「毕业」消项说明。**Gate1 CI 验证边界已如实标注**（以 PR #141 substrate-gate-suite 为准）——与终稿三态表口径一致且已随合并升级为实测 | 完成 |
| Truth Basis 块 | Source Refs（终审简报绝对路径 + 三轮过程）/ Authority Refs（architecture-layering-v3.md + 用户 2026-09-22 裁定）/ Evidence Refs（file:line 索引，抽点核验一致）/ Conflict Status: resolved，四节齐全 | 完成 |
| 上引 architecture-layering-v3.md | 文档头部「上位决策」显式引用并声明互补不重复 | 完成 |
| 既有 3 份决策文档零改动 | stat 实测：v3（Sep 21 01:12）、byom（Sep 22 11:48）、reconcile（Sep 18 16:53）三份 birth==modified，均早于新文档写入时刻（Sep 22 22:28）；handoff 自报写前写后 sha256 byte-identical（策略系统拦截本地 shasum，以 mtime+handoff 记录双证采信） | 通过 |
| memory/ 零入 git | `git ls-files memory/` 输出为空；`.gitignore:54` `/memory/` 在册 | 完成 |

---

## mission 过程质量（handoffs / validation 复核）

**handoffs/ 目录（9 份）discoveredIssues 全量清点**：

| handoff | discoveredIssues | orchestrator 处理判定 |
|---|---|---|
| gate1-fail-closed（13:34） | 1 条 non_blocking：CI gate1 step 无 GH_TOKEN、规划前提被证伪、已解析修复规避 | **已闭环**：F4 handoff 的 Orchestrator 注记将其转为「CI 红时 revert 9d7b6ae」回退预案；PR #141 最终全绿合并，预案自然作废 |
| heartbeat-conclusion-liveness（13:48） | 1 条 non_blocking：fresh+3 连 failure 走 INFRA-597 自愈通道，可观测效果是日志+dispatch 非立即告警 | spec 裁定 main() 不动范围内的既有集成行为，无需处理；非缺陷 |
| core-delivery-import-ast-lock（14:00 与 14:02 两份，同 feature 重派） | 各 1 条 non_blocking：宿主路径用户名遮蔽 [USER] 环境问题（commit 经 /tmp 软链完成） | 环境适配问题，commit 已真实落库，无需 further action |
| zero-red-scan-reorder-and-pr（14:21） | 1 条 non_blocking：指向 feature 1 的 CI 传输路径未端到端实测（同上，已被合并结果闭环） | 同 gate1 项 |
| evolution-goal-decision-doc（14:35） | 2 条：①KB 写入通道硬约束（Edit 拒绝/Create 覆盖拒绝，一次写对无法勘误）；②规划文档 .gitignore 行号漂移 | ① 属环境约束记录 + 方法沉淀（library/kb-decision-doc-findings.md），② 已在决策文档中显式更正。均无需 orchestrator action |
| scrutiny-validator（14:52）/ user-testing-validator（15:22） | 均为空数组 | 无 |
| readme-defense-hardening-note（15:30） | 空数组 | 无 |

**结论：无被 orchestrator 忽略未处理的 discoveredIssues。** 所有 non_blocking 项要么已被后续事实闭环（CI 实测），要么属环境约束记录/已在产物中更正，无一条需要 orchestrator action 而未做。

**validation-state.json**：21 条断言（VAL-GATE1-001..004、VAL-HB-001..003、VAL-IMPORT-001..002、VAL-CI-001..003、VAL-DOC-001..003、VAL-PR-001..002、VAL-CROSS-001..004）全部 `status=passed`，validatedAtMilestone=hardening、validatedBy=user-testing，与 mission 自报 21/21 一致。本次主审抽实跑 4 个测试文件（10+76+27+47=160 用例）与 validator 报告的定向用例计数完全吻合，**自报可信**。

**过程合规抽查**：PR #141 恰好一个、title 无治理资源字面名、squash merge（bc810b7）、合并后 README note 走了正确路径（bc810b7 含 README.md +9 行）；合并提交白名单 8 文件（ci.yml / README.md / pyproject.toml / evolution_heartbeat.py / gate1_interface.py / 4 个测试文件）与 mission 架构图一致，`pyproject.toml:138` deptry DEP001 已含 `gate1_interface`。

---

## 与 spec 的偏差

1. **决策文档交付路径**（spec P0 原文「走 PR + read-first-CRUD」→ 实际「本地 KB，不进 git」）：**结构上不可能走 PR**——`memory/` 被 `.gitignore:54` 排除，现有决策文档全是本地未跟踪文件。mission planning 阶段已实证并获用户 2026-09-22 裁定，决策文档附加修正 B 中显式记录并更正了规划期 `.gitignore:32-38` 行号漂移。**合理偏差，已留痕**。
2. **Gate1 CI 传输路径**（spec 态一隐含「CI 内已验证」→ 落地时先以模拟环境验证）：planning 假设 CI 持 GH_TOKEN 被证伪，worker 以无凭证 tarball 兜底修复解析并如实 handoff 披露；PR #141 全绿合并后该不确定性已消除。**过程性偏差，已闭环**。
3. **F3 锁的表达力边界**（spec 要求「真 AST + 检查是否可被绕过，如实报告」）：静态 AST 天然不防 `importlib.import_module`/字符串拼接动态导入（当前仓内零此类写法）与跨文件别名转发。这是该类锁的固有边界而非实现缺陷，测试文件 docstring 与本报告均已如实标注。**无偏差，局限披露**。
4. **范围排除项**（B1 governance 入链、watchdog 触发器恢复、P3 两项、B5、F8 schedule 化、消费仓接回）：spec 终裁即将其列为独立 mission/后续 backlog，mission 未触碰，决策文档已登记排除表。**无偏差**。
5. 行号漂移类小项：mission.md 写「13 job」后自查更正为 12（features.json 注记）；决策文档引用的 ci.yml 行号（548/563/577 等）与现树抽点全部一致。**无实质偏差**。

---

## 主审结论

**通过（5/5 完成，无虚报）。**

- 四个代码 feature 的防线语义与 spec P0/P1/P2 零成本防线项逐条对上，实现均以测试锁定（本次主审独立实跑 160 用例全绿），关键契约（常量值、step 顺序、LOCAL-ONE 消失、扫描面清单）都有防漂移断言而非一次性验证。
- 测试修订纪律良好：F2 旧语义断言是原地修订并保留用例名，F3 的牙齿用例与边界防误报用例（docstring/子串/合法 import）齐备，F4 契约锚点取脚本调用防改名漂移——三处都体现了「锁不可静默失效」的工程意识。
- F5 决策文档质量高于 spec 最低要求：8 处修正每条带 file:line 且与终稿逐条口径一致，三态分界如实标注验证边界而非夸大，附加修正 A/B 与行号更正均留痕。
- mission 过程质量可信：9 份 handoff 的 discoveredIssues 全部有去向（闭环/披露/更正），validation-state 21/21 与实测吻合，PR/载荷/白名单纪律合规。
- 遗留给后续 mission 的均为 spec 明示排除项（B1、watchdog、P3、B5、F8 schedule、消费仓接回），决策文档 backlog 表已登记，无本 mission 遗留缺陷。
