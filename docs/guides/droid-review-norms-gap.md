# Droid Review 成文规范缺口（相对官方自定义审查规则）

**状态**：勘误修订版（2026-10-26）——按 SSOT《审查用｜Droid Review 成文规范缺口》勘误后口径修订：被翻案的原表述仅保留在 §4.0 勘误对照表（带标注引用），结论位不再出现；对齐包（prompt 成文升格 + E1/E2 契约测试）已随 `feat/droid-review-prompt-alignment` 分支施工。  
**范围**：只用官方「自定义审查规则 / custom instructions」纪律做尺子，对照本仓现有审查成文面。  
**非范围**：不搬 Copilot / Factory **产品机制**（Ruleset、Approve、effort、droid-action 自动注入路径）；不替代流程四簇药、PR 入口四组、closed→cancel。  
**结论一句话**：BYOM 在用；成文面有 `.github/review/shard-review-prompt.md`，**不是「零规范」，是质量未达标**——相对官方「怎么写审查规则」存在结构与字面覆盖缺口；对齐包已按勘误口径落地整改。

---

## 1. 为什么用 Copilot 规则当尺子

| 事实 | 含义 |
|------|------|
| GitHub **不公布**「Droid Review」内置判据 / System Prompt | Droid 是 Factory 第三方能力，挂在 Actions 上 |
| Copilot code review **因额度限制**必须把「自定义指令怎么写」成文公开 | 公开的是写法纪律与入口，不是内置黑盒判据 |
| 本仓不用 Copilot Review 产品 | **只借鉴**「有额度就必须成文规范」的纪律；落点仍走本仓 Droid / BYOM |

**官方对内置面的表述（黑盒，仅作边界）**  
Copilot code review 使用 “a carefully tuned mix of models, prompts, and system behaviors”（见 [GitHub Copilot code review](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/code-review)）。完整内置判据文本不公开——与 Factory Droid 内置 prompt 同属黑盒侧。

---

## 2. 官方证据索引（尺子原文）

### 2.1 写法与结构

