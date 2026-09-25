# 引擎裸名 import 立项决策（A 维持现状 / B 全面包化 / C 门面过渡）

> **文档性质：立项决策建议，最终裁决权在用户；实施另立项。**
> 本文件回答「拆不拆、怎么拆、会碎什么锁、前置条件是什么」，**不落任何代码改动**——`src/infra_core/engine/` 在本文件落地前后保持零 diff。
>
> - **范围**：`src/infra_core/engine/` 的模块组织方式（模块间裸名 import 链 + 单文件平铺部署面）是否改造；与 v3 步骤 4（消费仓接回）的先后关系。
> - **纪律**：文中全部关键数字由本 worker 于 2026-09-21 实测复核（命令见 §7），不转抄调查报告；与调查结论有出入处以实测值为准。
> - **登记位置**：`docs/governance/`（已注册分类目录，`scripts/check_doc_classification.py` 覆盖）。
> - **交叉引用**：`docs/architecture.md` §1.1（四层体系 + 寄居件标注）、`docs/governance/source-of-truth.md` §5（真源登记与副本归属）。比对结论见 §4。
> - **旧仓名写法**：全文如涉旧引擎仓一律间隔写法（两段分开书写）；连写形态会被本仓守卫 `tests/test_no_old_repo_references.py` 零容忍拦截。

---

## 0. 结论摘要

| 决策项 | 建议 | 依据 |
|---|---|---|
| **当前动作** | **A：维持现状不拆**（零代码改动；本文件即"债务已显式登记"的产物） | 拆分动机当前未成立：本仓活消费仓数 0、无多人协作冲突（§1.6、§3.2） |
| **立项后的路径** | **C：门面过渡双轨**（按模块切片、每片可独立验证与回退） | 644 处 patch 目标必须迁移，C 允许按片支付该成本（§2.3、§2.4） |
| **一次性全面包化** | **否决（B 不作单次动作）** | 同时撕裂几乎全部锁面；与部署面"单文件平铺自包含"模型正面冲突（§2.2、§2.5） |
| **硬前置** | 消费仓接回（v3 步骤 4）落地**之后**再立项 | 避免"引擎裸名改造"与"消费仓迁移"双窗口漂移（§3.2 P1） |
| **拆分第一批** | 只动**非部署面**模块（scanner / heartbeat / self_audit / version_sync） | 部署面 4 文件必须保持平铺自包含（§2.5 实测约束） |
| **每片验收口径** | 全量 pytest 绿 + patch 目标 grep 归零 + 指纹面复核 + prod 4 文件逐字节一致 | §3.4 |

**一句话结论**：裸名 import 现在是**承重结构**而不是历史脏迹——644 处测试 patch 目标、22 条 suppress 指纹、4 行 gate0 豁免、4 条平铺部署映射、2 处流水线路径锚都挂在它上面。拆分应以"门面过渡、分片迁移、每步可回退"（C）推进，且必须等消费仓接回完成后再立项；是否立项与何时立项，**裁决权在用户**（§6）。

---

## 1. 现状量化（全部实测）

### 1.1 引擎模块间裸名 import 全清单

**规模**：`src/infra_core/engine/` 12 个 Python 文件、共 7821 行（`wc -l` 实测）。

| 文件 | 行数 | 文件 | 行数 |
|---|---|---|---|
| `evolution_utils.py` | 2071 | `droid_review/publish_findings.py` | 445 |
| `evolution_scanner.py` | 1755 | `evolution_adapters.py` | 431 |
| `evolution_self_audit.py` | 972 | `anchor_gate.py` | 164 |
| `evolution_heartbeat.py` | 916 | `droid_review/plan_shards.py` | 130 |
| `version_sync.py` | 775 | `extract_anchor.py` | 111 |
| `engine/__init__.py` | 50 | `droid_review/__init__.py` | 1 |

**裸名 import 全清单：10 条语句 / 7 条依赖边 / 6 个模块**（`grep` + AST 双重实测）：

