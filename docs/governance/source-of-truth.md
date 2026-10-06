# 真源规则与分发副本登记（Source of Truth Registry）

**性质**：真源规则显式化 + 机械副本登记。**只登记，不改造**——不搬目录、不建新包、不拆文件、不统一词表。
**日期**：2026-09-21
**上游决策**：`memory/kb/decisions/architecture-layering-v3.md` 步骤 2（真源规则显式化 + 机械清单，规则先于清单）
**权威分层**：`docs/architecture.md`（四层体系：Evolution Core / Governance / Rule Packs / Delivery）。本文件的登记项按该模型归属，见 §5。
**位置说明**：本文件置于 `docs/governance/` 而非 `docs/` 顶层——文档分类守卫（`scripts/check_doc_classification.py`，由 CI 步骤与 `tests/test_doc_classification_guard.py` 双重执行）要求 `docs/` 下所有文件必须落在注册分类目录内，顶层仅豁免 `docs/README.md`、`docs/INDEX.md`、`docs/architecture.md`。

---

## 0. 背景：为什么需要显式化

1. **规则先于清单**：真源规则此前是隐含的，散落在注释与测试里，没有单一入口可读。任何人（或 agent）想改一个副本文件，必须先去读 `MANIFEST.sh` 注释、`action.yml` 注释和若干字节锁测试，才能拼出全貌。
2. **本仓最真实的跨层耦合不是 import，而是"分发副本"**：`substrate/gates` 与 `engine` 之间双向零 import，真正的耦合形态是"同一文件在多处各存一份"，靠测试锁住字节一致或判定等价。
3. **漂移有真实先例**：`actions/branch-cleanup/` 内容变更后未同步 bump 引用 pin，造成全量套件唯一红（origin/main PR #131 遗留，本仓 commit `0cece46` 修复）。这正是"真源改了、副本引用没跟上"的典型形态。

**范围声明**：本文件登记事实，不改事实。目录搬迁、包拆分、严重度词表统一均属 v3 步骤 4-5 或条件触发项，不在本次范围。

---

## 1. 真源规则（四要素）

### 规则 1：`src/` 为真源

`src/infra_core/` 是引擎与治理能力的**唯一真源**。其消费面有三：

| 消费面 | 形态 |
|---|---|
| pip 包 `infra_core` | `pip install git+https://…infraro-core.git@<ref>`，消费仓按 tag 安装 |
| 模块 CLI | `python -m infra_core.engine.*` / `python -m infra_core.governance` |
| 仓内 CI 与测试 | pytest 直接 import `infra_core.*` |

凡 `src/` 之外的同类文件（`actions/`、`scripts/`、`webhook-scripts/` 内的副本），均从 `src/` 复制或平铺分发而来，**不得成为独立演化线**。

### 规则 2：`actions/` 为分发副本

composite action 必须**自包含**：消费仓在任意 base 上使用 action 时，不能假设 `infra-core` 已安装，因此 action 目录内嵌脚本副本。

约束（三条同时成立）：

1. 内嵌副本与 `src/` 真源**字节一致**（或判定等价，见规则 3）；
2. 任何一侧改动必须**双侧同步**并经 PR 评审；
3. `action.yml` 引用的脚本必须位于 action 目录内（禁止 `../` 路径穿越；`tests/test_branch_cleanup_action_copies.py::TestBranchCleanupActionCopies::test_action_yml_references_scripts_within_action_dir` 锁定）。

`actions/droid-review-aggregate/action.yml:80-82` 的规则声明原文（**L81 为规则核心句**）：

```yaml
        # 自包含发布脚本（$GITHUB_ACTION_PATH 解析，禁止相对路径越出 action 目录）；
        # 与 src/infra_core/engine/droid_review/publish_findings.py 字节一致
        # （tests/test_droid_review_aggregate_action.py 锁定）。
```

### 规则 3：执行机制 = 字节锁测试 + 等价测试

真源规则不靠约定维持，靠**测试**维持。两类执行机制：