| 纪律 | 官方出处 | 原意摘要 |
|------|----------|----------|
| 短、标题分节、bullet、**短祈使句** | [Using custom instructions…](https://docs.github.com/en/copilot/tutorials/customize-code-review) · Writing effective | 忌长叙述；用 Check/Verify 式祈使 |
| 推荐分节：Purpose / Naming / Error / Security / Testing / Performance 等 | 同上 · Recommended instruction file structure | 结构服务于审查决策 |
| 先写 **10–20** 条可测规则再迭代 | 同上 · Start with a minimal instruction set | 可观测、可增删 |
| **正反例** | 同上 · Provide concrete examples | Avoid / Prefer 代码块 |
| 全仓 + **路径特化** | 同上 · Organizing instructions | `copilot-instructions.md` vs `*.instructions.md` + `applyTo` |
| 可指向仓内 checklist（勿只丢外链） | 同上 · What not / Follow external links | 标准应内联或仓内文件 |

### 2.2 应审什么与优先级

| 纪律 | 官方出处 | 原意摘要 |
|------|----------|----------|
| Security / Performance / Quality / **Review Style** | [Code reviewer（Customization library）](https://docs.github.com/en/copilot/tutorials/customization-library/custom-instructions/code-reviewer) | 四块 + 反馈风格 |
| **Always prioritize** security **and** performance that could impact users | 同上；完整范例亦见 customize-code-review | 高于其它主题 |
| Security 含 XSS、input validation；Performance 含 cleanup、caching | customize-code-review 完整范例 | 清单项 |
| Testing Standards | 同上完整范例 | 新功能/边界/错误路径 |
| Repository context / 领域正确性 / 敏感数据不落盘 | [Build an optimized review process](https://docs.github.com/en/copilot/tutorials/optimize-code-reviews) | 仓上下文与领域条 |

**官方优先级原话（Code reviewer / 完整范例同义）**：

> Always prioritize security vulnerabilities and performance issues that could impact users.

### 2.3 勿写（What not to include）

出处：[customize-code-review · What not to include](https://docs.github.com/en/copilot/tutorials/customize-code-review)

- 改评论 UX / 格式 / emoji  
- 改 PR overview 长什么样  
- 用指令假装「不处理完不许合并」等改核心职能  
- 只丢外链当标准  
- **模糊质量口号**（Be more accurate / Don’t miss any issues / Be consistent）

### 2.4 额度 → 必须成文（产品纪律推论）

出处：同上教程引言 + Understanding how… processes instructions  

- 审查消耗额度 / 上下文；指令过长会被淹没  
- 因此必须成文、宜短、宜可测、宜优先高价值主题  
- **推论（本仓对抗采用）**：规范须约束「审什么 / 不审什么 / 优先什么」，避免额度花在风格噪声；仅有杀进程硬顶不算「有规范」

### 2.5 Factory 对等入口（产品对位，**不升格为本缺口主条**）

出处：[Automated Code Review · Custom review guidelines](https://docs.factory.ai/software-factory/code-review-ci)

> Add repository-specific review guidelines by creating a `.factory/skills/review-guidelines/SKILL.md` file… These guidelines are automatically picked up and injected into every review run. No workflow changes needed.  
> The `review-guidelines` skill applies to code review only. Security review reads its own `security-review-guidelines`…

**对抗定稿口径**：本仓走自研分片 + `droid exec -f`，缺该路径属**产品对位缺口**，已降出「官方规则账」主条；若未来对齐 Factory 入口再单独立项。

Factory「审什么」高阶列表（同页 What Droid reviews）含 SQL/**XSS**、resource leaks、missing error handling 等，并写明 **skips stylistic concerns**——可作对照参考，但不把「必须接 droid-action」写成违规。

---

## 3. 本仓现状（靶子证据；main@5f52919 对齐包落地前基线）

| 项 | 证据 |
|----|------|
| 成文面 | `.github/review/shard-review-prompt.md`（存在；运行时由 `run_shard.sh` 从 **BASE** `cat` 拼进 `prompt.md`） |
| Factory skill 入口 | **无** `.factory/skills/review-guidelines/SKILL.md` |
| 阻断映射 | `src/infra_core/engine/droid_review/publish_findings.py`：`BLOCKING_SEVERITIES = frozenset({"P0", "P1"})` |
| 审查主题成文 | 仅 4 条主题词簇（Correctness / Security / Performance / Maintainability） |
| Severity 成文 | P1 含 *missing error handling*；P2 含 *performance issue*；P3 = *style, minor improvement, suggestion* |
| 额度成文 | Output ≤20；Prioritize；*if budget remains* 再扫 P2/P3；*Prefer fewer, high-quality* |

> 注：本表描述对齐包施工前的基线（origin/main@5f52919）。对齐包落地后的成文面由 `tests/test_droid_review_prompt_contract.py` 契约测试锁定；`BLOCKING_SEVERITIES` 不在本次整改面内。

---

## 4. 定稿：官方规则账（勘误后执行口径）

原定稿（2026-10-25～26，经「代码对抗 ×4 波 → 结果对抗 ×4 波」收窄，第 4 波结果对抗新问题 0）之后另经两轮对抗勘误：R1 打原定稿 S1–S6 → STANDS_WITH_NITS；R2 复核 R1 → STANDS，翻案 0。五处被翻案 / 收窄的表述与执行口径的对照固定于 §4.0；下列各条按**执行口径**呈现，每条末行附对齐包处置。全程批次号与论证链见 SSOT 原文。

### 4.0 勘误对照表（原表述仅在此存档，均带勘误标注，不得作为结论引用）

| 原条（已翻案 / 已收窄表述，仅存档） | 对抗后执行口径 |
|------|----------------|
| S1「优先级倒置」 | 废弃「倒置」判定；改写为：用户向性能相对官方收尾句偏弱（见 S1） |
| S2「零祈使」 | 废弃；prompt 本有 `You MUST` 输出契约与 Budget 祈使列表；结构/分节/正反例缺口保留（见 S2） |
| S3 引申「无任何安全条文」 | 禁止引申；字面缺口（XSS / input validation / cleanup / caching 等）属实保留（见 S3） |
| S4「P3=style=官方违规」 | 废弃；官方未禁止低优先级 style finding，属混层判定；What-not 镜像缺失保留记为缺口（见 S4） |
| S5「无可测预算」 | 废弃；≤20 上限与运行时分钟预算客观存在；至多弱 nit「未写死『额度紧禁止 P3』类条文」（见 S5） |
| S6「非零规范」 | 保留（防夸大锚点，见 S6） |

### S1 · 用户向性能未对齐（原条已勘误改写，见 §4.0）

| | |
|--|--|
| **官方** | “Always prioritize security vulnerabilities and **performance issues that could impact users**.” — [Code reviewer](https://docs.github.com/en/copilot/tutorials/customization-library/custom-instructions/code-reviewer) / [customize-code-review 完整范例](https://docs.github.com/en/copilot/tutorials/customize-code-review) |
| **仓内（基线）** | `shard-review-prompt.md`：P1 = correctness / **missing error handling**；P2 = **performance** / maintainability。`BLOCKING_SEVERITIES={P0,P1}` → 缺错误处理可阻断；纯性能默认不阻断。 |
| **缺口（执行口径）** | 相对官方收尾句，用户向性能未进入本仓成文的优先 / 阻断语境（P1 条目未提性能），优先级表述偏弱。 |
| **对齐包处置** | 成文升格：Purpose 增官方镜像句 “Always prioritize security and user-impacting performance over style preferences”；Severity P1 兼含 user-impacting performance（latency / capacity / cost under real traffic）。`BLOCKING_SEVERITIES` 不改——写法尺子与门禁配置分列。 |

### S2 · 结构 / 分节 / 正反例缺口（原条已勘误收窄，见 §4.0）

| | |
|--|--|
| **官方** | 推荐 Purpose / Naming / Error / Security / Testing / Performance 等分节；10–20 短祈使；正反例；路径特化；checklist 锚定 — [customize-code-review](https://docs.github.com/en/copilot/tutorials/customize-code-review) |
| **仓内（基线）** | 分节为 Guidelines / Output / Self-Validation / Budget / Important；Guidelines 仅 4 个名词簇；无正反例；无路径特化文件；无指向仓内入口说明文档 / boundary / security checklist；Output+Budget 占主体。（prompt 本有 `You MUST` 输出契约与 Budget 祈使列表，故原条中「零祈使」表述已废弃，见 §4.0。） |
| **缺口（执行口径）** | 有成文文件，但不符合官方「可扫、可测、分层、正反例」的结构纪律。 |
| **对齐包处置** | 补 Purpose / Testing Guidelines / Review Style / Repository context / What not to include / Examples（正反例），扩充 Review Guidelines 为分主题条文 + Imperative checks；既有分节全保留；E1 结构契约测试锁定（必备分节存在且有序）。 |

### S3 · 应审内容缺口（保留；引申边界见 §4.0）

| | |
|--|--|
| **官方** | Security：XSS、input validation；Performance：resource cleanup、caching；Testing Standards；Review Style（actionable、explain why）；Repository context；敏感数据不落盘 — customize-code-review / optimize-code-reviews / Code reviewer |
| **仓内（基线）** | Security 无 XSS / 输入校验字面；Performance 无 cleanup / caching；全文无 Testing / Review Style / 仓上下文 / 幂等 / 敏感落盘脱敏；且 Scope 禁止追片外上下文。 |
| **缺口（执行口径）** | 官方清单多项未成文；Review Style 与输出「禁 prose / concise」同向削弱「讲 why」。 |
| **对齐包处置** | Review Guidelines 落 XSS / unsafe rendering、input validation、资源 cleanup、caching；secrets / tokens / PII 不落盘条文入 Review Guidelines 与 Repository context；新增 Testing Guidelines 与 Review Style；E2 必含词断言锁定。 |

### S4 · What-not 镜像缺失（原条已勘误收窄，见 §4.0）

| | |
|--|--|
| **官方** | 勿写模糊口号；完整范例与 Factory What Droid reviews 均强调优先硬缺陷、**跳过风格** — [What not to include](https://docs.github.com/en/copilot/tutorials/customize-code-review)；[What Droid reviews](https://docs.factory.ai/software-factory/code-review-ci) “skips stylistic concerns” |
| **仓内（基线）** | 无 deny-list / out-of-scope 分节；Budget 含 Prefer fewer / high-quality / Be precise 等软话。（P3 = style / suggestion 属官方允许的合规分级，原条对该分级的判定已废弃，见 §4.0。） |
| **缺口（执行口径）** | 官方「What not to include」在本仓成文无镜像，模糊口号与风格噪声通道未成文收窄。 |
| **对齐包处置** | 新增 What not to include 分节（模糊口号 / 改审查 UX / 无依据投机 / 纯风格重写 / 纯外链五类勿审）+ Examples 正反例；E2 禁词断言锁模糊口号不得裸回归（仅可作反例引用）。 |

### S5 · 额度软纪律（原条已勘误废弃误判，见 §4.0）

| | |
|--|--|
| **官方** | 有额度/上下文限制 → 指令须短、须优先、须可测；过长则淹没 — customize-code-review · Understanding how… / Keep instructions short |
| **仓内（基线）** | ≤20 findings 上限与 `SHARD_TIMEOUT_MINUTES` 运行时分钟预算客观存在；弱 nit 级缺口：未写死「额度紧禁止 P3」类条文，*if budget remains* 未量化。 |
| **缺口（执行口径）** | 相对「有额度才出规范」的纪律，成文对消耗焦点的约束偏软（杀进程硬顶属工程层，另账）。 |
| **对齐包处置** | 行为契约原文保留（≤20 / P0>P1 先扫 / `SHARD_TIMEOUT_MINUTES` / JSON 契约），E2 契约测试锁定；不扩大 BLOCKING、不改门禁。 |

### S6 · 成文口径（防夸大，保留）

| | |
|--|--|
| **裁定** | **有**成文面（`shard-review-prompt.md`），缺口是 **质量**，不是「零规范」。 |
| **已翻案说法** | 「没有审查规范」「强绑缺 `.factory/...` = 零成文」（结果对抗已打假），不得作为结论出现。 |
| **对齐包处置** | 全程贯彻：本次为既有成文面的结构与字面覆盖升格，不宣称「从无到有建规范」。 |

---

## 5. 已降出官方规则账（工程 / 产品另账）

下列在对抗中出现过，**结果对抗后不得再当「没守官方自定义规则」主条**：

| 项 | 降出理由（摘要） |
|----|------------------|
| docs-only 跳过 / SKIP | 触发策略；官方允许 skip 特定 PR；≠成文写法违规 |
| 不加载 CLAUDE/GEMINI/REVIEW | 官方为「存在则读」，非强制清单 |
| 非确定性→required check | 预期管理 ≠ 禁止 CI 按严重度门禁 |
| 无 droid-action 自动注入 / 双 skill | Factory **产品路径**对位，本仓 BYOM `droid exec -f` |
| overflow_warning 丢弃 / SHARD_MAX_LINES 断线 | workflow 接线工程债 |
| 大 diff 同信道 / heredoc 注入 | 工程/安全面；官方亦是指令+变更同上下文 |
| validate/repair/adjudicated、去重键 | 后处理工程债 |
| 25vs40 默认、Budget 运行时双份、Time budget「指错」 | 工程注释/拼装；成文引用 `SHARD_TIMEOUT_MINUTES` 本身成立 |
| JSON 输出契约 = 改 UX；severity→CI = 指令挡合并 | 尺子误用（自研产物形态 / 真闸在代码） |
| BASE-only 读规范 = 违规 | `pull_request_target` 安全取舍，非成文违规 |

---

## 6. 对抗过程（可追溯）

| 阶段 | 结果 |
|------|------|
| 代码对抗 ×4 波 | 结构/内容/勿写/入口/预算大量初报；第 4 波对代码面新问题 0 |
| 结果对抗 ×4 波 | KEEP 翻案、总表口径收窄、25vs40/Budget双份/Time「指错」降出；第 4 波对结果新问题 **0** |
| 勘误对抗 ×2 轮 | R1 打原定稿（STANDS_WITH_NITS）→ R2 复核（STANDS，翻案 0）；五处表述修正沉淀为 §4.0 对照表 |
| 定稿 | §4 执行口径 + §5 降出 |

---

## 7. 与总账其它车道的关系

- **流程四簇药**：另车道；本文件不授权改 concurrency / rollup / QG。  
- **PR 入口四组**（生产/预算/形态/人的通道）：上游供给；本文件只定「审查成文质量」缺口。  
- **closed→cancel**：工厂/交接规格车道，另账文档（未随本分支进仓）。  
- **落地规范正文**：对齐包（成文 A2 + 应审字面 + 用户向性能成文升格 + E1/E2 契约测试）已随 `feat/droid-review-prompt-alignment` 分支施工；流程四簇药 / PR 入口四组 / closed→cancel / Factory 入口对位仍另账，不在本包。

---

## 8. 官方链接速查

1. [Using custom instructions to unlock the power of Copilot code review](https://docs.github.com/en/copilot/tutorials/customize-code-review)  
2. [Code reviewer（Customization library）](https://docs.github.com/en/copilot/tutorials/customization-library/custom-instructions/code-reviewer)  
3. [Using GitHub Copilot code review](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/use-code-review)（自定义 instructions 段）  
4. [Build an optimized review process with Copilot](https://docs.github.com/en/copilot/tutorials/optimize-code-reviews)  
5. [GitHub Copilot code review（概念 / 内置 mix）](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/code-review)  
6. [Factory Automated Code Review](https://docs.factory.ai/software-factory/code-review-ci)（guidelines 入口 / What Droid reviews；产品对位参考）

---

## 9. 仓内靶子路径速查

- `.github/review/shard-review-prompt.md`  
- `src/infra_core/engine/droid_review/run_shard.sh`（拼 prompt / `droid exec -f`）  
- `src/infra_core/engine/droid_review/publish_findings.py`（`BLOCKING_SEVERITIES`）  
- `.github/workflows/droid-review.yml` / `droid-review-shards.yml`  