| # | 源模块:行 | 语句 | 层次 | 依赖边 |
|---|---|---|---|---|
| 1 | `evolution_utils.py:18` | `from evolution_adapters import quarantine_corrupted_file, sanitize_structured_field` | 模块级 | utils → adapters |
| 2 | `evolution_scanner.py:23` | `from evolution_adapters import TOOL_TO_CATEGORIES, sanitize_structured_field, sanitize_text` | 模块级 | scanner → adapters |
| 3 | `evolution_scanner.py:24` | `from evolution_utils import (…)`（10 个名字） | 模块级 | scanner → utils |
| 4 | `evolution_heartbeat.py:22` | `from evolution_utils import gh_repo_args` | 模块级 | heartbeat → utils |
| 5 | `extract_anchor.py:29` | `from evolution_utils import extract_linkback_anchor` | 模块级 | extract_anchor → utils |
| 6 | `evolution_scanner.py:145` | `from evolution_adapters import ADAPTER_MAP` | 函数级 | scanner → adapters |
| 7 | `evolution_scanner.py:924` | `from evolution_heartbeat import (…)`（2 个名字，`try` 内） | 函数级 | scanner → heartbeat |
| 8 | `evolution_scanner.py:1227` | `from evolution_utils import forward_drift_watch` | 函数级 | scanner → utils |
| 9 | `evolution_scanner.py:1708` | `from evolution_utils import reverse_drift_watch`（`try` 内） | 函数级 | scanner → utils |
| 10 | `evolution_self_audit.py:434` | `from evolution_scanner import resolve_rule_packs`（`ImportError` 回退包路径 `from infra_core.engine.evolution_scanner import …`） | 函数级 | self_audit → scanner |

**支撑机制（裸名可被解析的前提，实测）**：

- 3 处 `sys.path.insert` 把自身目录塞进 `sys.path`：`evolution_scanner.py:18-21`（注释 P1-A：`PYTHONSAFEPATH` 阻断自动插入、防模块污染）、`evolution_heartbeat.py:21`、`extract_anchor.py:27`；
- workflow 侧以 `PYTHONSAFEPATH: '1'` 运行（`evolution-scan.yml:172`、`evolution-heartbeat.yml:145`），把"自动插入"换成"显式插入"；
- `version_sync.py` 与 `anchor_gate.py` **零裸名 import**（前者纯标准库；后者以同目录子进程 `SCRIPT_DIR / "extract_anchor.py"` 调用，仍是平铺布局假设）；
- 全清单**零** Core→Delivery 反向依赖（`evolution_*` 核心模块不 import `droid_review` / `anchor_gate` / `extract_anchor`），与 `docs/architecture.md` §1.1 的单向依赖红线一致。

### 1.2 测试面：`sys.path` 引导 + patch 目标（实测计数）

| 项 | 实测值 | 口径 |
|---|---|---|
| 使用 `sys.path.insert` 的测试文件 | **18 个文件 / 20 处** | `grep -rc "sys.path.insert" tests/*.py` |
| 测试内裸名 import 语句 | **211 条 / 14 个文件**（`test_evolution_scanner.py` 单文件 179 条） | `rg -o "^\s*(import\|from) evolution_[a-z_]*" tests/*.py \| wc -l` |
| `patch("evolution_*")` 目标总数 | **644 处 / 8 个文件** | `grep -rho 'patch("evolution_[^"]*"' tests/ \| wc -l` |
| — `patch("evolution_scanner…")` | **366 处**（全部在 `tests/test_evolution_scanner.py`） | `grep -c 'patch("evolution_scanner' tests/test_evolution_scanner.py` |
| — `patch("evolution_heartbeat…")` | **192 处** | 分文件计数（见下表） |
| — `patch("evolution_utils…")` | **75 处** | 同上 |
| — `patch("evolution_self_audit…")` | **11 处** | 同上 |
| `tests/test_evolution_scanner.py` 全貌 | **446 处 patch / 352 个 test def / 11074 行** | 同上 |
| 测试内包路径 import（`infra_core.engine.*`） | **17 处 / 7 个文件** | `rg -o "infra_core\.engine\.[a-z_]+" tests/*.py \| wc -l` |
| 引擎域测试收集数（9 个文件） | **496 个** | `pytest … --collect-only -q` |
| 全量套件收集数 | **2351 个** | `.venv/bin/pytest tests/ --collect-only -q` |

patch 目标分文件分布（实测，四列相加 = 644）：

| 测试文件 | scanner | heartbeat | utils | self_audit | 合计 |
|---|---|---|---|---|---|
| `test_evolution_scanner.py` | 366 | 31 | 38 | 11 | 446 |
| `test_evolution_heartbeat.py` | 0 | 155 | 0 | 0 | 155 |
| `test_drift_watch_reverse.py` | 0 | 0 | 15 | 0 | 15 |
| `test_gh_repo_context_guard.py` | 0 | 6 | 4 | 0 | 10 |
| `test_drift_watch_reverse_integration.py` | 0 | 0 | 9 | 0 | 9 |
| `test_cross_e2e_observability.py` | 0 | 0 | 4 | 0 | 4 |
| `test_tick_budget.py` | 0 | 0 | 3 | 0 | 3 |
| `test_deadlock_exit_retrigger.py` | 0 | 0 | 2 | 0 | 2 |
| **合计** | **366** | **192** | **75** | **11** | **644** |

