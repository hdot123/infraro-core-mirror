# 决策：架构演进目标口径修正（north star 修订 + 三态现状分界）

日期：2026-09-22
状态：accepted

**上位决策**：`memory/kb/decisions/architecture-layering-v3.md`（四层定稿：Evolution 核心 + Governance 判定 + Delivery 编排 + Rule Packs）。v3 回答「这个仓是什么」，本文回答「演进目标怎么表述、现状到哪一步」——两者同构互补，本文不重复 v3 的分层裁定内容。

**范围**：只沉淀目标口径与现状分界（P0），并记录本 mission 四个零成本防线项落地后的状态迁移；不改代码、不改治理配置。落地交付见文末「关联」。

## 背景

用户提出「infraro-core 架构演进最终目标 + 6 大验收指标」陈述后，经三轮交叉对抗审查（GLM-5.3 立论 → DS4.1 红队 → GLM-5.3 申辩 + Fable 5.1 独立终审，全程只读、file:line 级证据）收敛出终审裁决：目标方向与 v3 四层裁定同构，但原陈述把 **8 处未来态写成已实现态**，不可直接作为验收基线；修正 8 处口径 + 三态标注后可采信为 north star。审查输入与收敛快照见 Source Refs。

本文沉淀修正后的 north star 口径、8 处修正条目（含 file:line 证据）与三态现状分界，供后续 mission 直接消费。

## 修正版目标陈述（north star，可直接作为验收基线）

1. **单仓四层**：infraro-core 承载 Evolution 核心 + Governance 判定 + Delivery 编排 + Rule Packs（v3 裁定）；演进目标是四层职责清晰、单向依赖（Delivery → Evolution Core）有代码级防线，而非「三引擎」叙事。
2. **合并门禁**：三个 required check 构成阻断链——`quality-gate`、`droid-review`、`substrate-gate-suite`；`ci-ok` 十 job needs 闭包 + 零红 API 级兜底（「一个红都不允许」），且零红快照覆盖 droid-review 轮询窗口。
3. **消费仓接入**：脚本零副本（执行体全部由 reusable workflows / composite actions 承载）；但**模板面非零**——5 件模板需整目录复制（4 个 thin-caller + actionlint 配置）；**配置面最小化但非零**（`.evolution/config.yml` + `suppress.json` + secrets + repo variables）。
4. **接口门禁**：Gate1 语义 = 阻断**新增**违约（存量 6 项豁免为设计），且「无法验证」不得静默放行——模板不可得 / tags 不可得即 fail-closed 红。
5. **自愈**：核销三通道真实存在（scanner auto_close 五重保护 / heartbeat 自愈关单 / budget-guard 恢复关单）；**不主张 100% 闭环率**（结构上不可能 + 无度量，仅打印计数）。
6. **liveness**：判活 = createdAt 年龄在阈值内 **且** 最近 3 条 run 非全 `failure`；不再依赖「关机后重拉」类假设。
7. **语言检查**：「自动 skipped」无此机制——引擎无语言概念，pack 是命令模板数据；不列入目标。
8. **Watchdog**：引擎仓为 dispatch-only 现状（触发器 M2 停用），「捕获补偿」不是当前能力；不列入目标。
9. **发布链**：发版公告 job 走 production 人工审批门——「无人值守」存在设计性豁免，显式承认而非默认覆盖。
10. **消费者**：当前零活消费仓（`~/memory` 引用已归档 @v0.15.2）；终局验收（1/2 与指标③④）依赖消费仓接回（v3 步骤 4）。

## 8 处修正（原陈述 → 可实现口径，含 file:line 证据）

