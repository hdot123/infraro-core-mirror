# 消费仓接回方案决策（`~/memory` → 引擎仓 `v0.18.9`）

> **文档性质：决策建议，最终裁决权在用户；实施另立项。**（本方案不实施迁移）
> 本文只回答「怎么接、先决条件是什么、哪些地方会碎、风险怎么排」，不落任何代码改动。
>
> - **范围**：消费仓 `~/memory`（调查基线 HEAD `6d5833a`）从旧引擎仓 tag `v0.15.2`（该仓已归档）接回本仓 `hdot123/infraro-core` tag `v0.18.9`（实测 `f041789`）。
> - **纪律**：文中全部关键数字由本 worker 于 2026-09-21 实测复核（命令与输出见 §8），不转抄调查报告；与调查结论有出入处以实测值为准。
> - **旧仓名写法**：全文旧仓名一律间隔写法（`hdot123-org` 与 `infra-core` 两段分开书写），连写形态会被本仓守卫 `tests/test_no_old_repo_references.py` 零容忍拦截。
> - **登记位置**：`docs/governance/`（已注册分类目录，`scripts/check_doc_classification.py` 覆盖）。

---

## 0. 结论摘要

| 决策项 | 建议 | 依据 |
|---|---|---|
| 交付形态 | **单 PR**（15 个文件，一次 squash） | 迁移面同源、断言与 pin 必须齐步走，拆 PR 会造出中间态红灯 |
| 硬前置（P0） | **合并前**给消费仓设置 repo variable `ENGINE_CONSUMERS=authorized` | 新引擎 droid-review / auto-merge 入口带授权门，未授权 fail-loud（§2） |
| runner（P1） | **首版显式传** `runner: '["self-hosted", "pve-linux"]'`，保持行为等价 | 旧 reusable 硬编码自建 runner，新默认 `ubuntu-latest`，不传即静默换执行环境（§3） |
| 版本齐步 | 10 处 `uses` tag + `pyproject.toml` pin + `uv.lock` relock 全部 `v0.18.9`，同一 PR 三写 | 消费仓自带版本锁测试强制三写一致（§1、§6） |
| 兼容性 | inputs / secrets / action 路径 / 包名全兼容；secrets 零新增必需；prompt 第三副本当前零 drift（但**无锁**，§4.4） | §4 逐项实测 |
| 风险最高项 | P0 variable 缺失 → droid-review + auto-merge 秒级全红（fail-loud，可见即修，无状态损坏） | §2、§6 |

---

## 1. 迁移面：15 个实质 pin 点清单

**本清单共 15 个实质 pin 点**（编号 1-15，逐点给出文件:行 + 当前值 + 迁移后值）。

**表记约定**（规避本仓连写守卫，非笔误）：

- `⟨旧仓⟩` = 旧引擎仓（`hdot123-org` 下的 `infra-core`），引用 tag 当前 `v0.15.2`；
- `⟨新仓⟩` = 本仓 `hdot123/infraro-core`，迁移后引用 tag `v0.18.9`（实施时以当时最新 tag 为准，本方案以 v0.18.9 为基线）。

### 1.1 A 类：workflow `uses` 引用（10 处，8 个文件）