另有测试外的裸名引导：`tests/conftest.py:28-33` 的自动 fixture `_reset_tick_tracker` 对**每个测试**执行 `sys.path.insert(engine_dir)` + `importlib.import_module("evolution_utils")`——整个测试进程的隔离面都建立在平铺布局上。

> **拆分陷阱（与 F2 `daily_audit` 拆分同源的教训，本 mission 已实证）**：`patch("evolution_utils.X")` 打的是**门面/模块属性**；若实现体已搬走而调用方改用 `from <定义模块> import X` 的绑定，patch 会**静默失效**——不报错、mock 不生效，真实调用穿透（真实 SSH/网络 → 挂起）。拆分时 patch 重定向必须逐条改到**定义模块**，并以 grep 归零验证。

### 1.3 部署面：`webhook-scripts/MANIFEST.sh` 4 条单文件平铺映射（实测）

```bash
CROSS_DIR_MAPPINGS=(
    "src/infra_core/engine/extract_anchor.py:extract_anchor.py"
    "src/infra_core/engine/evolution_utils.py:evolution_utils.py"
    "src/infra_core/engine/evolution_adapters.py:evolution_adapters.py"
    "src/infra_core/engine/anchor_gate.py:anchor_gate.py"
)
```

- 格式是「仓库相对路径 : 部署目标文件名」——目标端**平铺到生产目录根**，不带包结构、不建子目录；
- 同文件另有 `MANAGED_FILES` 14 条 + `MANAGED_LIB_FILES` 3 条（与本次决策无关，未动）；
- **生产侧与仓库逐字节一致（实测 4/4 IN_SYNC）**：`~/.factory/webhook/scripts/` 下四个文件的 `md5 -q` 与仓库侧相同；
- 生产调用链以**裸路径**执行其中的脚本：`trigger-droid.sh:1180` 跑 `python3 "$SCRIPT_DIR/anchor_gate.py"`、`reconcile-evolution.sh:391` 跑 `python3 "$SCRIPT_DIR/extract_anchor.py"`——依赖链（`extract_anchor → evolution_utils → evolution_adapters`）必须与调用方一同平铺部署，否则 `ModuleNotFoundError`（INFRA-357 根因）；
- 契约锁：`tests/test_webhook_scripts_manifest.py` 共 **10 个用例**，其中 `test_cross_dir_targets_are_flat_names` 锁「目标必须是平铺裸文件名」、`test_engine_sources_are_the_deploy_canonical_modules` 锁「源集合恰为上述 4 个引擎模块」。

### 1.4 部署巡检：`drift-gate` 的「工作树干净」要求（实测）

`webhook-scripts/drift-gate.sh` 是三态巡检（`GATE_INVALID` exit 2 / `IN_SYNC` exit 0 / `DRIFT` exit 1），预检 6 项中与拆解直接相关的 4 项：

| # | 预检 | 不满足时 |
|---|---|---|
| 2 | 必须在 `main` 分支 | GATE_INVALID |
| 4 | `HEAD == origin/main`（先 `git fetch`） | GATE_INVALID |
| 5 | `webhook-scripts/` 工作树干净 | GATE_INVALID |
| 6 | **`src/infra_core/engine/` 工作树干净** | GATE_INVALID |

**含义**：只要 `src/infra_core/engine/` 存在任何未提交改动（包括拆分进行中的中间态、或长期特性分支上的引擎改动），部署巡检即退化为 **GATE_INVALID（不判漂移）**——巡检从「能判漂移」变成「不能判」。因此拆分必须落在**合并后的 main** 上才恢复巡检能力，这反过来约束了拆分 PR 的粒度（不宜碎到长期悬空）。

### 1.5 锁面总表（「会碎的锁」清单）