| # | 原陈述（未来态写法） | 修正口径 | 证据（2026-09-22 对现树复核） |
|---|---|---|---|
| 1 | 「唯一 ci-ok 聚合门」 | 三门 required：quality-gate + droid-review + substrate-gate-suite；ci-ok 是 quality-gate 的聚合对象之一 | live ruleset `23079535`（实测 enforcement=active、bypass_actors=[]、required=quality-gate/droid-review/substrate-gate-suite）；`.github/workflows/quality-gate.yml:69-72` |
| 2 | 「零模板复制」 | 零**脚本**副本；模板仍需复制 5 件（4 thin-caller + actionlint 配置） | `docs/onboarding/consumer-onboarding.md:4-6`（零脚本副本）、`:12`（模板目录）、`:22-30`（4 模板表）、`:39`（整目录复制）、`:47-48`（actionlint 配置必需）；`docs/onboarding/templates/` 实测 5 文件 |
| 3 | 「零配置膨胀」 | 配置面最小化但非零：config.yml 规则包 + suppress.json（非 Python 仓缺失触发 critical，半强制）+ secrets + variables | `docs/onboarding/consumer-onboarding.md:50-119`（config.yml / suppress.json / secrets 表 :104 / repo variables :117） |
| 4 | 「with/secrets 100% 吻合、直接阻断」 | Gate1 语义 = 阻断**新增**违约；三逃逸面中两个已修复（模板软通过、tags 静默跳过），存量豁免保留为设计 | `substrate/gates/gate1_interface.py:266-270,300-306,327-332`（fail-closed；LOCAL-ONE 软通过分支零命中）；`substrate/gate0-exemptions.md:20-29`（## Gate 1 存量 6 模板） |
| 5 | 「自愈闭环率 100%」 | 结构上不可能 + 零度量；核销三通道真实存在（scanner auto_close 五重保护 / heartbeat 自愈关单 / budget-guard） | `src/infra_core/engine/evolution_utils.py:94,914-950`（四闸 protected/self_audit/grace_deferred/linear_unverified；GRACE_PERIOD_TICKS=3）、`:1099-1106`（仅打印计数摘要）；heartbeat 关单 `src/infra_core/engine/evolution_heartbeat.py:613`（`resolve_cleared_alerts`，仅 evolution-heartbeat 标签告警） |
| 6 | 「语言检查自动 skipped」 | 无此机制：引擎无语言概念，pack 是命令模板数据 | `src/infra_core/packs/memory/pack.py`（命令模板数据）；全仓 `language`/`tsc`/`setup-node`/`typescript` 零命中（`.github/` 下亦零命中，2026-09-22 grep 复核） |
| 7 | 「Watchdog 捕获补偿」 | dispatch-only 现状；本仓零 caller；auto-merge 只合并不 rerun | `.github/workflows/droid-review-watchdog.yml:37-43`（HOTFIX M2：triggers disabled，`workflow_dispatch` only）、`:135`（[M4 待恢复] 注释）；`.github/workflows/auto-merge.yml` 零 `rerun` 命中 |
| 8 | 「PVE 关机判 stale 重拉」 | 零承载；判定已补 conclusion 维度：连续 3 条 run 全 failure → stale（即使新鲜） | `src/infra_core/engine/evolution_heartbeat.py:54`（`CONSECUTIVE_FAILURE_STALENESS = 3`）、`:343-344`（`all_failed_streak`）、`:366-367`（"last 3 runs concluded failure" 文案）；scanner 与 heartbeat 双面生效（共用 `_check_workflow_liveness`） |

### 附加修正 A（发布链设计性豁免）

「无人值守」不覆盖发版公告链：`.github/workflows/release-announce.yml:19-24`（`environment: production`，required reviewer 人工审批门）。需显式承认或移除，不计入「无人值守」范围。

### 附加修正 B（交付路径：决策文档走 PR → 本地 KB）

终审简报 P0 建议「决策文档沉淀（memory/kb/decisions/，走 PR + read-first-CRUD）」——**结构上不可能**：`memory/` 整目录被忽略，现有 3 份决策文档全部是本地未跟踪文件。

- 实证：`.gitignore:53-61`（memory 工件块）——`/memory/` :54、`project-map/` :55、`INDEX.md` :56、`NOW.md` :57、`tests/.memory-anchor.md` :58、`droid-wiki/` :59、`AGENTS.md` :60、`tools/` :61；该块自 commit `3695445`（2026-09-13）起未变更。
- 行号漂移更正：规划文档引用的 `.gitignore:32-38` 不成立（现树 32-38 是 IDE 忽略块）；实证位置为 `.gitignore:53-61`。
- 用户 2026-09-22 裁定：决策文档走**本地 KB 写入**（read-first-CRUD，overwrite_allowed=False → 只新增不改写），不进 git。

## 三态现状分界（本 mission 防线四件套落地后）

### 态一：CI 内阻断（有实现，在 required 链内）

