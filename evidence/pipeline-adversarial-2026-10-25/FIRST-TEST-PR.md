# 第一测 PR 计划（验 B：rollup + ruleset）

依据：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md` §1.1 / §3.6。  
前置：整集已合入 `main`（或等价试跑分支已启用新 workflow）；三门仍绑 ruleset `23079535` / `integration_id=15368`。

## 测 PR 形态

- 最小无害改动（如 docs 一行）+ `do-not-merge`（防误合）
- 标题标明 `pipeline-cutover-probe`
- 不依赖旧信封 `droid-review-pr-rollup.yml`（仓内应已不存在）

## 验收步骤

1. **PRT 先出 `droid-review`**：打开/同步后 `pull_request_target` 应出现同名 check（挂 **PR head**）。QG 未齐时结论多为 **failure + NOT_READY**（禁止 `skipped`）。
2. **廉价门 + QG**：CI/QA completed → `quality-gate` 单次判定；未齐 `NOT_READY`（title 含 `NOT_READY`）；双绿 → success，Checks API 贴到 head。
3. **kick**：QG success → `kick-after-qg` 按 PR 号 `gh run rerun` PRT；无长等、无刮日志。
4. **rollup**：GraphQL `statusCheckRollup` 含 Actions 产出的 `droid-review`（`integration_id=15368`）。
5. **ruleset**：他门绿时，该 check 足以单独构成/解除 BLOCK（无「required 缺席」空窗）。
6. **并发**：同 PR 新 head 互 cancel；异 PR 不互砍。
7. **负向**：仓内无 `droid-review-pr-rollup.yml`、无 mirror 双写同名。

## 不过 → 转 A

- App（factory-droid/cursor）贴 required `droid-review` + 必要时改 ruleset `integration_id`
- B 失败不得硬拆、不得空窗（见 FINAL-PLAN §1.1）

## FIFO / 哨兵冒烟（同测或第二测）

- 打 `merge-queue` 后仅队头可进 mergeable；冷却 label 生效
- QG `NOT_READY` → triage `pending`（不升级 stalled）
- 缺席超 TTL → watchdog `absence-sentinel` 显式红 `droid-review`