| # | 锁 | 形态 | 位置 | 触发条件 |
|---|---|---|---|---|
| L1 | 字节锁（涉 engine 的 1 对） | 两侧 `read_bytes()` 相等 + git 文件模式锁 | `src/infra_core/engine/droid_review/publish_findings.py` ↔ `actions/droid-review-aggregate/publish_findings.py`；`tests/test_droid_review_aggregate_action.py` | 真源路径或字节变动即碎 |
| L2 | 等价锁（不涉 engine，列作对照） | 11 条决策表逐例判定等价 | `governance.py` ↔ `governance_check.py`；`tests/test_governance.py::TestActionScriptEquivalence` | 与包化无关 |
| L3 | 部署清单锁 | 4 条平铺映射的**结构 + 源路径 + 目标平铺性** | `tests/test_webhook_scripts_manifest.py`（10 用例） | 平铺 → 包部署即碎 |
| L4 | patch / 引导锁 | 644 处 `patch("evolution_*")` + 211 条裸名 import + 20 处 `sys.path.insert`（18 文件） | `tests/` | 模块名或绑定位置一改即碎（且可能**静默**） |
| L5 | 指纹锁（suppress） | `.evolution/suppress.json` 36 条中 **22 条涉 engine**（11 对重复块指纹成对记账） | `.evolution/suppress.json` | 路径/行号变更即失配 |
| L6 | 指纹锁（gate0 豁免） | `substrate/gate0-exemptions.md` 31 行中 **4 行涉 engine**（3 行 `publish_findings` 重复块 + 1 行 `evolution_adapters.py` 的 local-path 豁免） | `substrate/gate0-exemptions.md` | 同上 |
| L7 | 流水线路径锚 | `engine/src/infra_core/engine/droid_review/{plan_shards.py,run_shard.sh}` 仓库内路径拼接 | `.github/workflows/droid-review-shards.yml:362,587` | 目录一动即断（droid review 流水线） |
| L8 | 契约锁（import 面） | 4 用例：子模块 import 不拉起 scanner 链（消费仓同名裸名模块碰撞防御）+ 10 个旧包级导出名保持 + 未知属性 AttributeError + 干净解释器可 import | `tests/test_engine_lazy_init.py`；`src/infra_core/engine/__init__.py:24-50`（`_LAZY_ATTRS` L24、`__getattr__` L40，共 10 个 lazy 导出） | 防碰撞补偿层随裸名布局存在 |
| L9 | 契约锁（sys.path 自恢复） | `test_scanner_restores_sys_path_with_safepath` 断言 scanner 目录必须在 `sys.path` | `tests/test_evolution_scanner.py:4277` | 显式锁住 P1-A 机制 |
| L10 | 入口字符串锁 | `python -m infra_core.engine.evolution_scanner` / `…heartbeat` 字面断言 | `tests/test_evolution_scan_heartbeat_workflow.py:100,376` | 入口串一改即碎 |
| L11 | 治理面锁 | 受保护路径 `src/infra_core/engine/**` 共 4 处：`src/infra_core/governance.py:22`、`actions/governance-check/governance_check.py:26`、`actions/governance-check/action.yml:11`、`.github/workflows/evolution-governance.yml:36`（另有 `:9` 触发 `paths`） | 同上 | 引擎路径变更需同步治理面 |
| L12 | 工具链配置面 | `pyproject.toml`：`[tool.setuptools.packages.find] where=["src"]`（L52-53，平铺自动发现）、ruff `per-file-ignores`（L84）、mypy `overrides` 列 8 个裸名模块（L97-108）、deptry `DEP001` 列 9 项（L135-145） | `pyproject.toml` | 包化需同步四处配置 |
| L13 | 部署一致性（运行时） | drift-gate 预检 5/6（§1.4）+ 生产 4 文件逐字节一致（实测 4/4） | `webhook-scripts/drift-gate.sh`、`webhook-scripts/sync-webhook-scripts.sh` | 见 §1.4 |

### 1.6 消费面与入口面（包化的「外部可见面」）

| 消费面 | 形态 | 实测位置 |
|---|---|---|
| 打包入口 | `infra-self-audit = "infra_core.engine.evolution_self_audit:main"` | `pyproject.toml:41` |
| 模块 CLI | `python -m infra_core.engine.evolution_scanner`（`PYTHONSAFEPATH: '1'`）、`python -m infra_core.engine.evolution_heartbeat` | `evolution-scan.yml:163`、`evolution-heartbeat.yml:141` |
| 包内调用 | `from infra_core.engine import evolution_scanner` / `version_sync` | `src/infra_core/cli.py:18,69` |
| 仓内包装脚本 | `scripts/droid_review/plan_shards.py:4`、`publish_findings.py:8`（re-export 包装）、`run_shard.sh:8`（拼引擎路径） | 同上 |
| CI 健康自检 | `scripts/ci_health_check.sh:24-28` 逐个 `__import__` 5 个 engine 模块 + `infra_core.packs.memory` | 同上 |
| 边界白名单 | `scripts/check_boundary.py:89` 列 `src/infra_core/engine/evolution_adapters.py` 为「行为等价移植例外」 | 同上 |
| 消费仓侧 | 本仓**活消费仓数 = 0**（`~/memory` 仍 pin 已归档的旧引擎仓 tag `v0.15.2`，接回未实施）；`~/memory` 树内**已无活裸名模块**（`find` 命中的 16 个 `evolution_*.py` 全部是 `artifacts/runs/*` 历史归档、`.venv` 已安装包与 `build/lib` 产物） | §7 复核命令 |