| 机制 | 语义 | 适用场景 |
|---|---|---|
| **字节锁测试** | 两侧 `read_bytes()` 相等，漂移即红 | 副本可逐字节一致时（5 对，见 §2.1） |
| **等价测试** | 两侧字节可不同（md5 不同），但**判定结果**在决策表上逐例相等 | 两侧存在不可避免的实现差异时（governance，见 §2.2） |

字节锁测试清单：

| 测试 | 锁定对象 |
|---|---|
| `tests/test_droid_review_aggregate_action.py::TestBundledPublishCopy::test_copy_matches_engine_copy` | `publish_findings.py` 双副本字节一致（另有 `test_copy_git_mode_matches_engine` 锁 git 文件模式） |
| `tests/test_branch_cleanup_action_copies.py::TestBranchCleanupActionCopies::test_action_copy_matches_src_copy` | branch-cleanup 三件套双副本字节一致（另有 `test_action_copy_git_mode_matches_src` 锁文件模式） |
| `tests/test_check_droid_review.py::TestScriptCopiesSync::test_two_copies_byte_identical` | `check_droid_review.sh` 双副本字节一致 |

等价测试清单：

| 测试 | 锁定对象 |
|---|---|
| `tests/test_governance.py::TestActionScriptEquivalence::test_equivalent_verdicts_on_decision_table` | `governance.py` 与 `governance_check.py` 在 11 条决策表用例上判定逐例一致（包内 `allowed` ↔ 脚本 exit code），并断言脚本无 Traceback |

### 规则 4：`webhook-scripts/MANIFEST.sh` 单文件平铺映射规则

`webhook-scripts/MANIFEST.sh` 是本仓 → 生产目录（`~/.factory/webhook`）同步的**唯一清单真源**。其中 `CROSS_DIR_MAPPINGS` 定义"仓库相对路径 → 部署目标文件名"的**单文件平铺**映射：源文件位于 `src/infra_core/engine/`，目标是把该**单个文件**平铺到生产目录根，**不复制包结构、不建子目录**。

为什么必须平铺：生产侧消费方以裸路径调用这些脚本与模块，依赖链（`extract_anchor.py` → `evolution_utils.py` / `evolution_adapters.py`）必须与调用方一同受管部署，否则出现 `ModuleNotFoundError`（INFRA-357 根因）。

源码引用——`webhook-scripts/MANIFEST.sh:62-81` 原文（血缘收敛注释 + 格式 + 映射数组）：

```bash
# 血缘收敛（INFRA-679）：M5 迁移时点曾在本仓 webhook-scripts/cross-dir/
# 维护 memory-core 生产血统的逐字节快照作为同步源，与 src/infra_core/engine/
# 的引擎演化线形成双副本（CODE_HYGIENE_DUPLICATE_BLOCK 重复块根因）。
# memory 侧回滚窗口四件套（evolution_{scanner,heartbeat,utils,adapters}.py）
# 随 memory #1097 删除、窗口按处置记录 §6 权威口径关闭后，本清单收敛到
# 引擎单源 src/infra_core/engine/——与 CI/CLI 的 python -m
# infra_core.engine.* 消费面同源，快照副本目录 cross-dir/ 随之退役。
# 引擎版本与旧快照在生产消费面（extract_linkback_anchor / sanitize_* /
# quarantine_corrupted_file / anchor gate 判定）逐字等价，差异仅为
# ruff 格式化与加性增强（INFRA-601 gh_repo_args、audit_layout P 档映射），
# 由 INFRA-679 PR 承载行为等价评审。
#
# 格式: "<仓库相对路径>:<部署目标文件名>"

CROSS_DIR_MAPPINGS=(
    "src/infra_core/engine/extract_anchor.py:extract_anchor.py"
    "src/infra_core/engine/evolution_utils.py:evolution_utils.py"
    "src/infra_core/engine/evolution_adapters.py:evolution_adapters.py"
    "src/infra_core/engine/anchor_gate.py:anchor_gate.py"
)
```

执行机制（该规则锁的是清单的**结构与源路径**，不是目标文件字节）：

