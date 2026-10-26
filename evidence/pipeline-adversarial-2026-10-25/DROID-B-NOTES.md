# DROID-B-NOTES · ③ B 先行落地说明（2026-10-25）

依据：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md` §1.1（B）、§2 ③ Droid / 信封 mirror。

## B 形态（已落地）

1. **触发**
   - `pull_request_target`（main）：干活腿 + 聚合 job `name: droid-review`（挂 PR head → 可进 GraphQL `statusCheckRollup` / ruleset `integration_id=15368`）
   - `workflow_run(Quality Gate success)`：仅 `kick-after-qg` —— 按 **PR 号** 查 PRT run 并 `gh run rerun`（零长等、零刮 job 日志）
   - `workflow_dispatch`：手工补跑（仍听 pr_number）
   - **禁** `pull_request`（PR-branch yaml 不可信带 secret）

2. **产出 `droid-review` check**
   - 唯一写者：`droid-review.yml` → job `droid-review`（PRT/dispatch）
   - QG 未齐：单次判定 → `NOT_READY` → job **failure**（禁 skip 假绿）；QG success 后 kick rerun
   - 已有 success：短退 exit 0（不重跑 LLM）

3. **concurrency**
   - `droid-review-pr-${PR||orphan-run_id}`
   - **禁**回落 `head_branch` / `github.ref`（防 main 桶跨 PR 互砍）
   - `cancel-in-progress: true`：仅同 PR 新 head / 新 kick 取代

## 信封 / mirror 结论：**删（无并存窗）**

| 路径 | 处置 | 理由 |
|------|------|------|
| `.github/workflows/droid-review-pr-rollup.yml` | **已删** | 200×30s 长等 + 刮 preflight 日志认 PR（病三）；B 由 PRT 原生贴 |
| `pr-head-check-mirror` job | **已删** | 观察-only 双写残留；B 单一写者 |
| 存档 | `archive-pre-cutover/.github/workflows/droid-review-pr-rollup.yml` | 回滚用 |

无短暂并存窗——替换集进 main 前本地已单一写者，避免 R1-B3 双写同名。

## 联动依赖（本代理未改；组装汇总须跟）

| 文件 | 为何 |
|------|------|
| `tests/test_merge_dependency_dag_contract.py` | 去掉 `droid-review-pr-rollup.yml` GATE_CARRIER / CARRIER_SAME_SOURCE_EXEMPT / 同名断言 |
| `tests/test_quality_gate_contract.py` | 注释/断言不再要求 `droid-review-pr-rollup.yml` 字面存在 |
| `tests/test_h1_alwayscheck_earlyexit.py` | 去掉 `pr-head-check-mirror` checks:write 例外 |
| `evidence/.../REPLACEMENT-INVENTORY.md` | 信封行标「已删」；B 落地处指向本 NOTES |
| Watchdog 代理 | kick 找不到 PRT run 时 exit 1 → 哨兵应识别缺席/kick 失败并显式红；**勿**把 `NOT_READY` 当质量红升级骂街 |

## 转 A 条件

测 PR：`droid-review` **未**进 `statusCheckRollup` 或 ruleset `23079535` 仍 BLOCK（他门已绿）→ 停 B，改 App 贴 + 必要时改 `integration_id`（FINAL-PLAN §1.1）。

## 测 PR 验收要点

1. 打开测 PR → PRT 先落 `droid-review`（多为 `NOT_READY` 红，非 skip）
2. QG success → `kick-after-qg` rerun PRT → 真审查 → 同名 check 终态 success/failure
3. GraphQL `statusCheckRollup` 含 `droid-review`（Actions / 15368）
4. ruleset 在他门绿时对该 check 单独可推非 BLOCK
5. 同 PR 新 push：旧腿 cancel；异 PR 互不 cancel
6. 仓内无 `droid-review-pr-rollup.yml`、无 mirror job、无同名双写