---

## 2. 三方案对比

### 2.1 方案 A：保持现状不拆（零改动）

| 维度 | 内容 |
|---|---|
| 改动面 | **0 个文件**；本文件即唯一产物（把债务显式登记） |
| 会碎的锁 | 无 |
| 风险 | 债务累积：`evolution_utils.py` 2071 行 / `evolution_scanner.py` 1755 行；裸名链 + `sys.path` 自插入是隐式契约网络，新读者或 agent 容易误改触发静默失效（L4）；消费仓同名裸名模块碰撞的风险面长期存在（现由 L8 的 lazy 补偿层兜住） |
| 可回退性 | 不需要回退（无改动） |
| 适用判据 | 拆分动机未成立时（即当前状态）：无活消费仓、无多人协作合并冲突、文件规模虽大但改动落在少数熟悉者手上 |

### 2.2 方案 B：全面包化（一次性）

| 维度 | 内容 |
|---|---|
| 改动面 | 引擎 6 个文件 10 条 import 语句 + 3 处 `sys.path.insert` 移除；**测试 18 个文件**（644 处 patch + 211 条裸名 import + 20 处 `sys.path.insert`）；`MANIFEST.sh` 4 条平铺改包部署 + `sync-webhook-scripts.sh` + `drift-gate.sh`；`pyproject.toml` 四处配置（L12）；2 处 workflow `python -m` 入口 + `shards.yml` 2 处路径锚；3 个 `scripts/droid_review/*` 包装 + `ci_health_check.sh` + `check_boundary.py`；治理面 4 处受保护模式（L11）；suppress 22 条 + gate0 4 行登记复核；生产部署模型（平铺 → 包目录）与 2 处生产裸路径调用链 |
| 会碎的锁 | **L1 / L3 / L4 / L5 / L6 / L7 / L9 / L10 / L11 / L12 / L13 —— 几乎全表**（仅 L2、L8 语义不变） |
| 风险 | ①**静默失效**：patch 目标改名后 mock 不生效，测试要么假绿要么挂起（§1.2 陷阱）；②部署面冲突：生产脚本以裸路径调用，包化必须连同部署模型一起改，否则生产锚点链路断（INFRA-357 回潮）；③指纹失配：重复块/边界豁免被重新判定，gate0 需重新签核；④**中间态不可发布**，只能用一个巨型 PR 落地 |
| 可回退性 | 差：回退 = 整 PR revert，且 revert 本身要再跑一次全量门禁 |

### 2.3 方案 C：门面过渡双轨（分片、逐模块）

| 维度 | 内容 |
|---|---|
| 改动面 | 按**模块切片**：每片 ①建目标结构；②旧模块名文件降为**门面**（显式逐名 re-export，含下划线别名；`import *` 拿不到下划线名）；③该模块的 patch 目标重定向到定义模块（grep 逐条）；④复核该模块指纹面。部署面 4 文件**不进第一批**（§2.5） |
| 会碎的锁 | L4（**每片均须支付 patch 重定向成本，但可切片支付**）；涉 L1/L5/L6/L7 的片才触发对应锁；L3/L13 在「部署面」模块被拆分前**不触发** |
| 风险 | ①双轨漂移：门面与新结构并存，若门面漏 re-export 某个名字，会得到「看起来可用、行为分歧」的中间态；②门面装载方式陷阱（见下）；③patch 漏改静默失效（与 B 同源，但影响面被限制在单片） |
| 可回退性 | 好：每片独立 commit + 独立 revert；门面保留旧名，回退不需要动消费面 |
| 技术约束（实测推出） | 门面必须**同时**支持两种装载方式：a) 包路径 `import infra_core.engine.X`；b) 裸名 `import X`（`sys.path.insert(engine_dir)` 后）。后者**无父包上下文**，门面**不能只用相对 import**；仓内已有可直接复用的模式：`evolution_self_audit.py:432-436` 的 `try/except`——裸名优先、`ImportError` 回退包路径 |

### 2.4 对比总表

