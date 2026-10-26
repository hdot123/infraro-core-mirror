# archive-pre-cutover · MANIFEST

- 存档时刻：2026-10-25（Asia/Shanghai）· 组装脚手架阶段
- 基线分支：`cursor/pipeline-cutover-assemble`（基于 FINAL-PLAN）
- 权威方案：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md`
- 规则：原样 **复制**（相对路径保持不变）；**未**改 `.github/workflows` 业务内容
- 用途：替换合入失败或大哥要求整集回滚时，从此目录打回（见同级 `../ROLLBACK.md`）

## 原路径 → 存档路径

| 原路径 | 存档路径 |
|--------|----------|
| `.github/workflows/quality-gate.yml` | `archive-pre-cutover/.github/workflows/quality-gate.yml` |
| `.github/workflows/droid-review.yml` | `archive-pre-cutover/.github/workflows/droid-review.yml` |
| `.github/workflows/droid-review-pr-rollup.yml` | `archive-pre-cutover/.github/workflows/droid-review-pr-rollup.yml` |
| `.github/workflows/droid-review-shards.yml` | `archive-pre-cutover/.github/workflows/droid-review-shards.yml` |
| `.github/workflows/droid-review-watchdog.yml` | `archive-pre-cutover/.github/workflows/droid-review-watchdog.yml` |
| `.github/workflows/droid-review-watchdog-handlers.yml` | `archive-pre-cutover/.github/workflows/droid-review-watchdog-handlers.yml` |
| `.github/workflows/auto-merge.yml` | `archive-pre-cutover/.github/workflows/auto-merge.yml` |
| `.github/workflows/auto-merge-pipeline.yml` | `archive-pre-cutover/.github/workflows/auto-merge-pipeline.yml` |
| `.github/workflows/ci.yml` | `archive-pre-cutover/.github/workflows/ci.yml` |
| `.github/workflows/qa.yml` | `archive-pre-cutover/.github/workflows/qa.yml` |
| `.github/workflows/setup-labels.yml` | `archive-pre-cutover/.github/workflows/setup-labels.yml` |
| `.github/workflows/droid-task.yml` | `archive-pre-cutover/.github/workflows/droid-task.yml` |
| `.github/workflows/evolution-scan.yml` | `archive-pre-cutover/.github/workflows/evolution-scan.yml` |
| `.github/workflows/droid-autofix.yml` | `archive-pre-cutover/.github/workflows/droid-autofix.yml` |
| `actions/auto-merge/action.yml` | `archive-pre-cutover/actions/auto-merge/action.yml` |
| `src/infra_core/shell/auto_merge_triage.sh` | `archive-pre-cutover/src/infra_core/shell/auto_merge_triage.sh` |
| `src/infra_core/shell/check_droid_review.sh` | `archive-pre-cutover/src/infra_core/shell/check_droid_review.sh` |
| `scripts/check_droid_review.sh` | `archive-pre-cutover/scripts/check_droid_review.sh` |
| `scripts/check_cheap_gates_green.sh` | `archive-pre-cutover/scripts/check_cheap_gates_green.sh` |
| `scripts/check_zero_red.sh` | `archive-pre-cutover/scripts/check_zero_red.sh` |
| `scripts/droid_review/plan_shards.py` | `archive-pre-cutover/scripts/droid_review/plan_shards.py` |
| `scripts/droid_review/publish_findings.py` | `archive-pre-cutover/scripts/droid_review/publish_findings.py` |
| `scripts/droid_review/run_shard.sh` | `archive-pre-cutover/scripts/droid_review/run_shard.sh` |
| `scripts/droid_review/adjudicated_findings.json` | `archive-pre-cutover/scripts/droid_review/adjudicated_findings.json` |
| `src/infra_core/engine/evolution_scanner.py` | `archive-pre-cutover/src/infra_core/engine/evolution_scanner.py` |
| `src/infra_core/engine/evolution_utils.py` | `archive-pre-cutover/src/infra_core/engine/evolution_utils.py` |
| `.evolution/config.yml` | `archive-pre-cutover/.evolution/config.yml` |
| `webhook-scripts/trigger-ci-droid.sh` | `archive-pre-cutover/webhook-scripts/trigger-ci-droid.sh` |
| `webhook-scripts/ci-timeout-watchdog.sh` | `archive-pre-cutover/webhook-scripts/ci-timeout-watchdog.sh` |
| `tests/test_quality_gate_contract.py` | `archive-pre-cutover/tests/test_quality_gate_contract.py` |
| `tests/test_droid_review_pr_rollup_contract.py` | `archive-pre-cutover/tests/test_droid_review_pr_rollup_contract.py` |
| `tests/test_droid_review_watchdog_contract.py` | `archive-pre-cutover/tests/test_droid_review_watchdog_contract.py` |
| `tests/test_droid_review_watchdog_handlers_workflow.py` | `archive-pre-cutover/tests/test_droid_review_watchdog_handlers_workflow.py` |
| `tests/test_droid_review_watchdog_quota_sweep.py` | `archive-pre-cutover/tests/test_droid_review_watchdog_quota_sweep.py` |
| `tests/test_droid_review_sharding.py` | `archive-pre-cutover/tests/test_droid_review_sharding.py` |
| `tests/test_droid_review_shards_workflow.py` | `archive-pre-cutover/tests/test_droid_review_shards_workflow.py` |
| `tests/test_droid_review_shell_contract.py` | `archive-pre-cutover/tests/test_droid_review_shell_contract.py` |
| `tests/test_auto_merge_workflow_contract.py` | `archive-pre-cutover/tests/test_auto_merge_workflow_contract.py` |
| `tests/test_auto_merge_gate_contract.py` | `archive-pre-cutover/tests/test_auto_merge_gate_contract.py` |
| `tests/test_auto_merge_triage.py` | `archive-pre-cutover/tests/test_auto_merge_triage.py` |
| `tests/test_auto_merge_pipeline_hardening.py` | `archive-pre-cutover/tests/test_auto_merge_pipeline_hardening.py` |
| `tests/test_auto_merge_escape_hatch.py` | `archive-pre-cutover/tests/test_auto_merge_escape_hatch.py` |
| `tests/test_auto_merge_read_write_token_contract.py` | `archive-pre-cutover/tests/test_auto_merge_read_write_token_contract.py` |
| `tests/test_fifo_trust_gates.py` | `archive-pre-cutover/tests/test_fifo_trust_gates.py` |
| `tests/test_merge_dependency_dag_contract.py` | `archive-pre-cutover/tests/test_merge_dependency_dag_contract.py` |
| `tests/test_evolution_scanner.py` | `archive-pre-cutover/tests/test_evolution_scanner.py` |
| `tests/test_evolution_scanner_dedup_nf1.py` | `archive-pre-cutover/tests/test_evolution_scanner_dedup_nf1.py` |
| `tests/test_cheap_gates_green.py` | `archive-pre-cutover/tests/test_cheap_gates_green.py` |
| `tests/test_check_droid_review.py` | `archive-pre-cutover/tests/test_check_droid_review.py` |
| `tests/test_ci_structure_contract.py` | `archive-pre-cutover/tests/test_ci_structure_contract.py` |

**文件数：50**（不含本 MANIFEST.md）

存档根目录（相对仓根）：

`evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover/`
