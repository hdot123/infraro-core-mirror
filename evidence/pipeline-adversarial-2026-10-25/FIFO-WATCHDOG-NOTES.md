# FIFO + 哨兵并入 watchdog · 组装笔记

定版：2026-10-25 · 模块：合并 FIFO / 哨兵（FINAL-PLAN §2）

## FIFO 三要素

| # | 要素 | 落地 |
|---|------|------|
| 1 | **介质** | label `merge-queue`（在队）/ `merge-queue-cooldown`（踢出冷却） |
| 2 | **排序键** | 在队 PR 按 `createdAt` 升序；队头 = 最老且无 cooldown |
| 3 | **队头唯一写者** | `pr-queue-controller.yml` → `scripts/pr_queue_controller.sh` 独占入队/出队/踢出/冷却；`auto-merge` **只读** `isQueueHead`（舰队查询注入），禁止自称队头直合 |

配套：`auto-merge-pipeline` `max-parallel: 1`（单飞腿）。`mergeable` 分类要求：三门绿 ∧ `merge-queue` ∧ `isQueueHead==true`。

### 禁止项（契约锁）

- 禁止 mergeable 无序扫合（非队头绿 PR → `pending`）
- 禁止快路径（单 PR `workflow_run`）自称队头直合（仍读 label 舰队）

### 踢出 + 冷却

- `HEAD_TIMEOUT_MINUTES` 默认 90（repo var `PR_QUEUE_HEAD_TIMEOUT_MINUTES`）
- 踢出：去 `merge-queue`，打 `merge-queue-cooldown`
- `COOLDOWN_MINUTES` 默认 60（≥ 一个 controller schedule 窗 17,47）
- 冷却起点近似 `updatedAt`（label 事件无廉价精确时戳）

## 哨兵并入点

- **并入** `.github/workflows/droid-review-watchdog.yml` job `absence-sentinel`
- helper：`scripts/droid_absence_sentinel.sh`
- **不另起** `*sentinel*` workflow
- 触发：与 quota-sweep 同面（`schedule` 11,41 + `workflow_dispatch`）
- TTL：`WATCHDOG_ABSENCE_TTL_SECONDS` 默认 **3600** = 2× cron 窗（禁止 30min 空头支票）
- 缺席 → Checks API 贴 `droid-review` `failure` / title `ABSENCE_SENTINEL: not-started`
- 不误杀：`in_progress`/`queued` check 或 Droid Auto Review run → skip
- 不误伤：TTL 内合法排队（`merge-queue` / `droid-review-queue`）→ `skip_ttl`

## NOT_READY 识别点

| 面 | 位置 | 行为 |
|----|------|------|
| 合并守卫 | `auto_merge_triage.sh`：`quality_gate_not_ready` / `red_only_quality_not_ready` | → `pending`（可重试），**不** `stalled` 骂街；ruleset 仍非绿 |
| 富集 | `auto-merge-pipeline.yml` check-runs 投影 `not_ready` 布尔（自 output.title/summary/name 探测 `NOT_READY`） | 不保留大文本，防 E2BIG |
| 哨兵 | `droid_absence_sentinel.sh classify` → `skip_not_ready` | 不升级显式红缺席路径 |

## 与 QG / Droid 模块接口依赖

- **读**：REST check-runs 上 `substrate-gate-suite` / `quality-gate` / `droid-review`（绿判与缺席判定）；QG 须在 NOT_READY 出口把 `NOT_READY` 写入 check output title/summary（本模块只认该信号）
- **不改**：`quality-gate.yml`、`droid-review.yml`（干活腿）本组装不触碰
- **写（哨兵）**：仅在缺席时贴同名 `droid-review` failure；真实 Droid 开工后由其覆盖——与 B 先行「单一写者在干活腿」窗口需在替换清单写明：哨兵是兜底写者，仅 absent 态
- **FIFO 入队触发**：controller 听 `Quality Gate` / `Droid Auto Review` success（快路径入队仍须经 label）

## 文件清单（本模块）

| 路径 | 改\|增 |
|------|--------|
| `.github/workflows/pr-queue-controller.yml` | 增 |
| `scripts/pr_queue_controller.sh` | 增 |
| `.github/workflows/auto-merge-pipeline.yml` | 改（isQueueHead / not_ready / triage heredoc） |
| `src/infra_core/shell/auto_merge_triage.sh` | 改（FIFO 队头 + NOT_READY） |
| `.github/workflows/droid-review-watchdog.yml` | 改（absence-sentinel job） |
| `scripts/droid_absence_sentinel.sh` | 增 |
| `tests/test_pr_queue_controller.py` | 增 |
| `tests/test_droid_absence_sentinel.py` | 增 |
| `tests/test_fifo_trust_gates.py` | 改 |
| `tests/test_auto_merge_triage.py` | 改（默认队头字段） |
| `tests/test_droid_review_watchdog_contract.py` | 改（第四 job） |