| 维度 | A 维持现状 | B 全面包化 | C 门面过渡 |
|---|---|---|---|
| 改动面（文件数） | 0 | 6 引擎 + 18 测试 + 3 部署脚本 + 4 配置面 + 3 workflow + 4 脚本 + 2 登记面 ≈ **40+** | 按片，单片 ≈ 2-4 文件 |
| 部署面（MANIFEST 平铺） | 不动 | **改部署模型**（平铺 → 包） | 部署面放最后、单独决策 |
| 会碎的锁 | 无 | 全表（L1、L3-L7、L9-L13） | 每片 L4（+涉及片的 L1/L5/L6/L7） |
| 测试迁移成本 | 0 | 644 patch + 211 裸名 + 20 sys.path（18 文件），一次性 | 同上，按片分摊 |
| 风险 | 债务累积；静默失效风险随改动概率上升 | 静默失效面最大 + 部署链断裂 + 中间态不可发布 | 双轨漂移；静默失效可被单片隔离 |
| 可回退性 | — | 差（整 PR revert） | 好（逐片 revert） |
| 每步可否独立验证 | — | 否（大爆炸） | 是（每片：pytest 绿 + patch 归零 + 指纹复核） |

### 2.5 关键推论：必须「按部署面划线」

实测（§1.3、§1.4）给出一个硬约束：**部署面 4 文件（`evolution_utils.py`、`evolution_adapters.py`、`extract_anchor.py`、`anchor_gate.py`）必须保持「单文件自包含 + 同目录裸名依赖」**。理由：

1. `CROSS_DIR_MAPPINGS` 的契约是**单文件平铺**（`tests/test_webhook_scripts_manifest.py::test_cross_dir_targets_are_flat_names` 锁死），目标端不接收包结构；
2. 生产调用方以裸路径直接执行其中两个脚本（`trigger-droid.sh:1180`、`reconcile-evolution.sh:391`），依赖链 `extract_anchor → evolution_utils → evolution_adapters` 必须与调用方**同目录**；
3. drift-gate 的三态判定建立在「引擎目录干净 + main 同步」之上，部署面一旦涉及目录搬迁，巡检与同步脚本必须同步改造。

因此拆分应**先动非部署面**（`evolution_scanner` / `evolution_heartbeat` / `evolution_self_audit` / `version_sync`），**部署面（含 `droid_review` 的路径锚与字节锁）留到最后**，且要先有一份独立的「部署模型决策」（是否允许包部署、生产目录如何承载）才能动。

---

## 3. 建议与前置条件

### 3.1 建议（明确）

1. **当前：A**。维持现状不拆，把债务显式登记在本文件（本条 + `docs/governance/source-of-truth.md` §5 的登记面）。
2. **立项后：C**。门面过渡双轨、按模块切片、先非部署面后部署面；**否决 B 的单次动作形态**（B 的终态可在 C 完成后自然到达，但不应作为一次性变更）。
3. **拆分不是重构练习，而是契约迁移**：判据是「每一步都能独立验证并独立回退」，而不是「终态更整齐」。

### 3.2 前置条件（全部满足才立项）

| # | 前置条件 | 理由 |
|---|---|---|
| P1 | **消费仓接回（v3 步骤 4）已落地并稳定** | 避免双窗口漂移：引擎裸名改造与消费仓 pin 迁移若并行，消费仓会同时经历「引擎版本跨度」与「引擎模块名/路径变更」两类变化，出问题时无法二分定位 |
| P2 | 拆分动机成立（择一即触发） | ①单文件规模成为实际维护瓶颈（当前最大 `evolution_utils.py` 2071 行）；②出现多人/多 agent 并行改动同一模块的合并冲突；③需要独立演进或测试隔离 |
| P3 | **部署模型先决**：明确 `evolution_utils` / `evolution_adapters` / `extract_anchor` / `anchor_gate` 是否脱离平铺 | 若脱离，`MANIFEST.sh`、`sync-webhook-scripts.sh`、`drift-gate.sh`、`test_webhook_scripts_manifest.py` 与生产调用链必须同 PR 改造（§2.5） |
| P4 | 门面装载方式定案（裸名 + 包路径双支持） | 复用 `evolution_self_audit.py:432-436` 的 try/except 模式，并加一条载荷测试锁定两种装载路径 |
| P5 | 每片的验收口径先写进实施计划 | 见 §3.4 |

### 3.3 为什么不是「现在就 B」

- 644 处 patch + 211 条裸名 import 的迁移是**机械但静默危险**的工作：漏改不报错（patch 打空目标），只表现为「测试假绿」或「真实网络调用挂起」；
- 部署面 4 文件与生产裸路径调用链绑定（INFRA-357 的历史根因），包化会迫使**部署模型**同时变更——那是运维面变更，不应夹带在重构里；
- 中间态不可发布：B 只有一个可发布点（全绿时刻），失败时没有中间锚点可停。

### 3.4 若立项：分片顺序与每片验收口径（登记，不实施）

