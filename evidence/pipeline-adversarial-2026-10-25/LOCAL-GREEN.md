# LOCAL-GREEN · 流水线 cutover 本地聚焦绿证明（§3）

- **日期**：2026-10-25
- **工作树**：`.worktrees/pipeline-cutover-assemble`
- **分支**：`cursor/pipeline-cutover-assemble`（未 commit）
- **结论**：**全绿** — `557 passed` / `0 failed` / `0 error`

## 命令

```bash
pytest -q --tb=line \
  tests/test_quality_gate_contract.py \
  tests/test_quality_gate_judge.py \
  tests/test_droid_review_shell_contract.py \
  tests/test_droid_review_pr_rollup_contract.py \
  tests/test_droid_review_sharding.py \
  tests/test_droid_review_auto_resolve_job.py \
  tests/test_droid_review_shards_workflow.py \
  tests/test_check_droid_review.py \
  tests/test_droid_review_fork_guard.py \
  tests/test_droid_review_watchdog_contract.py \
  tests/test_droid_absence_sentinel.py \
  tests/test_pr_queue_controller.py \
  tests/test_fifo_trust_gates.py \
  tests/test_auto_merge_triage.py \
  tests/test_auto_merge_escape_hatch.py \
  tests/test_auto_merge_pipeline_hardening.py \
  tests/test_evolution_scanner_dedup_inflight.py \
  tests/test_evolution_scanner_dedup_nf1.py \
  tests/test_evolution_scanner.py::test_validate_config_requires_max_inflight_prs \
  tests/test_merge_dependency_dag_contract.py \
  tests/test_h1_alwayscheck_earlyexit.py
```

## 摘要

| 项 | 值 |
|----|----|
| collected | 557 |
| passed | **557** |
| failed / error / skipped | 0 / 0 / 0 |
| 墙钟 | ~63s |
| Python | 3.12.13 |
| pytest | 8.4.2 |

### 本轮 sharding 收口

- 目标：`tests/test_droid_review_sharding.py::TestSeamIntegration::test_29_deleted_file_path_accepted_from_base_side`
- 现象：受限沙箱下 `git init` 写 `.git/hooks` / `.git/config` → `CalledProcessError`；`mktemp` 树清理 → `PermissionError`
- 真修：`fixture_git_repo` 改空 template + 隔离 `GIT_CONFIG_*` + teardown `chmod u+w`；test_29 用 `tmp_path` 双 work-tree 替代 `mktemp`；仍禁止写 `.git` 时显式 `pytest.skip`（本机全权限跑通为 pass，非 skip）

### lint（改动 py）

```bash
ruff check tests/test_droid_review_sharding.py   # All checks passed
ruff format tests/test_droid_review_sharding.py  # 1 file left unchanged
```

## 未覆盖项（本证明不声称）

- 全仓 / nightly 全量 suite（按 AGENTS：全门在 CI·QA，本地不跑）
- hosted 搬家（病二）与生产 ruleset 实绑演练
- 第一测 PR 上的 rollup / GraphQL / FIFO 冒烟（见 `FIRST-TEST-PR.md`）
- 未在本命令列表中的契约（如 `test_ci_structure_contract`、watchdog handlers/quota-sweep 全文件等）
- commit / push / 合入 main（待大哥看清单）