| 机制 | 作用 |
|---|---|
| `webhook-scripts/sync-webhook-scripts.sh`（L106 `source "$MANIFEST"`） | 按清单执行正向同步 |
| `webhook-scripts/drift-gate.sh`（L158-162 读 `MANIFEST.sh`） | 反向巡检生产目录漂移 |
| `tests/test_webhook_scripts_manifest.py` | 契约测试：四件套不得缩水（`test_cross_dir_sources_exist`）、源路径必须解析到引擎单源 `src/infra_core/engine/`（`test_cross_dir_sources_use_engine_single_source`）、目标必须是平铺裸文件名（`test_cross_dir_targets_are_flat_names`）、与 `MANAGED_FILES` 不得双 claim（`test_no_double_claim_between_managed_and_cross_dir`）、`cross-dir/` 快照目录不得复活（`test_cross_dir_snapshot_directory_stays_retired`） |

---

## 2. 机械副本登记

### 2.1 字节重复副本（5 对）

全部为 git 跟踪文件，两侧字节一致（md5 实测于 2026-09-21，本机 `md5 -q`）：

| # | 文件 | 真源路径 | 分发副本路径 | md5（两侧一致） | 执行机制 |
|---|---|---|---|---|---|
| 1 | `branch_cleanup.sh` | `src/infra_core/shell/branch_cleanup.sh` | `actions/branch-cleanup/branch_cleanup.sh` | `6bf1cd60b34d6df1f8efe6c59336eb12` | `test_branch_cleanup_action_copies.py::TestBranchCleanupActionCopies::test_action_copy_matches_src_copy` |
| 2 | `branch_cleanup_issue.sh` | `src/infra_core/shell/branch_cleanup_issue.sh` | `actions/branch-cleanup/branch_cleanup_issue.sh` | `edc2a24dbe2696a94742c4f2d5370136` | 同上 |
| 3 | `branch_cleanup_retired.txt` | `src/infra_core/shell/branch_cleanup_retired.txt` | `actions/branch-cleanup/branch_cleanup_retired.txt` | `7a209d65e4f8b061621e8799a7e4a21f` | 同上 |
| 4 | `publish_findings.py` | `src/infra_core/engine/droid_review/publish_findings.py` | `actions/droid-review-aggregate/publish_findings.py` | `3191620e17c79ee1d6d1ea5189ee7255` | `test_droid_review_aggregate_action.py::TestBundledPublishCopy::test_copy_matches_engine_copy` |
| 5 | `check_droid_review.sh` | `src/infra_core/shell/check_droid_review.sh` | `scripts/check_droid_review.sh` | `497fc88bd459057a88dce6dd93d51ea8` | `test_check_droid_review.py::TestScriptCopiesSync::test_two_copies_byte_identical` |

说明：

- 第 1-4 对是"真源 → 分发副本"单向关系：`src/` 侧是真源，`actions/` 侧是自包含分发副本。
- 第 5 对（`check_droid_review.sh`）**无单向真源声明**，是**互等**关系：`src/infra_core/shell/` 侧随引擎包分发，`scripts/` 侧由 `ci.yml` 消费（测试 docstring 原话："Both shipped copies (scripts/ consumed by ci.yml, src/infra_core/shell/ shipped with the engine package) must uphold it and stay byte-identical"）。锁是双向字节相等，改动须双侧同步。
- 第 1-4 对另有 **git 文件模式锁**（脚本 `100755` / 清单 `100644`），防止 action 检出后权限漂移。
- 三对 branch-cleanup 副本的行为测试对象是 `src/` 侧（`tests/test_branch_cleanup*.py` 覆盖 `src/infra_core/shell/`），故 `src/` 侧改动必须连带跑行为测试。

**非副本澄清（防止误判为第 6 对）**：`scripts/droid_review/` 下的 `publish_findings.py`、`plan_shards.py`、`run_shard.sh` 是**委派壳（delegation shim）**，不是字节副本——它们 import 或 `exec` 引擎侧实现（`publish_findings.py` 仅 re-export，`run_shard.sh` 用 `exec bash` 转发）。其内容与同名引擎文件**本就不同**（md5 各异），不受任何字节锁约束，不应被"修"成副本。

### 2.2 等价复制（非字节）：`governance.py` ↔ `governance_check.py`