| 片 | 对象 | 规模/成本实测 | 备注 |
|---|---|---|---|
| 片 0 | `version_sync.py` | 775 行；零裸名依赖 | 风险最低，可作门面范式样板先行 |
| 片 1 | `evolution_scanner.py` | 1755 行；366 处 patch 集中在一个测试文件 | patch 面最大的一刀 |
| 片 2 | `evolution_heartbeat.py` | 916 行；192 处 patch | 与 scanner 有函数级依赖边（scanner:924） |
| 片 3 | `evolution_self_audit.py` | 972 行；11 处 patch | 已有 try/except 回退模式，可作门面范式样板 |
| 片 4（独立立项） | 部署面 4 文件 + `droid_review/` | 见 §1.3/§2.5 | 需 P3 部署模型决策 + L1/L3/L7 同 PR 改造 |

每片验收（五条全绿才提交）：①全量 pytest 绿（含引擎域 496）；②该模块 patch 目标 grep 归零（`grep -n 'patch("<旧模块名>\.' tests/` 输出为空）；③指纹面复核（suppress 22 条 / gate0 4 行不变或同步更新并说明）；④生产 4 文件 `md5` 仍与仓库一致；⑤关键路径 characterization（CLI `--help` / 入口字符串）diff 为空。

---

## 4. 与既有治理文档的一致性（交叉比对结论）

| 既有声明 | 出处 | 本文件结论 | 一致性 |
|---|---|---|---|
| 四层体系路径清单：Evolution Core = `src/infra_core/engine/` 的 6 个模块 | `docs/architecture.md` §1.1 | §1.1 的模块清单与行数与之吻合（6 核心模块 + 3 寄居件 + `__init__`） | 无矛盾 |
| 寄居件标注：`droid_review/`、`anchor_gate.py`、`extract_anchor.py` 物理留 `engine/`、逻辑归 Delivery；搬走 = 撕裂 suppress 指纹 / gate0 豁免 / 消费锚定 / 字节锁，净负收益 | `docs/architecture.md` §1.1 | §1.5 把「撕裂」量化为：22 条 suppress 指纹（11 对）、4 行 gate0 豁免、`shards.yml` 2 处路径锚、1 对字节锁；并补充**第五项**撕裂面——部署面平铺（§2.5） | 强化既有结论，无矛盾 |
| 单向依赖原则：`Delivery → Evolution Core` 允许；`evolution_*` 核心模块不得 import 交付件（代码级防线在 `engine/__init__.py` docstring） | `docs/architecture.md` §1.1 | §1.1 全清单实测：10 条裸名语句**零**跨越 Core→Delivery | 无矛盾 |
| 真源规则四要素 + 副本登记（字节锁 / 等价锁 / MANIFEST 平铺 / 第三副本 / 严重度词表） | `docs/governance/source-of-truth.md` §1-§5 | §1.5 的 L1-L3 与 §1.3 的映射数（4 条）与登记一致（`md5` 复核 4/4 通过）；本文件未新增副本、未改任何锁 | 无矛盾 |
| 消费仓接回方案（15 pin 点、P0 variable、P1 runner） | `docs/governance/consumer-reconnect-plan.md` | §3.2 P1 把「接回完成」设为拆分前置条件——两份文档的时序关系：**先接回，后拆分** | 时序相容 |

**反向声明**：本文件**不**主张搬迁寄居件、**不**主张改任何 pin / 配置 / 目录 / 测试；§2.3 与 §3.4 的分片顺序是「若立项」的登记，不是实施授权。

---

## 5. 给未来实施者的最短路径（命令备忘）

```bash
# 1) 拆分前基线：裸名面 / patch 面 / 引导面
grep -rn "^\s*\(import\|from\) evolution_" src/infra_core/engine/*.py
grep -rho 'patch("evolution_[^"]*"' tests/ | sort | uniq -c
grep -rc "sys.path.insert" tests/*.py | grep -v ':0'

# 2) 拆分一片后的归零验证（示例：scanner 片；期望输出为空）
grep -rn 'patch("evolution_scanner\.' tests/

# 3) 指纹面与部署面复核
python3 -c "import json;d=json.load(open('.evolution/suppress.json'));print(len(d['suppressed']))"   # 期望 36
for f in extract_anchor evolution_utils evolution_adapters anchor_gate; do
  a=$(md5 -q "src/infra_core/engine/$f.py"); b=$(md5 -q "$HOME/.factory/webhook/scripts/$f.py")
  [ "$a" = "$b" ] && echo "IN_SYNC $f" || echo "DIFF $f"
done
```

补充纪律（与 mission 拆分纪律同源）：**patch 重定向漏改 = mock 静默失效 → 真实 SSH/网络调用**；若测试挂起，先查 patch 漏改，不要靠加超时硬扛。

---

## 6. 裁决权与实施归属