| # | 文件:行 | 当前值（语义） | 迁移后值 |
|---|---|---|---|
| 1 | `.github/workflows/droid-review.yml:50` | `⟨旧仓⟩/.github/workflows/droid-review-shards.yml@v0.15.2`（PR 审查分片流水线 reusable） | `⟨新仓⟩/.github/workflows/droid-review-shards.yml@v0.18.9` |
| 2 | `.github/workflows/droid-review.yml:93` | `⟨旧仓⟩/actions/droid-review-aggregate@v0.15.2`（聚合发布 composite，check 名 `droid-review` 承载体） | `⟨新仓⟩/actions/droid-review-aggregate@v0.18.9` |
| 3 | `.github/workflows/evolution-governance.yml:25` | `⟨旧仓⟩/actions/governance-check@v0.15.2`（受保护路径治理判定 composite） | `⟨新仓⟩/actions/governance-check@v0.18.9` |
| 4 | `.github/workflows/evolution-heartbeat.yml:32` | `⟨旧仓⟩/.github/workflows/evolution-heartbeat.yml@v0.15.2`（心跳 reusable） | `⟨新仓⟩/.github/workflows/evolution-heartbeat.yml@v0.18.9` |
| 5 | `.github/workflows/evolution-scan.yml:40` | `⟨旧仓⟩/.github/workflows/evolution-scan.yml@v0.15.2`（扫描 reusable） | `⟨新仓⟩/.github/workflows/evolution-scan.yml@v0.18.9` |
| 6 | `.github/workflows/setup-labels.yml:16` | `⟨旧仓⟩/.github/workflows/setup-labels.yml@v0.15.2`（标签初始化 reusable） | `⟨新仓⟩/.github/workflows/setup-labels.yml@v0.18.9` |
| 7 | `.github/workflows/branch-cleanup.yml:42` | `⟨旧仓⟩/actions/branch-cleanup@v0.15.2`（分支清理 composite） | `⟨新仓⟩/actions/branch-cleanup@v0.18.9` |
| 8 | `.github/workflows/auto-merge.yml:70` | `⟨旧仓⟩/.github/workflows/auto-merge-pipeline.yml@v0.15.2`（自动合并 reusable） | `⟨新仓⟩/.github/workflows/auto-merge-pipeline.yml@v0.18.9` |
| 9 | `.github/workflows/droid-review-watchdog.yml:74` | `⟨旧仓⟩/.github/workflows/droid-review-watchdog-handlers.yml@v0.15.2`（watchdog `self-heal-rerun`） | `⟨新仓⟩/.github/workflows/droid-review-watchdog-handlers.yml@v0.18.9` |
| 10 | `.github/workflows/droid-review-watchdog.yml:98` | 同上文件（watchdog `cancel-on-ci-fail`） | 同上迁移后值 |

### 1.2 B 类：打包 pin（2 处）

| # | 文件:行 | 当前值 | 迁移后值 |
|---|---|---|---|
| 11 | `pyproject.toml:15` | `"infra-core @ git+https://github.com/⟨旧仓⟩.git@v0.15.2"` | `"infra-core @ git+https://github.com/hdot123/infraro-core.git@v0.18.9"`（**包名 `infra-core` 不变**，仅改 URL 与 tag） |
| 12 | `uv.lock:310` 与 `uv.lock:469` | `source = { git = "…⟨旧仓⟩…?rev=v0.15.2#4954b34…" }` 与依赖条目 `rev=v0.15.2` | 改 `pyproject.toml` 后 `uv lock` 重新锁定（`uv.lock` 由工具生成，不手改） |

> `uv.lock` 两行必须与 `pyproject.toml` 同 PR 变更；relock 后 `uv sync --frozen --extra dev` 必须通过。

### 1.3 C 类：测试断言面（3 个文件，17 处字面 + 2 处隐式）

| # | 文件:行 | 当前形态 | 迁移后值 / 动作 |
|---|---|---|---|
| 13 | `tests/test_ci_config.py`（12 处字面：`:115`、`:145`、`:210`、`:268`、`:373`、`:454`、`:527`、`:575`、`:884`（docstring）、`:898`、`:1140`、`:1315`） | 精确字符串 / 前缀断言，全部钉 `⟨旧仓⟩/…@v0.15.2` 形态（`:898`、`:1315` 为 `actions/` 前缀断言） | 全部替换为新仓路径 + `@v0.18.9`；`:884` docstring 同步 |
| 13b | `tests/test_ci_config.py:670` 与 `:720`（**隐式耦合，调查未列**） | `_collect_org_uses()` 过滤 `uses.startswith("hdot123-org/")`；探针 URL 拼 `https://github.com/hdot123-org/{repo}/archive/{ref}.tar.gz` | 必须同步改为新 org 前缀 `hdot123/`，否则收集器返回空 → `_org_uses_refs()` 断言「uses 引用不可能为空」直接红（即使 12 处字面全改也仍红） |
| 13c | `tests/test_ci_config.py:604-628`（版本锁 `test_thin_caller_tags_match_engine_pin`） | 正则从 `pyproject.toml` 解析 `infra-core @ git+…@vX.Y.Z`，要求 scan/heartbeat 的 `uses` tag 同版本 | **无需改代码**：正则锚定包名 `infra-core`（不变），改 URL/tag 后自然通过；但强制「pyproject + 两处 uses 同 PR 齐步」 |
| 14 | `tests/test_governance_rename.py:148` | 断言 governance 步骤 `uses` 含 `⟨旧仓⟩/actions/governance-check` | 改为新仓路径（前缀断言） |
| 15 | `tests/test_pr_merged_verification.py:507`、`:521`、`:529`、`:542` | 跨仓 `gh pr view --repo ⟨旧仓⟩` 的 mock fixture（4 处，示例用仓名） | **可选（P3）**：mock 语义不依赖真实仓存在，不迁移也不红；建议一并改为新仓名，保持生态事实一致 |

