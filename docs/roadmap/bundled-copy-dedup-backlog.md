# 自包含副本真重复收敛 Backlog（M3 遗留，2026-10-07）

## 背景与边界

M3（VAL-MIRROR-001/002，evolution/mirrors-toml）落地了 `.evolution/mirrors.toml`
声明式镜像清单：**字节一致**的自包含分发副本对由 hygiene `check_duplicates`
豁免 DUPLICATE 检测（漂移即恢复，守卫测试 `tests/test_hygiene_mirrors.py`）。

清单只罩得住字节一致对。以下 **6 对真重复**（10 条 suppress 条目，即两种
os.walk 遍历顺序各登记一条 × 5 个文件对）不符合字节一致前提，保留在
`.evolution/suppress.json`（expires 2027-06-30 + reason 留案）。本文件记录
各对的收敛方案，供后续 refactor 消化；**M3 未修改这些代码**。

suppress 条目迁移史：publish_findings 镜像对原登记的 30 条条目（15 函数对 ×
双向）已删除，由 mirrors.toml 豁免承担；下表对对应原 governance 2 条 +
sweep/adversarial 8 条 + trigger_release/routing 2 条（suppress 构成从
24 镜像+12 真重复+2 非镜像 演进为 0 镜像+14 条目登记 5 文件对+2 非镜像，
镜像增量源于 INFRA-1287 批次函数增长）。

## 收敛原则

- 测试辅助类重复：抽共享 helper 进 `tests/`（conftest 或独立 helpers 模块），
  两个消费测试文件 import；抽出后 helper 只留一份（先例：pilot seed 收敛
  PR #276，`tests/pilot_seed_helpers.py`）。
- 判定等价类重复（governance）：两文件行号不同、语义等价而非字节一致，
  收敛需抽共享判定函数并双侧回归（`TestActionScriptEquivalence` 契约保持）。
- 每对收敛后必须同步删除对应 suppress 条目（`test_suppress_drift_guard.py`
  正反向对账会强制）。

## 清单

| # | 文件对 | suppress 条目 | 性质 | 收敛方案 |
|---|--------|---------------|------|----------|
| 1 | `actions/governance-check/governance_check.py::L35` ↔ `src/infra_core/governance.py::L42` | 2 条 | 判定等价（`_match_any`，98% AST 相似，行号不同） | 抽共享判定 helper；action 侧自包含约束下评估「副本生成于真源」构建方案，或接受长期留案 |
| 2 | `tests/test_sweep_pipeline_fix.py::L39` ↔ `tests/test_real_shell_adversarial_matrix.py::L35` | 2 条 | 同源复刻测试辅助（`_extract_pipeline`） | 抽 `tests/` 共享 helper，双文件 import |
| 3 | `tests/test_sweep_pipeline_fix.py::L57` ↔ `tests/test_real_shell_adversarial_matrix.py::L53` | 2 条 | 同源复刻测试辅助（`_extract_case_guard`） | 同 #2（与 #2/#4/#5 一并做） |
| 4 | `tests/test_sweep_pipeline_fix.py::L74` ↔ `tests/test_real_shell_adversarial_matrix.py::L70` | 2 条 | 同源复刻测试辅助（`_run_extracted_guard`） | 同 #2 |
| 5 | `tests/test_sweep_pipeline_fix.py::L117` ↔ `tests/test_real_shell_adversarial_matrix.py::L113` | 2 条 | 同源复刻测试用例（`test_extracted_guard_adversarial_matrix`，class method） | 用例本体参数化/合并进共享 helper 后的单一定义处 |
| 6 | `tests/test_trigger_release_contract.py::L38` ↔ `tests/test_trigger_ci_droid_routing.py::L142` | 2 条 | 同源复刻测试辅助（`_make_env`） | 抽 `tests/` 共享 helper，双文件 import |

#2-#5 是同一对测试文件的四组重复（同源复刻），建议单 PR 一次收敛；#6 独立；
#1 涉及 action 自包含分发约束，需先裁定「共享判定真源放哪」（引擎仓判例：
副本生成于真源 vs 语义等价双写+契约测试）。
（2026-10-07 记录：poll_releases/trigger_release 两处 `test_shellcheck_zero_warnings`
在 f62a9cd 对齐降级口径后一度构成新的真重复对，当日直接以 `tests/shellcheck_helpers`
共享 helper 收敛（INFRA-297 判例复用），未入 suppress 留案。）

## 验收口径（未来消化时）

- 每对收敛 PR：改动后 scanner `--report-only` 断言对应 finding 不再出现，
  suppress.json 对应条目删除，`test_suppress_drift_guard.py` /
  `tests/test_evolution_scanner.py` 的 expected 集合同步缩小。
- expires 2027-06-30 前未消化：到期 suppress 失效（fail-open 检测恢复），
  scanner 会重新浮出这些 finding——这是有意设计（欠账到期自动显形）。
