# REPLACEMENT-INVENTORY · PR→main 流水线整体替换清单（脚手架）

- 定版依据：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md`
- 证据：`evidence/pipeline-adversarial-2026-10-25/`（cure-adversarial / disease-4 / final-verdicts）
- 旧集存档：`evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover/`（见该目录 `MANIFEST.md`）
- 状态：**大哥已点头**（2026-10-25）；本地聚焦绿见 `LOCAL-GREEN.md`（557 passed）；存档+回滚齐；第一测 PR 计划见 `FIRST-TEST-PR.md`。合入须读下方三注脚。

## 三门 check 名 · 当前产出点（替换瞬间必须继续产出）

| Check 名（ruleset `23079535` / `integration_id=15368`） | 当前产出点 | 替换后预期 |
|----------------------------------------------------------|------------|------------|
| `quality-gate` | `.github/workflows/quality-gate.yml` → job `name: quality-gate` | 同名保留；改为 CI/QA 双触发单次判定 + `NOT_READY`/`failure`/`success` |
| `substrate-gate-suite` | `.github/workflows/ci.yml` → job `name: substrate-gate-suite` | **名保留**；本集以少动为原则（真干活仍可留 pve） |
| `droid-review` | **PR head 原生载体**：`.github/workflows/droid-review-pr-rollup.yml` → job `name: droid-review`；**干活聚合**（workflow_run 语境）：`.github/workflows/droid-review.yml` → job key `droid-review`（无显式 name，id=check 名） | B 先行：瘦 Actions 贴同名；测 PR 过 rollup 后再 **删信封**；禁双写同名窗 |

辅助（非 ruleset 三门，但是 QG 聚合面）：`ci-ok`（ci.yml）、`qa-ok`（qa.yml）。

---

## 替换表

列说明：`改|删|增`；三门产出点 = `QG`/`SGS`/`DR`/`—`（Y=该文件是当前或目标产出点相关）；负责模块。

| 路径 | 改\|删\|增 | 意图 | 三门check产出点 (QG / SGS / DR) | 模块 |
|------|-----------|------|--------------------------------|------|
| `.github/workflows/quality-gate.yml` | **改** | 废 200×30s 轮询；CI/QA completed 双触发单次判定；未齐=`exit 1`+`NOT_READY`（禁 skip）；组键含 PR 号禁回落 main；瘦成可搬 hosted | **Y / N / N**（产出 `quality-gate`） | QG |
| `.github/workflows/droid-review-pr-rollup.yml` | **删** | 信封退休（B 测过 rollup 后）；若 B 首测前短暂并存须单一写者 | **N / N / Y**（当前 PR head 产出 `droid-review`） | Droid |
| `.github/workflows/droid-review.yml` | **改** | 仍听 QG success；concurrency 组键强制 PR 号（禁回落 `head_branch`）；cancel 仅同 PR 新 head；B：瘦形态贴/承 `droid-review`；`pr-head-check-mirror` 观察腿收敛或删；cancel 后禁残留 in_progress | **N / N / Y**（干活侧同名 job；B 后应为单一写者之一） | Droid |
| `.github/workflows/droid-review-shards.yml` | **改** | 真干活留 pve；与壳触发/聚合解耦保持；组键/并发上限与病四对齐（待组装方补细节） | N / N / N | Droid |
| `.github/workflows/droid-review-watchdog.yml` | **改** | 哨兵并入 watchdog：缺席→显式红；不误伤排队/in_progress；时限按 cron/heartbeat 口径；取消「30min 空头支票」 | N / N / N（可补写失败态 `droid-review`，非主产出） | Watchdog |
| `.github/workflows/droid-review-watchdog-handlers.yml` | **改** | 与并入哨兵的处置腿对齐；待组装方补 | N / N / N | Watchdog |
| `.github/workflows/auto-merge.yml` | **改** | FIFO：label 介质；触发壳 concurrency/`max-parallel:1`；队头失败/超时踢出+冷却；识别 `NOT_READY`≠质量红 | N / N / N（读三门） | FIFO |
| `.github/workflows/auto-merge-pipeline.yml` | **改** | mergeable **仅队头**；禁无序扫合；禁快路径自称队头直合；REST 三门绿判保留 + `NOT_READY` 语义 | N / N / N（读三门） | FIFO |
| `.github/workflows/ci.yml` | **改** | 保留 `substrate-gate-suite` / `ci-ok` 名；等候类步骤按病二评估是否 hosted（待组装方补） | **N / Y / N**（产出 `substrate-gate-suite`） | 其它 |
| `.github/workflows/qa.yml` | **改** | 保留 `qa-ok`；与 QG 双触发契约对齐（待组装方补） | N / N / N | 其它 |
| `.github/workflows/setup-labels.yml` | **改** | 增 `merge-queue` / `merge-queue-cooldown`（及病四排队标签，若拍板） | N / N / N | FIFO |
| `.github/workflows/droid-task.yml` | **改** | 铸卡/开 PR 侧：存量帽消费；`Fixes INFRA` 反查链；埋键后续加固不挡本集 | N / N / N | Dedup |
| `.github/workflows/evolution-scan.yml` | **改** | 铸卡入口走新去重+文件核闸+`max_inflight_prs`（待组装方补触发面） | N / N / N | Dedup |
| `.github/workflows/droid-autofix.yml` | 改 | 待组装方补（与铸卡/工单反查写端是否同改） | N / N / N | Dedup |
| `actions/auto-merge/action.yml` | **改** | 三门前置门 + FIFO 队头门；`NOT_READY` 不升级骂街 | N / N / N | FIFO |
| `src/infra_core/shell/auto_merge_triage.sh` | **改** | 与 pipeline heredoc 字节同步；FIFO+冷却；禁 rollup 假绿 | N / N / N | FIFO |
| `src/infra_core/shell/check_droid_review.sh` | **改** | 轮询胶水退役后收口或改短判定；与 `scripts/` 副本同步 | N / N / N | Droid |
| `scripts/check_droid_review.sh` | **改** | 同上（双载体） | N / N / N | Droid |
| `scripts/check_cheap_gates_green.sh` | **改** | 供短判定/哨兵复用；去掉长等假设（待组装方补） | N / N / N | QG |
| `scripts/check_zero_red.sh` | 改 | 待组装方补（零红扫描与 NOT_READY 并存） | N / N / N | 其它 |
| `scripts/droid_review/*` | 改 | 待组装方补（分片/发布是否随 B 瘦形态调整） | N / N / N | Droid |
| `src/infra_core/engine/evolution_scanner.py` | **改** | 在飞去重：issue∪（Fixes/INFRA/Closes 反查 issue）键；DUPLICATE 文件核附加闸；`max_inflight_prs` | N / N / N | Dedup |
| `src/infra_core/engine/evolution_utils.py` | **改** | `_normalize_dedup_location` / `dedup_intra_tick` 归一；反查键解析 helper | N / N / N | Dedup |
| `.evolution/config.yml` | **改** | 增 `max_inflight_prs`（及 REQUIRED_CONFIG_KEYS 同步处） | N / N / N | Dedup |
| `webhook-scripts/trigger-ci-droid.sh` | 改 | 待组装方补（外置触发是否仍刮日志） | N / N / N | 其它 |
| `webhook-scripts/ci-timeout-watchdog.sh` | 改 | 待组装方补（与 Actions watchdog 职责边界） | N / N / N | Watchdog |
| `tests/test_quality_gate_contract.py` | **改** | 锁 NOT_READY≠CLEAN；禁 skip 假绿；双触发/组键 | Y / N / N | QG |
| `tests/test_droid_review_pr_rollup_contract.py` | **删**（或改成「信封已删」负向契约） | 信封删除后契约改写 | N / N / Y | Droid |
| `tests/test_droid_review_watchdog_*.py` | **改** | 哨兵：缺席红、不误杀 in_progress | N / N / N | Watchdog |
| `tests/test_droid_review_sharding.py` 等 | **改** | 组键硬化 / 单一写者 / cancel 收尾 | N / N / Y | Droid |
| `tests/test_auto_merge_*.py` / `test_fifo_trust_gates.py` | **改** | FIFO 仅队头；冷却；NOT_READY | N / N / N | FIFO |
| `tests/test_merge_dependency_dag_contract.py` | **改** | 三门载体 DAG / CARRIER 豁免随信封删改 | Y / Y / Y | 其它 |
| `tests/test_evolution_scanner*.py` | **改** | 反查键 + 文件核闸 + 存量帽 | N / N / N | Dedup |
| `tests/test_cheap_gates_green.py` / `test_check_droid_review.py` / `test_ci_structure_contract.py` | **改** | 随短判定与结构契约更新 | 视改动 | 其它 |
| `scripts/quality_gate_judge.py` | **增** | QG 单次判定（`NOT_READY`/`failure`/`success`） | Y / N / N | QG |
| `tests/test_quality_gate_judge.py` | **增** | judge 契约 | Y / N / N | QG |
| `.github/workflows/pr-queue-controller.yml` | **增** | FIFO 队头唯一写者（入队/出队/踢出/冷却） | N / N / N | FIFO |
| `scripts/pr_queue_controller.sh` | **增** | 队头控制器实现 | N / N / N | FIFO |
| `tests/test_pr_queue_controller.py` | **增** | FIFO 冷却/队头契约 | N / N / N | FIFO |
| `scripts/droid_absence_sentinel.sh` | **增** | 缺席哨兵 classify（识 NOT_READY） | N / N / N | Watchdog |
| `tests/test_droid_absence_sentinel.py` | **增** | 哨兵契约 | N / N / N | Watchdog |
| `tests/test_evolution_scanner_dedup_inflight.py` | **增** | Dedup 在飞帽/反查键 | N / N / N | Dedup |

---

## 按模块计数（本清单已填行）

| 模块 | 改 | 删 | 增 | 合计（约） |
|------|----|----|----|------------|
| QG | 3 | 0 | 2 | 5 |
| Droid | 7 | 2 | 0 | 9 |
| Dedup | 5 | 0 | 1 | 6 |
| FIFO | 6 | 0 | 3 | 9 |
| Watchdog | 4 | 0 | 2 | 6 |
| 其它 | 6 | 0 | 0 | 6 |
| **合计** | **31** | **2** | **8** | **41** |

> 计数按上表「意图明确行」聚合；「增」已落路径（见上表与 `ROLLBACK.md` §3）。测试文件按簇合并计数，非整文件逐条。

---

## 高危 Top（组装方优先盯）

见父代理回传「必须删/必须改 Top 15」；本表标 **粗体** 的改|删行为首批。

## 拍板落地占位（组装方填）

| 拍板 | 落地文件 | 回退开关 |
|------|----------|----------|
| B 先行（瘦 Actions `droid-review`） | PRT 聚合 job `name: droid-review`（PR head）；`kick-after-qg` rerun；信封 `droid-review-pr-rollup.yml` **已删**；笔记 `DROID-B-NOTES.md` | 测 PR 进不了 rollup → 转 A（App + 必要时改 `integration_id`） |
| 未齐 = `exit 1` + `NOT_READY` | `scripts/quality_gate_judge.py`（`judge_gates` / `QG_RESULT=`）；`quality-gate.yml` judge step → Checks API 贴 head；契约 `tests/test_quality_gate_judge.py` | 禁改回 skip；FIFO/哨兵须识 NOT_READY≠质量红 |
| 工单反查取键 + 文件核闸 | `evolution_scanner._resolve_dedup_key_from_pr` / `deduplicate`；`evolution_utils.collect_duplicate_file_cores`；`.evolution/config.yml` `max_inflight_prs`；笔记 `DEDUP-NOTES.md` | `EVOLUTION_DEDUP_PR_KEYS=0` / `EVOLUTION_DEDUP_FILE_CORE=0` / `max_inflight_prs:0`；埋键后续；禁改主键为文件集 |

## 合入注脚（大哥点头时明示）

1. **B 未上现网**：合入后须立刻按 `FIRST-TEST-PR.md` 验 GraphQL `statusCheckRollup` + ruleset；不过 → 转 A（App 贴 `droid-review` + 必要时改 `integration_id`），禁止空窗硬拆。
2. **整集进 main**：信封 `droid-review-pr-rollup.yml` 已删；替换须一次性整集合入，禁半截；不合则按 `ROLLBACK.md` 整集打回存档。
3. **病二 hosted 搬家不在本集**：等候类搬 hosted / QG 按 job 定性后再搬，本提交刻意未做；pve 仍可留真干活腿。