### 1.4 D 类：文档引用（可选，不影响 CI）

| 位置 | 内容 | 建议 |
|---|---|---|
| `README.md:380` | thin caller 总述段：执行体由 `⟨旧仓⟩` 的 reusable workflows 与 composite actions 承载，当前 pin v0.15.2 | 同 PR 更新为新仓名 + `v0.18.9`（接回指南链接改指新仓 `docs/onboarding/consumer-onboarding.md`） |
| `README.md:406` | 分支清理段：`uses: ⟨旧仓⟩/actions/branch-cleanup`，tag pin 当前 v0.15.2 | 同上 |
| `AGENTS.md:196` | 「Scanner 执行体在 `⟨旧仓⟩`」 | 同上 |

> D 类不计入 15 个实质 pin 点（它们不参与任何机械断言）；但接回 PR 建议一并改，避免文档与事实漂移。

**实测口径（与调查报告的出入）**：调查报告记「~13 处测试断言」；本 worker 精确计数为 **17 处字面 + 2 处隐式 org 前缀耦合**（逐文件：`test_ci_config.py` 12、`test_pr_merged_verification.py` 4、`test_governance_rename.py` 1；另有 `:670`/`:720` 两处不含字面但必碎）。**以实测为准。**

---

## 2. P0 硬前置：`ENGINE_CONSUMERS` repo variable

### 2.1 事实（实测）

- 新引擎的管线入口带**授权门 job**，读消费仓 repo variable 判定授权：
  - `.github/workflows/droid-review-shards.yml:127`（job `authorization-gate`，`droid-review.yml:50` 的 callee）
  - `.github/workflows/auto-merge-pipeline.yml:71`（job `authorization-gate`，`auto-merge.yml:70` 的 callee）
  - 判定源 `VARS_VALUE: ${{ vars.ENGINE_CONSUMERS || '' }}`；未授权 / 读取失败 → **fail-loud**（`未授权消费引擎，走授权流程`），下游 job 因 `needs` 不启动；引擎仓自身豁免。
- 消费仓现状：`gh variable list -R hdot123-org/memory` → 12 个变量（`BRANCH_AGE_*`、`DROID_REVIEW_TIMEOUT_MINUTES`、`LINEAR_PROJECT_MEMORY_CORE_ID`、`QUOTA_*`、`SHARD_*`、`WATCHDOG_MAX_ATTEMPT`），**无 `ENGINE_CONSUMERS`**（实测输出见 §8）。
- 影响面精确化：**只有 droid-review 与 auto-merge 两个 reusable 内置授权门**。`branch-cleanup` 的授权门在**消费仓 caller 模板侧**（`docs/onboarding/templates/branch-cleanup.thin-caller.yml:38-66`），而 `actions/branch-cleanup/action.yml` 本体不含门 → memory 现有 caller 不含该 job，**不阻塞**（见 §6 P3 可选项）。

### 2.2 设置命令与时机

```bash
# 设置（合并前，一次性）：
gh variable set ENGINE_CONSUMERS --body authorized -R hdot123-org/memory

# 验证：
gh variable list -R hdot123-org/memory | grep '^ENGINE_CONSUMERS'   # 期望：ENGINE_CONSUMERS	authorized	<时间戳>
```

**时机（精确）**：**在把 `v0.18.9` pin 合并进 `main` 之前**设置。

- 迁移 PR 自身安全：`pull_request_target` 类事件按 **base(main) 的旧定义**执行，PR 打开期间跑的仍是旧 reusable（无门）。
- 一旦合并：`main` 上的 `droid-review.yml` / `auto-merge.yml` 引用新 callee，**下一个 PR 的 droid-review 与 auto-merge 立即撞门**。
- 先设 variable 成本为零、可逆（`gh variable delete ENGINE_CONSUMERS -R hdot123-org/memory` 即回退），故建议**在 PR 打开时就设**，不要卡在合并窗口。