- 零红扫描 + ci-ok 十 job needs 闭包：`.github/workflows/ci.yml:469-491`（job + needs×10 + `if: always()`）、`:493`（首 step 校验全部 required job）、`:563-577`（零红聚合）
- droid-review 轮询 fail-closed：`ci.yml:548-561`
- quality-gate 聚合：`quality-gate.yml:69-72`
- substrate 五门：`substrate/gates/gate0_coverage.py` … `gate4_timing_bootstrap.py`（gate-tests job，check 名 `substrate-gate-suite`）
- sha-pin 契约测试：`tests/test_uses_sha_pinning_contract.py`（远端 uses 全钉 40-hex）
- F8 静态锚点：`tests/test_platform_governance_contract.py:258`（无凭证环境含 CI 内生效，仅防期望值篡改）
- **【本 mission 新增】Gate1 fail-closed**：模板不可得（`gate1_interface.py:266-270` → `:306` `fail_closed` → `:327-332` 非零退出）与 tags 不可得（`:300-306`）均显式红；LOCAL-ONE 软通过分支已删除（零命中）
- **【本 mission 新增】liveness conclusion 判定**：连续 3 条 failure → stale（`evolution_heartbeat.py:54,343-344,366-367`），scanner 与 heartbeat workflow 双面生效
- **【本 mission 新增】零红快照时序**：零红聚合移序至 droid-review 轮询之后（`ci.yml:548` droid-review < `:563` 零红；契约锁 `tests/test_ci_structure_contract.py::TestCiOkStepOrder`），消除「轮询期间变红的 check 不感知」窗口
- **【本 mission 新增】Core↛Delivery 反向 import AST 测试锁**：红线从文档升级为测试强制（`tests/test_core_delivery_import_contract.py`；声明源 `src/infra_core/engine/__init__.py:15-16` + `docs/architecture.md:20`）

归入态一的依据：四项全部在 CI 内以红/绿判定，无网络凭证依赖面（Gate1 走公开源码 tarball 兜底）。**验证边界**（如实标注，非未落地项）：Gate1 的 CI 传输路径只在模拟环境（无 sibling / 无 gh 凭证 / 空 token）实测过，权威判定以 PR #141 的 `substrate-gate-suite` 结果为准；若该 check 红且确认为模板源不可达，既定回退为 revert `9d7b6ae`（其余三面不受影响）。

### 态二：有实现 · 条件生效

- **F8 live 断言 CI 内 SKIP**：`tests/test_platform_governance_contract.py:485-489` skipif（`_gh_api_available` :52-65；CI pytest step 无 GH_TOKEN，`ci.yml:74-81`）→ 三门 required 漂移在 CI 内不设防，验收 = 持凭证 live API 复核
- **Gate1 存量豁免**：`substrate/gate0-exemptions.md:20-29`（## Gate 1 节 6 个注册模板，保留为设计）
- **watchdog dispatch-only**：`.github/workflows/droid-review-watchdog.yml:37-43`（触发器 M2 停用，本仓零 caller；排除项）
- **Evolution Governance 非阻断**：不在 required 集合（live ruleset `23079535`）、不在 quality-gate EXPECTED_CHECKS（`quality-gate.yml:69`）（B1 排除项）
- **`.evolution` 状态托管 actions/cache**：`.github/workflows/evolution-scan.yml:147-154`（`findings_over_time.json` + `heartbeat.json`；7 天驱逐重置宽限，fail-safe 方向）（P3 排除项）

### 态三：仅文档（口径/声明层，无代码强制）

- 「100% 核销」「零配置接入」类目标陈述：本文已以修正口径改写，不再作为验收基线（对应 §8 处修正 3/5）
- Core↛Delivery 红线：**已从态三毕业**（本 mission feature 3 落地后入态一，测试强制）
- v3 归类声明与文档修正（v3 步骤 2-3）属叙事层，无 CI 强制

## 排除范围与后续 backlog

本 mission（2026-09-22）明确排除，留待后续 mission：

