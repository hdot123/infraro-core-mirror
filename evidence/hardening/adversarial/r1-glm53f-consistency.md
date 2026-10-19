# R1 事实一致性核验报告（GLM-5.3 Flash，2026-09-23）

**核验对象**：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（架构演进目标修正 + 审计防线加固）
**基准 spec**：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`
**核验方式**：严格只读（git 只读 / cat / grep / sed / gh pr view --json），全部行号与文件状态对现树独立复核。文中「入口文档」指仓库根说明文档，「架构文档」指 docs 下架构权威文档，「修正决策文档」指 KB 目标修正决策文档。

---

## 总一致性判定

**总体一致（PASS with findings）**。mission 交付链（PR #141 + 修正决策文档 + 21 断言验证）与 spec 终稿口径、实际代码状态、git/PR 元数据高度一致；未发现「未来态写成已实现态」复发。发现 **1 处中等严重度文档缺陷**（入口文档引用不存在的架构文档 §2.4）+ 4 处轻微问题 + 2 处注记。

---

## 逐项核对表（声明 → 事实 → 判定）

### 1. 入口文档「防线现状」节逐句（仓库根说明文档 :185-195）

| 声明 | 事实核验 | 判定 |
|---|---|---|
| 「本仓库已完成四层防御体系加固，详见架构文档 §2.4」（:187） | 四项防线确已落地（下述逐条）。但**架构文档不存在 §2.4**：章节结构为 §1-§8，§2「命名契约」无子节，全文无「审计防线/防线现状」内容（grep 零命中） | **引用悬空（不一致）** |
| 同句「四层防御体系加固」 | 实指四项防线（四件套），与 v3「四层体系」（分层架构术语）撞车，易误读为架构分层变更 | **含糊** |
| 「接口门（Gate1）fail-closed：模板/tags 不可验证时显式红而非软通过（CI 模板面经公开仓 tarball 兜底首次真实验证）」（:189） | `substrate/gates/gate1_interface.py:266-270`（模板不可得→errors）、`:300-306`（tags 不可得→fail_closed）、`:327-332`（return 1）；LOCAL-ONE 全仓零命中；PR #141 `substrate-gate-suite` check = SUCCESS（rollup 实测），「首次真实验证」成立 | **一致**（该句写于 PR OPEN 期，CI 后验成立） |
| 「heartbeat liveness conclusion 维度：连续 3 次 failure 判 stale」（:191） | `src/infra_core/engine/evolution_heartbeat.py:54`（CONSECUTIVE_FAILURE_STALENESS=3）、`:343-344`（all_failed_streak）、`:366-367`（stale 文案） | **一致** |
| 「零红聚合时序：ci-ok 内零红聚合在 droid-review 轮询之后执行（step-order 契约锁定）」（:193） | `.github/workflows/ci.yml:548`（Check droid-review）< `:563`（Zero-red aggregation）；`tests/test_ci_structure_contract.py:466`（TestCiOkStepOrder）存在 | **一致** |
| 「Core↛Delivery 反向 import：由 AST 契约测试强制」（:195） | `tests/test_core_delivery_import_contract.py` 存在（PR diff +212 行）；红线声明源 `engine/__init__.py:13-18`、架构文档 :20 实证 | **一致** |

### 2. 修正决策文档 vs spec 终稿（KB 目标修正决策文档）

| 维度 | spec | 决策文档 | 判定 |
|---|---|---|---|
| 修正 #1 三门 required | quality-gate+droid-review+substrate-gate-suite，ruleset 23079535 | 同口径，quality-gate.yml:69-72（实测为 EXPECTED_CHECKS 块，吻合） | 一致 |
| 修正 #2 零脚本副本/模板 5 件 | consumer-onboarding 文档 :1-4,44-50 | 同口径，行号细化 :4-6,12,22-30,39,47-48 | 一致 |
| 修正 #3 配置面最小化非零 | config.yml+suppress.json+4 secrets+variables | 同口径，:50-119 | 一致 |
| 修正 #4 Gate1 三逃逸面 | 三逃逸面（软通过/豁免/tags 跳过） | 更新为「两个已修复，存量豁免保留为设计」——反映落地后现实，时点演进有据 | 一致 |
| 修正 #5 自愈非 100% | 四闸 :905-952、仅打印计数 :1099-1106 | 同口径（行号更新 :914-950），heartbeat 关单 :613 | 一致 |
| 修正 #6 语言检查无机制 | 零 TS 工具链证据 | 同口径 | 一致 |
| 修正 #7 Watchdog dispatch-only | :37-43 M2 停用、quota-sweep 不可达 | 同口径，:135 M4 待恢复注释实测吻合 | 一致 |
| 修正 #8 liveness conclusion | 零承载（审计时点） | 更新为「已补 conclusion 维度」——落地后演进，:54,343-344,366-367 精确吻合 | 一致 |
| 附加 A 发布链人工审批 | release-announce.yml:18-24 | 同口径（:19-24 实测 environment: production） | 一致 |
| 附加 B 决策文档交付路径 | spec §7 建议随 PR 提交 | **spec 外新增口径**：忽略清单 :53-61 实证结构上不可能 + 用户裁定 | **超范围新增（有据、已声明）** |
| 三态分界 | 态一 6 项/态二 8 项/态三 2 项 | 态一 +4 新增（四件套落地迁入），态二减 3（软通过/时序/conclusion 已修），态三减 1（AST 锁毕业）；余项全保留且口径一致 | 一致（演进合理） |
| 修正版目标陈述 10 条 | spec 无逐条版（隐含于 8 修正+附加） | 8 修正+附加 A+零活消费仓的正面整合，无新口径 | 一致 |

### 3. mission 目录自洽

| 核对项 | 结果 | 判定 |
|---|---|---|
| features.json 8 feature：fulfills 4+3+2+5+7=21 断言 vs user-testing/synthesis.json 21 passed | 逐条比对：VAL-GATE1-001~004 / VAL-HB-001~003 / VAL-IMPORT-001~002 / VAL-CI-001~003 / VAL-DOC-001~003 / VAL-PR-001~002 / VAL-CROSS-001~004 完全一一对应，无孤儿、无缺失 | **一致** |
| mission.md「明确排除」（B1/watchdog/P3/consumer-mixed/消费仓接回）vs PR 最终 diff 9 文件 | diff 仅：ci.yml、入口文档、项目配置、evolution_heartbeat.py、gate1_interface.py、4 个测试文件；watchdog/quality-gate/auto-merge/evolution-scan workflow 零触碰；excluded-items-check.txt 佐证 NO_EXCLUDED_CHANGES | **一致（排除零违反）** |
| mission.md「5 features」vs features.json 8 条 | readme 收口 feature（end-of-mission gate 追加）+ 2 validator 为流程件；state.json initialFeatureCount=5 | 含糊（追加未在 mission.md 声明，非自洽破坏） |

### 4. git/PR 元数据

| 声明 | 事实 | 判定 |
|---|---|---|
| PR #141 state=MERGED、baseRefName=main | gh pr view：MERGED / main / mergedAt 2026-09-22T15:34:31Z | 一致 |
| 五提交清单 | 9d7b6ae（gate1）/ 5ff9cd2（heartbeat）/ 4a44a10（AST 锁）/ a4d2ae3（零红移序）/ 527a435（入口文档），与 feature 1-4 + readme feature 一一对应；mergeCommit bc810b7 = origin/main HEAD | 一致 |
| 分支同步 | 本地 feat/audit-defense-hardening (527a435) = origin 同名分支；**远端分支已删**（ls-remote 仅 main）；**本地 main (0196dcd) 落后 origin/main (bc810b7)**；当前 checked-out 仍在 feature 分支 | 部分一致（见不一致 #4） |
| PR 全 checks | rollup：quality-gate/droid-review/substrate-gate-suite/ci-ok 全 SUCCESS | 一致 |

---

## 不一致清单（severity）

1. **[Medium] 入口文档 :187 悬空引用**：「详见架构文档 §2.4」——架构文档无 §2.4 小节（§2 为命名契约无子节，全文无审计防线内容）。恰落在本 mission 要修的「文档-事实一致」域，属新文案引入的引用错误（非未来态误写）。
2. **[Low] 入口文档 :187 措辞含糊**：「四层防御体系加固」与 v3「四层体系」（分层架构术语）撞车，实指四项防线（四件套），易误读为架构分层变更。
3. **[Low] 决策文档时滞未回填**：「关联」节记录 HEAD a4d2ae3 / 8 白名单文件（快照时点真实），但 PR 终态为 5 commits / 9 文件（:527a435 追加于文档完成后）；「验证边界」段以 PR #141 substrate-gate-suite 为权威，该 check 已 SUCCESS 但文档未回填（KB 只增不改约束下可理解，仍属事实滞后）。
4. **[Low] 本地 git 收尾残留**：本地 main 落后 origin/main 一个合并提交未 fast-forward；本地 feature 分支未删（远端已删）；当前分支停在 feature 分支。「合并后三件事」本地半边未完成（readme feature 走路径 a 不负责合并后流程，后续无人接手本地收尾）。
5. **[Low] 证据文件 VAL-CI-003-actionlint-output.txt 为 0 字节空文件**：actionlint 通过时零输出属正常，但未捕获 exit code 标记，无法区分「通过无输出」与「未捕获/未跑」（对照 grep-local-one.txt 有 Exit code: 1 标记），证据力不足。
6. **[Info] 附加修正 B 为 spec 外新增口径**：决策文档交付路径从 spec §7「随 PR 提交」改为「本地 KB」，有忽略清单 :53-61 实证 + 用户裁定背书且文档内明示，非夹带。
7. **[Info] mission architecture.md §1 残留 stale 表述**：synthesis 已建议修正「13 job→12 job」与忽略行号 :32-38→53-61，mission 内文档未回改（不影响 repo 交付物）。

---

## 证据抽查记录（7/19 份，超出最低 4 份要求）

| 证据文件 | 内容核验 | 判定 |
|---|---|---|
| cross/VAL-CROSS-002-diff-stat.txt | 8 files changed / 827 insertions——与 a4d2ae3 时点 git diff --stat 逐行吻合（PR 终态 9 文件系入口文档追加所致，时点一致） | 有效 |
| doc/VAL-DOC-001-decision-doc-contents.md | 决策文档全文快照，与实际 KB 修正决策文档逐节比对一致（H1/8 修正表/三态/Truth Basis/关联全含） | 有效 |
| gate1/grep-local-one.txt | 仅「Exit code: 1」（13 字节）——grep 无匹配标准退出码；code-reality-crossref.txt 佐证 ZERO_HITS；我独立 grep 确认 LOCAL-ONE 零命中 | 有效（薄但真） |
| pr-gate/VAL-PR-001-branch-and-pr.md | 单分支单 PR（gh pr list 实录）、治理文件名 grep=0、pending-ci 瞬态性诚实标注、以 Auto-merge job passed 佐证注册 | 有效 |
| pr-gate/VAL-PR-002-VAL-CROSS-001-gate-suite.md | 2401 passed/5 skipped 双运行、mypy/ruff clean、5 skips 逐一列出与 known 清单吻合；诚实记录 1 次 flaky（test_trigger_release_contract，二三次通过）——与 scrutiny synthesis 数字一致 | 有效 |
| ci/VAL-CI-003-actionlint-output.txt | **0 字节空文件**——见不一致 #5 | 证据力不足 |
| cross/code-reality-crossref.txt + VAL-CROSS-004-future-tense-check.txt | LOCAL-ONE ZERO_HITS / fail-closed 行号 / CONSECUTIVE_FAILURE 行号 / CI step order——与我独立核验的现树代码事实全部吻合；future-tense 检索逻辑正确（命中均为引用原文或边界声明，非未落地表述） | 有效 |

---

## 结论

mission 交付的事实一致性整体**良好**：21 断言闭环无孤儿、排除清单零违反、PR/commit 元数据吻合、决策文档 8 修正与 spec 逐条对应且三态演进有据、入口文档四项防线声明全部有代码级实证、无「未来态写成已实现态」复发。需要处理的核心问题只有一项：**入口文档 :187 的 §2.4 悬空引用（Medium）**，建议在后续 docs PR 中改为指向实际存在的权威位置（如架构文档 §5 门禁矩阵或新建小节）。其余为低 severity 收尾项（本地 main 未同步、决策文档终态未回填、空 actionlint 证据文件），不构成验收障碍。

**核验环境备注**：核验期间 origin/main ref 经后台 fetch 从 0196dcd 前进至 bc810b7（即 PR #141 合并提交），所有最终核对均在 bc810b7 ref 状态下完成。