**失败模式与恢复**：若漏设，两 workflow 在授权门 job 秒级失败（下游零启动，**不产生部分状态**）；补设 variable 后重跑即恢复。属于「响亮失败」而非静默损坏。

**验收点**：迁移后首个 PR 的 droid-review run 中，`authorization-gate` job 日志出现 `Authorization gate: hdot123-org/memory authorized（ENGINE_CONSUMERS=authorized @vars）`。

---

## 3. P1 决策点：scan / heartbeat 的 runner

### 3.1 事实（实测，两侧原始文件）

| 侧 | 位置 | 实测内容 |
|---|---|---|
| 旧（`v0.15.2`） | `evolution-scan.yml` / `evolution-heartbeat.yml` 的 job | `runs-on: [self-hosted, pve-linux]`（硬编码，无 input） |
| 新（`v0.18.9`） | 同名 reusable 的 job | `runs-on: ${{ fromJSON(inputs.runner || '"ubuntu-latest"') }}`；input `runner`：`required: false`、`type: string`、`default: '"ubuntu-latest"'` |

即：**不显式传 `runner` 时，scan/heartbeat 的执行环境会静默从自建 runner 翻转到 GitHub-hosted**。memory 当前两个 caller 都**没有** `runner` input（实测 `with:` 块只有 secrets 转发）→ 默认即触发翻转。

### 3.2 两案对比

| 维度 | 方案 A：显式传（建议） | 方案 B：接受 hosted 默认 |
|---|---|---|
| 调用侧写法 | `with: runner: '["self-hosted", "pve-linux"]'`（JSON 字符串形态，`fromJSON` 解析） | 不写 `runner`（现状调用面零改动） |
| 行为等价性 | **与迁移前逐项等价**（执行环境、工具链、缓存路径、出网拓扑不变） | 执行环境变更：pip 安装走公网、cache 路径变更、出网拓扑变更 |
| 变更面 | 迁移 PR 内 +2 行（两个 caller） | 迁移 PR 内 0 行（但引入一次未隔离的行为变更） |
| 失败模式 | 无新增（沿用已验证环境） | 需重新验证：hosted 无 `droid`/工具链预装、`~/.memory-core` 索引缺失、cron 双窗（`13,43`）与 hosted 并发额度交互 |
| 可回退性 | 删掉 input 即切 hosted（单行） | 加回 input 即切自建（单行） |
| 风险归属 | 把「版本接回」与「执行环境迁移」两件事解耦 | 两件事耦合进同一 PR，出问题难定位 |

### 3.3 建议

**采纳方案 A（首版显式传 `runner: '["self-hosted", "pve-linux"]'`）**，理由：

1. **行为等价是接回的第一原则**：本 PR 的目标是「换仓库引用」，不是「换执行环境」。runner 翻转是独立的行为变更，应独立评估、独立立项。
2. **风险隔离**：等价迁移下，出问题只可能来自版本跨度（可控、已逐项核对）；若叠加 hosted 迁移，故障归因面翻倍。
3. **可逆**：后续评估通过后，删除该 input 即完成 hosted 切换，成本一行。
4. **既有自建 runner 面仍然存在**：memory 的 `droid-review.yml` 聚合 job 仍在 caller 侧 `runs-on: [self-hosted, pve-linux]`（实测），自建能力不因本方案消失。

> **注意 JSON 形态**：input 值是**字符串**，需写成 `'["self-hosted", "pve-linux"]'`（含引号的 JSON），不能写成 YAML 数组字面量——callee 侧是 `fromJSON(inputs.runner || '"ubuntu-latest"')`。

---

## 4. 兼容性结论（逐项实测）

### 4.1 `workflow_call` inputs：全兼容