| 编号 | 项 | 级别 | 说明 |
|---|---|---|---|
| B1 | Evolution Governance 纳入合并阻断链 | P1 | 唯一 workflow 篡改防线但不在 required 链；需先解决 paths 过滤永不上报问题 |
| — | watchdog 触发器恢复 | P1 | 涉及 workflow 结构变更（dispatch-only → schedule/workflow_run） |
| P3 | quality-gate `per_page=100` 分页 | P3 | rerun 累积超 100 时超时红 |
| P3 | `.evolution` 持久化 | P3 | actions/cache 驱逐后宽限重置（fail-safe 方向，可延后） |
| B5 | Dependabot github-actions 生态与 governance 门禁互斥 | 产品决策 | Dependabot PR 必改 workflows → 必红，review 旁路形同虚设 |
| — | F8 改 schedule 漂移审计 | P2 | 只读 token，不注入 pytest |
| — | consumer-mixed / cli 预检面 / 消费仓接回 | — | v3 步骤 4；终局验收前置（零活消费仓） |

## Truth Basis

### Source Refs

- 终审简报：`~/.factory/missions/7eedcdc4-98d7-4c44-9770-43022debbe28/reports/goal-adversarial-audit-final.md`（2026-09-22，三轮对抗审查收敛快照，自包含）
- 三轮过程：GLM-5.3 立论 → DS4.1 红队 → GLM-5.3 申辩 + Fable 5.1 独立终审（全程只读、file:line 级证据）；同目录过程产物：`cross-audit.md` / `repo-inventory.md` / `pr-retrospective.md`
- 本 mission 规划与落地记录：mission `3dfa75a1-b692-4426-9edf-5db7578339fa`（`architecture.md` §2-§4 证据基线、`library/*.md` 落地事实、`handoffs/*.json`）

### Authority Refs

- `memory/kb/decisions/architecture-layering-v3.md`（四层定稿，accepted；本文上位决策）
- 用户 2026-09-22 裁定（AskUser 确认）：范围 = P0 决策文档 + 四个零成本防线项；决策文档走本地 KB 写入不进 git；liveness 连续 3 次 failure 判 stale；零红移序加 step-order 契约测试

### Evidence Refs

（file:line 索引，2026-09-22 对现树复核）

- 三门 required（live）：`gh api repos/hdot123/infraro-core/rulesets/23079535`（enforcement=active、bypass_actors=[]、required=quality-gate/droid-review/substrate-gate-suite）；`quality-gate.yml:69-72`
- Gate1 fail-closed：`substrate/gates/gate1_interface.py:266-270,300-306,327-332`；存量豁免 `substrate/gate0-exemptions.md:20-29`
- liveness conclusion：`src/infra_core/engine/evolution_heartbeat.py:54,343-344,366-367`；旧语义锁定测试已修订（`tests/test_evolution_heartbeat.py`，76 passed）
- 零红时序：`.github/workflows/ci.yml:548,563,577`；契约锁 `tests/test_ci_structure_contract.py`（TestCiOkStepOrder）
- AST 锁：`tests/test_core_delivery_import_contract.py`；红线声明 `src/infra_core/engine/__init__.py:15-16`、`docs/architecture.md:20`
- 记忆工件 gitignore：`.gitignore:53-61`（`/memory/` :54）
- F8 live SKIP：`tests/test_platform_governance_contract.py:52-65,485-489`；`ci.yml:74-81`
- watchdog dispatch-only：`.github/workflows/droid-review-watchdog.yml:37-43,135`
- `.evolution` cache：`.github/workflows/evolution-scan.yml:147-154`
- 发布链人工审批：`.github/workflows/release-announce.yml:19-24`
- 核销四闸与计数：`src/infra_core/engine/evolution_utils.py:94,914-950,1099-1106`；heartbeat 关单 `evolution_heartbeat.py:613`
- 消费仓接入事实：`docs/onboarding/consumer-onboarding.md:4-6,12,22-30,39,47-48,50-119`

### Conflict Status

- resolved（三轮对抗审查收敛 + 用户范围裁定；无未决分歧；v3 分层裁定与本文目标口径同构互补）

## 关联

- 落地交付：PR #141（`feat/audit-defense-hardening`，HEAD `a4d2ae3`）——4 个代码 feature、8 个白名单文件（diff --stat 与白名单逐行一致）；合并由 auto-merge 全绿自动 squash
- read-first-CRUD 合规：本文只新增，未改写既有 3 份决策；写前写后 sha256 byte-identical（`architecture-layering-v3.md` / `byom-cf-ai-gateway-migration.md` / `reconcile-evolution-script-verification.md`）
- 本文为本地 KB 工件（`memory/` 在 `.gitignore:54` 内），不入 git、不进 PR