- **本文件性质**：立项决策建议。**是否拆分、选哪一案、何时立项，裁决权在用户**；本文件不构成任何实施授权。
- **实施归属**：拆分实施另立项（独立 mission / 独立 PR），且必须先满足 §3.2 的 P1-P5 前置条件。
- **本文件不做的事**：不改任何代码、不动 `src/infra_core/engine/`、不改任何测试、不改 `MANIFEST.sh` / `drift-gate.sh` / `pyproject.toml` / workflow / 治理面配置、不建包、不拆文件。
- **若用户选择 A（当前建议）**：本文件即完成态，无需后续动作；债务登记已在本文件 + `source-of-truth.md` §5 落位。
- **若用户选择 C**：按 §3.4 分片推进，每片独立 commit，收尾独立 PR；引擎改动期间部署巡检会处于 GATE_INVALID（§1.4），属预期状态。

---

## 7. 复核命令与实测输出（本文件所有数字的来源）

```bash
# §1.1 引擎规模与裸名清单
wc -l src/infra_core/engine/*.py src/infra_core/engine/droid_review/*.py      # 7821 行/12 文件
grep -rn "from evolution_\|^import evolution_" src/infra_core/engine/*.py      # 10 条语句
python3 -c "import ast;[print(f'L{n.lineno}: from {n.module} import {len(n.names)}') for n in ast.walk(ast.parse(open('src/infra_core/engine/evolution_scanner.py').read())) if isinstance(n,ast.ImportFrom) and n.module and not n.module.startswith('infra_core')]"

# §1.2 测试面
grep -rc "sys.path.insert" tests/*.py | grep -v ':0'                            # 18 文件；总 20 处
rg -o "^\s*(import|from) evolution_[a-z_]*" tests/*.py | wc -l                  # 211 条
grep -rho 'patch("evolution_[^"]*"' tests/ | wc -l                              # 644 处
grep -c 'patch("evolution_scanner' tests/test_evolution_scanner.py              # 366 处
rg -o "infra_core\.engine\.[a-z_]+" tests/*.py | wc -l                          # 17 处
.venv/bin/pytest tests/ --collect-only -q --no-header                           # 2351 个
.venv/bin/pytest tests/test_evolution_scanner.py tests/test_evolution_heartbeat.py \
  tests/test_engine_lazy_init.py tests/test_drift_watch_reverse.py \
  tests/test_drift_watch_reverse_integration.py tests/test_cross_e2e_observability.py \
  tests/test_tick_budget.py tests/test_deadlock_exit_retrigger.py \
  tests/test_gh_repo_context_guard.py --collect-only -q --no-header             # 496 个

# §1.3 部署面
sed -n '/CROSS_DIR_MAPPINGS=(/,/^)/p' webhook-scripts/MANIFEST.sh               # 4 条
for f in extract_anchor evolution_utils evolution_adapters anchor_gate; do
  a=$(md5 -q "src/infra_core/engine/$f.py"); b=$(md5 -q "$HOME/.factory/webhook/scripts/$f.py")
  [ "$a" = "$b" ] && echo "IN_SYNC $f" || echo "DIFF $f"; done                   # 4/4 IN_SYNC
.venv/bin/pytest tests/test_webhook_scripts_manifest.py -q --no-header           # 10 用例

# §1.4 巡检面
grep -n "gate_invalid\|status --porcelain" webhook-scripts/drift-gate.sh         # 预检 6 项（含 2 处脏树检查）

# §1.5 指纹与锁面
python3 -c "import json;d=json.load(open('.evolution/suppress.json'));print(len(d['suppressed']), sum(1 for s in d['suppressed'] if 'engine' in json.dumps(s)))"   # 36 条 / 22 条涉 engine
rg -c "^\| \`" substrate/gate0-exemptions.md                                     # 31 行；其中 4 行涉 engine
rg -n "src/infra_core/engine" src/infra_core/governance.py actions/governance-check/governance_check.py actions/governance-check/action.yml .github/workflows/evolution-governance.yml   # 治理面 4 处
rg -n "engine/src/infra_core/engine" .github/workflows/droid-review-shards.yml    # 2 处路径锚（L362/L587）
sed -n '95,150p' pyproject.toml                                                   # mypy/deptry 裸名清单

# §1.6 消费面
find ~/memory -name "evolution_*.py" -not -path "*/node_modules/*" -not -path "*/.git/*" | wc -l   # 16（全部为归档/产物）
ls ~/memory/scripts/evolution_*.py 2>/dev/null || echo "(无活裸名模块)"
```

实测时间：2026-09-21（本 mission 分支 `refactor/daily-audit-split`，基线 `b748b99`）。所有数字均以当日仓库内容为准；实施前应重跑本节命令复核。