| caller | 实传键（实测） | 新 callee 声明（实测） | 结论 |
|---|---|---|---|
| `evolution-scan.yml` | 无 `with:`（仅 secrets） | `engine_ref`（默认空）、`runner`（默认 hosted） | ✅ 兼容；`runner` 见 §3 决策 |
| `evolution-heartbeat.yml` | 无 `with:` | `engine_ref`、`scanner_workflow`（默认 `'evolution-scan.yml'`）、`runner` | ✅ 兼容；`scanner_workflow` 默认值恰好匹配 memory 的 caller 文件名 `evolution-scan.yml`，**无需传** |
| `droid-review.yml`（shards） | `pr_number`、`head_sha`、`shard_max_files`、`shard_max_count`、`shard_timeout_minutes`、`shard_max_parallel` | 同名 snake 六键（+`engine_ref`） | ✅ 逐键同名；新 callee 已删 hyphen 变体，memory 已是 snake 单形态 → 无「多余键」startup_failure 风险 |
| `droid-review-watchdog.yml` | `mode`、`run_id`、`run_attempt`、`head_sha`、`max_attempt` | 同名五键（`mode`/`run_id`/`run_attempt` 必填，`head_sha`/`max_attempt` 选填） | ✅ 兼容 |
| `auto-merge.yml` | 无 `with:`（仅 `dispatch_token`） | 仅 `secrets.dispatch_token`（required） | ✅ 兼容 |
| `setup-labels.yml` | 无 `with:` | `labels_json`（选填，默认空 → 引擎默认标签集） | ✅ 兼容 |

### 4.2 secrets：零新增必需（一处对齐项）

| caller | 实传（实测） | 新 callee 声明 | 结论 |
|---|---|---|---|
| `evolution-scan.yml` | `dispatch_token`、`linear_api_key` | `dispatch_token`（required）、`linear_api_key`（optional） | ✅ |
| `evolution-heartbeat.yml` | `dispatch_token` | `dispatch_token`（required） | ✅ |
| `auto-merge.yml` | `dispatch_token` | `dispatch_token`（required） | ✅ |
| `droid-review.yml` | `FACTORY_API_KEY`、`NVIDIA_KONG_PROXY_KEY` | `factory_api_key`（required）、`nvidia_kong_proxy_key`（optional）、`lumivane_cfat`（optional） | ✅ 必需集未扩张；**键名大小写见下** |

- **零新增必需 secret**：新 callee 相对旧版仅多出 `lumivane_cfat`，且为 optional（不传即空）。memory 已有 `DISPATCH_TOKEN` / `LINEAR_API_KEY` / `FACTORY_API_KEY` / `NVIDIA_KONG_PROXY_KEY`，无需新建任何 secret。
- **待对齐项（P2，低风险）**：memory caller 的 `secrets:` **映射键**是大写形态（`FACTORY_API_KEY:`），新 callee 声明为小写 snake（`factory_api_key`）。引擎侧契约测试明确断言「GHA secrets 上下文大小写不敏感，仅声明/转发层 snake 化」（`tests/test_droid_review_shards_workflow.py:84-96`，该测试同时强制声明键必须 snake 小写）。**建议**：迁移 PR 顺手把 caller 映射键改为小写形态（值表达式仍取 `secrets.FACTORY_API_KEY`，零语义变化），消除歧义；并在首次 droid-review run 复核 `review-shard` job 正常取到凭证。

### 4.3 action 路径与包名：不变

- 三个 composite action 的**路径与名称不变**：`actions/droid-review-aggregate`、`actions/governance-check`、`actions/branch-cleanup`；inputs 逐键核对一致（`governance-check`：`owner-login`/`protected-patterns`/`github-token`；`branch-cleanup`：`mode`/`trigger-branch`/`dispatch-token`/`branch-age-*-hours`/`linear-api-key`/`linear-project-id`；`droid-review-aggregate`：`github-token`/`pr-number`/`head-sha`/`docs-only`/`shards`/`shards-result`）。
- **包名不变**：分发名 `infra-core`、import 名 `infra_core`（新仓 `pyproject.toml:6` 实测 `name = "infra-core"`、`version = "0.18.9"`）→ 消费仓只需改 URL/tag；代码里的 `import infra_core…` 零改动。
- **引擎安装源已切换**：新 reusable 的安装步为 `pip install "git+https://github.com/hdot123/infraro-core.git@${ENGINE_REF}"`（`evolution-scan.yml:124`、`evolution-heartbeat.yml:116`）→ 消费仓无需自备安装源。