| 项 | 真源 | 分发副本 |
|---|---|---|
| 路径 | `src/infra_core/governance.py` | `actions/governance-check/governance_check.py` |
| md5（2026-09-21 实测） | `aeccbca158e72d52cb21583e5a157232` | `4ed0d056bfd06166c1de79b1675f6646` |

**md5 不同是预期状态**：action 内嵌脚本是自包含单文件（无包 import），真源是包内模块，两者实现必然有差异。因此**不能用字节锁**，改用**等价锁**：`tests/test_governance.py::TestActionScriptEquivalence::test_equivalent_verdicts_on_decision_table` 在 11 条决策表用例（含受保护/非受保护路径、owner/非 owner/空作者、自定义 patterns）上断言两个入口判定逐例一致（包内 `check_governance()` 的 `allowed` ↔ 脚本 exit code），并断言脚本无 Traceback。

**改动纪律**：任何一侧判定逻辑改动，必须同步另一侧并保证等价测试通过。另有三条命名契约测试约束副本的路径与能力面（`tests/test_naming_contract.py`：`action.yml` 必须引用 `$GITHUB_ACTION_PATH/governance_check.py`；副本必须支持 `scripts/evolution_*.py` / `scripts/` 整目录 / `.github/workflows/evolution-*.yml` / `.github/CODEOWNERS` 四类模式）。

### 2.3 MANIFEST 单文件平铺映射（4 条）

`webhook-scripts/MANIFEST.sh:76-81` 的 `CROSS_DIR_MAPPINGS` 四条（源 → 生产目录 `~/.factory/webhook` 平铺目标）：

| # | 源（真源，引擎单源） | 平铺目标 | 层级归属 |
|---|---|---|---|
| 1 | `src/infra_core/engine/extract_anchor.py` | `extract_anchor.py` | Delivery 补偿件（物理寄居 `engine/`） |
| 2 | `src/infra_core/engine/evolution_utils.py` | `evolution_utils.py` | Evolution Core |
| 3 | `src/infra_core/engine/evolution_adapters.py` | `evolution_adapters.py` | Evolution Core |
| 4 | `src/infra_core/engine/anchor_gate.py` | `anchor_gate.py` | Delivery 补偿件（物理寄居 `engine/`） |

约束：清单源路径**必须**解析到 `src/infra_core/engine/`（INFRA-679 血缘收敛后的引擎单源），`webhook-scripts/cross-dir/` 逐字节快照目录已退役且不得复活；目标必须是平铺裸文件名（无目录前缀）。

### 2.4 `shard-review-prompt.md`：消费仓第三副本（当前无活消费者）

`droid-review` 分片流水线的 prompt 模板 `.github/review/shard-review-prompt.md`（md5 `123dc4011b6232aa7df522ceff86aea2`，2643 字节）是**唯一一份没有机械锁的分发件**，登记如下：

| 副本 | 位置 | 消费方式 | 现状 |
|---|---|---|---|
| 引擎仓副本 | `infraro-core/.github/review/shard-review-prompt.md` | `src/infra_core/engine/droid_review/run_shard.sh:135` 从 **BASE checkout 的 CWD** 读取（`PROMPT_TEMPLATE="$(cat .github/review/shard-review-prompt.md)"`） | 随本仓版本走 |
| 消费仓第三副本 | 各消费仓 `<consumer>/.github/review/shard-review-prompt.md`；实测样例 `~/memory/.github/review/shard-review-prompt.md`（git 跟踪，md5 与引擎仓一致，2643 字节） | 同上——`run_shard.sh` 读的是**消费仓工作区**里的那份 | 与引擎仓当前字节一致，但**无任何测试锁** |
| 运行快照 | 消费仓 `artifacts/runs/*/.github/review/shard-review-prompt.md` | 历史 run 归档 | 非分发面，不登记为副本 |

**「消费仓第三副本」含义**：流水线实际读取的是**消费仓本地副本**（同一文件在生态内的第三个存放点：引擎仓 / 消费仓工作区 / 运行快照）。引擎仓与消费仓之间没有同步机制，也没有漂移检测。

