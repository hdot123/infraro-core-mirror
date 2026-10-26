# 病四铸卡去重 · 落地笔记（2026-10-25）

权威：`docs/guides/pr-pipeline-cutover-FINAL-PLAN.md` §1.3；设计补充：`disease-4-design-supplement.md`。

## 落地处

| 能力 | 位置 |
|------|------|
| 主键归一化 | `evolution_utils._normalize_dedup_location`（未改语义） |
| intra-tick 双侧归一化 | `evolution_utils.dedup_intra_tick` |
| 文件核提取 | `evolution_utils._extract_duplicate_file_core` / `collect_duplicate_file_cores` |
| PR 反查取键 | `evolution_scanner._resolve_dedup_key_from_pr` ← `get_open_prs_dedup_keys` |
| 精确键 ∪ PR 键 + 文件核闸 | `evolution_scanner.deduplicate` |
| 存量帽 | `main()` 读 `max_inflight_prs` / `_resolve_max_inflight_prs`；配置 `.evolution/config.yml` |
| 必填键登记 | `evolution_utils.REQUIRED_CONFIG_KEYS` + onboarding 示例 |

### 反查路径（PR → issue 两行键）

1. **直读** PR body `**Rule ID**` / `**Location**`（埋键后续加固；本集不挡）
2. **`Closes #N` / `Fixes #N`** → `gh issue view` → `_parse_issue_fields`
3. **`Fixes INFRA-N` / 分支名 `INFRA-N`** → `gh issue list --search INFRA-N --label evolution-found` → 解析 body 两行（**不调 Linear API**）
4. 失败 → **不进抑制集** + 日志 `suppressed_by_inflight_pr_unkeyed++`

### 文件核闸

- 仅 `CODE_HYGIENE_DUPLICATE_BLOCK` 且同文件对（`path::La <-> path::Lb`）
- 文件核 = `{path}`；与 open issue ∪ 已键 PR 的文件核相交 → `suppressed_by_inflight_file`
- **不**用文件集当主键

## 回退 / 开关

| 开关 | 效果 |
|------|------|
| `EVOLUTION_DEDUP_PR_KEYS=0` | 关闭 PR 反查抑制（`get_open_prs_dedup_keys` 直接空集） |
| `EVOLUTION_DEDUP_FILE_CORE=0` | 关闭同文件 DUPLICATE 文件核附加闸 |
| `EVOLUTION_MAX_INFLIGHT_PRS=0` | 关闭存量帽；正整数覆盖 config |
| `max_inflight_prs: 0`（config） | 同关帽（无 env 时） |

埋键（修复 PR body 复制 issue 两行）**不挡本集**；直读路径已留好，写端契约（droid-task / autofix 模板）单列 follow-up。

## 聚焦测试

```bash
pytest tests/test_evolution_scanner_dedup_inflight.py tests/test_evolution_scanner_dedup_nf1.py -x -q
```