### 4.4 prompt 第三副本：当前零 drift，但**无锁**

- 实测两侧 md5 一致：引擎仓 `.github/review/shard-review-prompt.md` 与消费仓 `~/memory` 的 `.github/review/shard-review-prompt.md` 均为 `123dc4011b6232aa7df522ceff86aea2`，2643 字节（git 跟踪）。
- **但该副本没有任何机械锁**（本仓真源登记 `docs/governance/source-of-truth.md` §2.4 已登记为「消费仓第三副本（当前无活消费者）」）。流水线读取的是**消费仓工作区**里的那份（`run_shard.sh:135` 从 BASE checkout 的 CWD 读）。
- **接回前必须定 drift 机制**（二选一，属实施立项范围）：
  1. 把该文件纳入 `MANIFEST` 平铺清单（走既有副本同步/漂移检查机制）；或
  2. `droid-review-shards.yml` 改为从 `engine/` checkout 读取（彻底取消消费仓副本）。
- 本方案不改任何一侧（不实施）；仅声明：**若不定机制就接回，等于上线一份无锁副本**——这与真源登记的结论一致（无矛盾）。

### 4.5 外围链路：不受影响

- **webhook 生产脚本已指向新仓**：`~/.factory/webhook/scripts/poll-releases.sh:25`（`REPO="hdot123/infraro-core"`）、`reconcile-evolution.sh:23`（同）。迁移 PR 只动 `~/memory` 内文件，与生产链路无耦合。
- **launchd**：`com.busiji.infra-drift-gate.plist` 的 `--repo-root` 指向本机 `~/infra-core` 目录（本地 checkout 路径），与消费仓 pin 面无关，不受影响。
- **消费仓 CI**：`~/memory` 的 `.github/workflows/ci.yml` 不含任何引擎 reusable/action 引用（实测仅 `actions/checkout` + 仓内本地 action）→ 迁移 PR 的 CI 只受 `tests/` 断言面影响（§1.3 已列全）。
- **权限面**：caller 授予集（shards：`contents: read`/`actions: write`/`id-token: write`/`pull-requests: read`）覆盖新 callee 各 job 声明（`contents: read`、`pull-requests: read`、`actions: write`）→ 无缺口（对齐接入指南 §7 权限同步守则）。

---

## 5. 迁移 PR 文件清单（15 个文件）

| 文件 | 动作 |
|---|---|
| `.github/workflows/droid-review.yml` | 2 处 `uses` 改名 + tag（#1/#2） |
| `.github/workflows/evolution-governance.yml` | 1 处（#3） |
| `.github/workflows/evolution-heartbeat.yml` | 1 处（#4）+ `runner` 显式传（§3） |
| `.github/workflows/evolution-scan.yml` | 1 处（#5）+ `runner` 显式传（§3） |
| `.github/workflows/setup-labels.yml` | 1 处（#6） |
| `.github/workflows/branch-cleanup.yml` | 1 处（#7） |
| `.github/workflows/auto-merge.yml` | 1 处（#8） |
| `.github/workflows/droid-review-watchdog.yml` | 2 处（#9/#10） |
| `pyproject.toml` | pin URL + tag（#11） |
| `uv.lock` | relock（#12，`uv lock` 生成） |
| `tests/test_ci_config.py` | 12 处字面（#13）+ 2 处隐式 org 前缀（#13b） |
| `tests/test_governance_rename.py` | 1 处（#14） |
| `tests/test_pr_merged_verification.py` | 4 处 mock fixture（#15，可选但建议） |
| `README.md` | 2 处文档引用（§1.4） |
| `AGENTS.md` | 1 处文档引用（§1.4） |

**另需（不在 PR 内）**：合并前设置 `ENGINE_CONSUMERS`（§2，GitHub 平台侧操作，不落仓库文件）。

---

## 6. 风险排序与建议路径

### 6.1 风险排序