**「当前无活消费者」**：`infraro-core` 当前活消费仓数 = 0（v3 事实基线：`~/memory` 的 `droid-review.yml:50,93` 引用的是**已归档**的 `hdot123-org / infra-core@v0.15.2`，不消费本仓）。因此该第三副本**当前无活消费者**，漂移风险为零，本次只登记不处理。

**待决**：若 v3 步骤 4 决定把 `~/memory` 接回 `infraro-core`，必须先定 prompt 第三副本的 drift 机制（二选一：进 `MANIFEST` 平铺清单，或 `droid-review-shards.yml` 改从 `engine/` 读），否则消费仓会带着一份无锁副本上线。

---

## 3. 严重度词表登记（3 套，只登记不统一）

本仓存在 3 套互不相同的严重度词表，各自服务不同的产出面：

| # | 词表 | 定义位置（file:line） | 取值 | 产出面 |
|---|---|---|---|---|
| 1 | droid-review findings | `actions/droid-review-aggregate/publish_findings.py:23`（真源 `src/infra_core/engine/droid_review/publish_findings.py:23`，`VALID_SEVERITIES`） | `P0` / `P1` / `P2` / `P3` | PR 审查 findings（prompt 模板 `.github/review/shard-review-prompt.md` 同款声明 P0-P3） |
| 2 | evolution scanner findings | `src/infra_core/engine/evolution_scanner.py:257`（`_valid_severity()`） | `critical` / `warning` / `info`（非法值归一为 `info`） | 审计 pack 输出经 scanner 归一后的 `.evolution` findings |
| 3 | memory layout audit | `src/infra_core/packs/memory/layout_audit.py:55`（`Finding.severity`） | `P0` / `P1` / `P2`（无 P3） | 仓库布局审计 findings |

**明确：本次只登记，不统一。** 理由与边界：

- 三套词表的消费面互不相交（PR 审查 / 引擎扫描 / 布局审计），统一会改动消费方解析逻辑与既有 findings 语义，属**行为变更**，不是文档动作。
- 统一是**条件触发项**：v3 步骤 6 规定"协作者 >1 或消费仓接回时"才执行统一，并需先指定 owner。
- 词表 #1 与 #3 的 `P0/P1/P2` 前缀相同但语义域不同（前者含 P3、面向代码审查；后者无 P3、面向目录布局），不可假定可互换。

---

## 4. 已知漂移登记（只登记不修）

### 4.1 `docs/onboarding/templates/branch-cleanup.thin-caller.yml:126` pin 过期

| 项 | 值 |
|---|---|
| 位置 | `docs/onboarding/templates/branch-cleanup.thin-caller.yml:126` |
| 内容 | `uses: hdot123/infraro-core/actions/branch-cleanup@3695445b1d8df8997de35d39256ffe596d229c8c` |
| 本仓对照 | `.github/workflows/branch-cleanup.yml:138` 已 bump 至 `04af200deaa62154d64b545e58788aa7c6e053c4`（注释 `post-v0.18.9 (PR #131；与 origin/main 内容等价)`） |
| 性质 | **内容已漂移**：`3695445b` 与 `04af200` 之间 `actions/branch-cleanup/` 有内容变更（origin/main PR #131：`branch_cleanup_issue.sh` +126/-16） |
| 为什么测试没红 | `tests/test_uses_sha_pinning_contract.py` 的 `SCAN_DIRS` 只含 `.github/workflows`、`.github/actions`、`actions`，**不扫 `docs/`**，该 pin 不在测试扫描面内 |
| 影响 | 消费仓按此 onboarding 模板落地时，会**带入过期 pin**（首次接入即落后于真源内容），且不会被任何测试发现 |
| 处置 | **只登记不修**。模板 pin 属 v3 步骤 4「消费仓接回」范围：修模板需同时决定"模板 pin 是否纳入新鲜度扫描面"，否则修完仍会再次漂移 |

（来源：F2 worker 在 branch-cleanup pin bump 时发现的同款遗留，本文件仅登记；未做任何修改。）

---

## 5. 与四层模型的关系

本文件的登记项按 `docs/architecture.md` 的四层体系归属（单向依赖原则：`Delivery → Core` 允许，`Core → Delivery` 禁止）：