| 级别 | 风险 | 触发条件 | 可见性 / 恢复 |
|---|---|---|---|
| **P0** | droid-review + auto-merge 授权门全红 | 合并前未设 `ENGINE_CONSUMERS` | 秒级 fail-loud、下游零启动；补设 variable 后重跑即恢复 |
| **P1** | scan/heartbeat 执行环境静默翻转 hosted | 未显式传 `runner` | 静默（首次 tick 才发现）；加回 input 即回退 |
| **P1** | 测试断言硬编码未改全 → CI 红 | 字面或 org 前缀漏改（尤其 `:670`/`:720` 两处隐式耦合） | CI 立即可见；逐处改到 rg 归零 |
| **P2** | 版本跨度行为差（`v0.15.2` → `v0.18.9` 之间的模板/接口演进） | 中间版本有未核对的接口变更 | 本方案已逐项核对调用面（§4）；剩余为运行时行为，靠首个 tick 与首个 PR 验证 |
| **P2** | secrets 映射键大小写形态不一致 | 若平台对 `secrets:` 映射键做大小写敏感匹配 | fail-loud 可见；按 §4.2 建议顺手对齐小写 |
| **P3** | 文档引用陈旧（README/AGENTS 指旧仓） | 不同 PR 更新 | 无机械守卫；建议同 PR 修 |
| **P3** | `branch-cleanup` 未对齐 caller 侧授权门模板 | 不修 | 不影响运行（action 本体无门）；若要求「满配对齐」可同 PR 追加 gate job |

### 6.2 建议路径（实施立项的执行顺序）

1. **平台侧先行（P0）**：`gh variable set ENGINE_CONSUMERS --body authorized -R hdot123-org/memory` → 用 `gh variable list` 验证。
2. **单 PR 落地（§5 的 15 文件）**：
   a. 10 处 `uses` 改新仓 + `@v0.18.9`（rg 归零验证）；
   b. `pyproject.toml` 改 URL/tag → `uv lock` relock → `uv sync --frozen --extra dev` 通过；
   c. scan/heartbeat 显式传 `runner`（§3 方案 A）；
   d. 测试断言面全改（含 `:670`/`:720` 隐式耦合）→ 本地跑 `tests/test_ci_config.py`、`test_governance_rename.py`、`test_pr_merged_verification.py` 全绿；
   e. README/AGENTS 文档引用同步；
   f. `secrets:` 映射键小写对齐（§4.2，可选但对齐推荐）。
3. **验收**：首个 PR 的 droid-review run 看 `authorization-gate` 通过；下一个 tick 看 scan/heartbeat 在 `pve-linux` 上正常执行；auto-merge 正常。
4. **后续独立立项**（本方案不覆盖）：prompt 第三副本 drift 机制二选一（§4.4）；hosted runner 迁移评估（§3）；onboarding 模板 pin 新鲜度扫描面（§7）。

---

## 7. 与真源登记、四层模型的衔接（无矛盾声明）

本方案与既有治理文档交叉核对，结论**一致、无矛盾**：

| 既有登记 | 位置 | 本方案的对应表述 |
|---|---|---|
| prompt 第三副本「当前无活消费者」「无任何测试锁」 | `docs/governance/source-of-truth.md` §2.4 | §4.4：实测 md5 零 drift + **无锁**；接回前必须定 drift 机制（与真源文档「待决」条目一致） |
| onboarding 模板 pin 漂移（`branch-cleanup.thin-caller.yml:126` 仍 `3695445b`，与 `04af200` 内容已漂移） | `docs/governance/source-of-truth.md` §4.1 | §6.1 P3：本方案不改模板（属独立立项）；memory 是既有仓升级、不按该模板落地，故不阻塞 |
| 四层模型中 `~/memory` 当前不消费本仓（引擎层活消费仓 = 0） | `docs/architecture.md` §1.1 + `source-of-truth.md` §2.4 | 本方案即「接回」的前置决策；接回后该事实条目需同步更新（实施立项的收尾项） |
| 授权三件套（variable / PAT / runner 注册） | `docs/onboarding/consumer-onboarding.md` §9 | §2 取其中 variable 一项作为 P0；PAT 与 runner 注册 memory 已具备（实测 `DISPATCH_TOKEN` 在用、自建 runner 在跑） |

> **反向印证**：真源登记说「第三副本无锁、模板 pin 漂移、memory 不消费本仓」，本方案说「接回前须定 drift 机制、模板漂移另立项、本次即接回决策」——两文档互为印证，不存在「一处说已漂移、另一处说无漂移」的矛盾。

---

## 8. 复核命令与实测输出（2026-09-21，本 worker 执行）