| 登记项 | 真源所在层 | 副本所在层 |
|---|---|---|
| 字节副本 1-3（branch-cleanup 三件） | `src/infra_core/shell/`（Delivery） | `actions/branch-cleanup/`（Delivery） |
| 字节副本 4（`publish_findings.py`） | `src/infra_core/engine/droid_review/`（**物理寄居 `engine/` 的 Delivery 组件**） | `actions/droid-review-aggregate/`（Delivery） |
| 字节副本 5（`check_droid_review.sh`） | `src/infra_core/shell/`（Delivery） | `scripts/`（Delivery） |
| 等价复制（`governance.py` ↔ `governance_check.py`） | `src/infra_core/governance.py`（Governance） | `actions/governance-check/`（Delivery） |
| MANIFEST 4 条平铺映射 | `src/infra_core/engine/evolution_utils.py`、`evolution_adapters.py`（Evolution Core）；`extract_anchor.py`、`anchor_gate.py`（**物理寄居 `engine/` 的 Delivery 组件**） | 生产目录 `~/.factory/webhook`（Delivery） |
| `shard-review-prompt.md` | `.github/review/`（Delivery） | 消费仓 `.github/review/`（Delivery） |
| 3 套严重度词表 | `publish_findings.py`（Delivery）；`evolution_scanner.py`（Evolution Core）；`packs/memory/layout_audit.py`（Rule Packs） | —（各自单点定义，无副本） |

要点：

- "物理位置"与"逻辑归属"解耦：`engine/droid_review/`、`engine/anchor_gate.py`、`engine/extract_anchor.py` 物理留在 `engine/` 下，但逻辑归 Delivery。**搬走它们是净负收益**（会撕裂 suppress 指纹、gate0 豁免、消费锚定与字节锁），故只登记不搬迁。
- 反向依赖红线：Evolution Core 的 `evolution_*` 核心模块不得 import `droid_review` / `anchor_gate` / `extract_anchor`。
- 本文件与 `docs/architecture.md` 互为引用：architecture.md 给层级与路径清单，本文件给"同一文件在多处各存一份"的机械登记与锁。

---

## 6. 复核方式

登记项可机械复核（全部本地、无网络）：

```bash
# 2.1 五对字节副本：两侧 md5 应一致
for pair in \
  "src/infra_core/shell/branch_cleanup.sh:actions/branch-cleanup/branch_cleanup.sh" \
  "src/infra_core/shell/branch_cleanup_issue.sh:actions/branch-cleanup/branch_cleanup_issue.sh" \
  "src/infra_core/shell/branch_cleanup_retired.txt:actions/branch-cleanup/branch_cleanup_retired.txt" \
  "src/infra_core/engine/droid_review/publish_findings.py:actions/droid-review-aggregate/publish_findings.py" \
  "src/infra_core/shell/check_droid_review.sh:scripts/check_droid_review.sh"; do
  a="${pair%%:*}"; b="${pair##*:}"
  [ "$(md5 -q "$a")" = "$(md5 -q "$b")" ] && echo "OK  $a" || echo "DRIFT $a"
done

# 2.1 / 2.2 字节锁与等价锁
.venv/bin/pytest tests/test_droid_review_aggregate_action.py tests/test_branch_cleanup_action_copies.py \
  tests/test_check_droid_review.py::TestScriptCopiesSync -q --no-header
.venv/bin/pytest tests/test_governance.py::TestActionScriptEquivalence -q --no-header

# 2.3 MANIFEST 映射契约
.venv/bin/pytest tests/test_webhook_scripts_manifest.py -q --no-header

# 3 严重度词表位置复核
rg -n 'VALID_SEVERITIES|_valid_severity|severity: str' \
  src/infra_core/engine/droid_review/publish_findings.py \
  src/infra_core/engine/evolution_scanner.py \
  src/infra_core/packs/memory/layout_audit.py

# 4.1 漂移登记复核（预期模板侧仍为 3695445b、workflow 侧为 04af200）
rg -n 'branch-cleanup@' docs/onboarding/templates/branch-cleanup.thin-caller.yml .github/workflows/branch-cleanup.yml

# 文档分类守卫（本文件所在目录必须在注册分类内）
python3 scripts/check_doc_classification.py
```