> 说明：命令中的旧仓名用 shell 变量拼装（`OLD_ORG` + `OLD_REPO`），规避本仓连写守卫；直接书写请用间隔写法。

```bash
# ① pin 面：workflow uses 计数（实测 10）
cd ~/memory
OLD_ORG=hdot123-org; OLD_REPO=infra-core
rg -n "uses:.*$OLD_ORG/$OLD_REPO" .github/workflows/ | wc -l          # → 10（8 个文件）
rg -c "uses:.*$OLD_REPO" .github/workflows/*.yml | grep -v ':0'
#   droid-review.yml:2  droid-review-watchdog.yml:2  setup-labels.yml:1
#   evolution-governance.yml:1  evolution-scan.yml:1  evolution-heartbeat.yml:1
#   branch-cleanup.yml:1  auto-merge.yml:1

# ② 全仓字面计数（实测 23 处，7 个文件）
rg -c "$OLD_ORG/$OLD_REPO" --glob '!.git' .
#   tests/test_ci_config.py:12  tests/test_pr_merged_verification.py:4
#   README.md:2  uv.lock:2  pyproject.toml:1  AGENTS.md:1  tests/test_governance_rename.py:1

# ③ pyproject / uv.lock 行号（实测）
rg -n 'infra-core @' pyproject.toml        # → 15:
rg -n 'infra-core' uv.lock                 # → 308/310（source）、443/469（依赖条目）

# ④ ENGINE_CONSUMERS 现状（实测：12 个变量，无 ENGINE_CONSUMERS）
gh variable list -R hdot123-org/memory

# ⑤ prompt 第三副本 md5（实测两侧一致）
md5 ~/infraro-core/.github/review/shard-review-prompt.md   # → 123dc4011b6232aa7df522ceff86aea2
cd ~/memory && md5 .github/review/shard-review-prompt.md   # → 同上（2643 字节）

# ⑥ 旧 reusable runner（实测硬编码自建）
#    取 v0.15.2 的 evolution-scan.yml / evolution-heartbeat.yml：runs-on: [self-hosted, pve-linux]
# ⑦ 新 reusable runner 默认（实测 hosted）
cd ~/infraro-core
rg -n "fromJSON\(inputs.runner" .github/workflows/evolution-scan.yml .github/workflows/evolution-heartbeat.yml
#   → runs-on: ${{ fromJSON(inputs.runner || '"ubuntu-latest"') }}

# ⑧ 授权门位置（实测）
rg -n 'ENGINE_CONSUMERS' .github/workflows/droid-review-shards.yml .github/workflows/auto-merge-pipeline.yml
rg -n 'ENGINE_CONSUMERS' actions/branch-cleanup/action.yml        # → 无输出（action 本体无门）

# ⑨ tag / 包名（实测）
git tag --list 'v0.18*' | tail -1        # → v0.18.9
git rev-parse v0.18.9                    # → f041789d9a1ab1748edc4b9ba73340096f5a43c6
rg -n '^name|^version' pyproject.toml    # → name = "infra-core" / version = "0.18.9"

# ⑩ 隐式耦合（实测，调查报告未列）
rg -n 'startswith\("hdot123-org/|hdot123-org/\{repo\}' tests/test_ci_config.py
#   → 670: if uses.startswith("hdot123-org/") and "@" in uses:
#   → 720: url = f"https://github.com/hdot123-org/{repo}/archive/{ref}.tar.gz"
```

---

## 9. 边界与待决

**本方案不做**（全部留给实施立项）：

- 不设置 `ENGINE_CONSUMERS`（平台侧写操作，属实施第一步）；
- 不改 `~/memory` 任何文件（只读调查）；
- 不改 `docs/onboarding/templates/*`（模板 pin 漂移属独立立项，见 §7）；
- 不改引擎仓任何代码/pin/action 默认值。

**待用户裁决 / 实施立项待决**：

1. runner 方案（建议 A：首版显式传，保持等价）——§3；
2. prompt 第三副本 drift 机制二选一（MANIFEST 纳入 vs 从 `engine/` 读）——§4.4；
3. hosted runner 迁移是否另行立项评估——§3；
4. `branch-cleanup` caller 侧授权门模板对齐（P3 可选）——§6.1。
