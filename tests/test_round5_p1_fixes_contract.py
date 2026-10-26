"""Round-5 P1 修复的契约测试（shard-3 / shard-9；shard-4 哨兵面归 PR-B）。

- shard-3（droid-review-shards.yml）：deleted_files.tsv 是「filename\\tremoved」
  形态，行尾锚定的 `\\.md$` 过滤恒不命中 → 删除的 .md 穿透进分片清单。
  锁定：先 cut -f1 剥状态列再滤 .md。
- shard-9（auto_merge_triage.sh + pipeline heredoc 字节锁双载体）：
  eligible_for_update_branch 消费 .isCrossRepository，但单 PR 调用方
  gh pr view --json 字段表缺该字段 → fork 过滤恒 false（fail-open）。
  锁定：单 PR 字段表含 isCrossRepository，且与 triage 输入契约头同步。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SHARDS_YML = REPO_ROOT / ".github" / "workflows" / "droid-review-shards.yml"
PIPELINE_YML = REPO_ROOT / ".github" / "workflows" / "auto-merge-pipeline.yml"
TRIAGE_SH = REPO_ROOT / "src" / "infra_core" / "shell" / "auto_merge_triage.sh"


class TestShardsDeletedDocsFilterStripsStatusColumn:
    """round-5 P1（shard-3）：删除文件过滤必须先剥状态列再滤 .md。"""

    def test_deleted_filter_cuts_status_column_before_md_grep(self) -> None:
        script = SHARDS_YML.read_text(encoding="utf-8")
        assert "cut -f1 deleted_files.tsv | grep -v '\\.md$'" in script, (
            "deleted_files.tsv 逐行形如 filename<TAB>removed，行尾锚定 \\.$ "
            "过滤恒不命中；必须先 cut -f1 剥状态列再滤 .md（shard-3）"
        )

    def test_no_md_grep_directly_on_status_tsv(self) -> None:
        script = SHARDS_YML.read_text(encoding="utf-8")
        # 带状态列的 tsv 不允许被 \\.$ 直接过滤（恒空集形态回归）
        assert not re.search(r"grep -v '\\\.md\$' +deleted_files\.tsv", script), (
            "禁止对含状态列的 deleted_files.tsv 直接做行尾 .md 过滤（shard-3 回归）"
        )


class TestTriageInputContractIsCrossRepository:
    """round-5 P1（shard-9）：fork 过滤字段必须在调用方字段表实有，双载体同步。"""

    FIELD_LIST = "number,mergeable,mergeStateStatus,isDraft,statusCheckRollup,labels,headRefOid,createdAt,isCrossRepository"

    def test_triage_header_contract_includes_is_cross_repository(self) -> None:
        script = TRIAGE_SH.read_text(encoding="utf-8")
        assert self.FIELD_LIST in script, (
            "triage 输入契约头（第 7 行）必须列出 isCrossRepository——"
            "eligible_for_update_branch 消费该字段（shard-9）"
        )

    def test_pipeline_single_pr_field_list_includes_is_cross_repository(self) -> None:
        pipeline = PIPELINE_YML.read_text(encoding="utf-8")
        assert (
            pipeline.count(f'gh pr view "$PR_NUMBER" --repo "$REPO" --json {self.FIELD_LIST}') >= 2
        ), (
            "pipeline 单 PR 路径（初取 + UNKNOWN 重试）的 gh pr view --json 字段表"
            "必须含 isCrossRepository，否则 triage fork 过滤恒 false（shard-9 fail-open）"
        )

    def test_no_field_list_without_is_cross_repository(self) -> None:
        # 单 PR 路径的三门字段表不允许存在缺 isCrossRepository 的旧形态
        pipeline = PIPELINE_YML.read_text(encoding="utf-8")
        stale = [
            m.group(0)
            for m in re.finditer(r"number,mergeable,mergeStateStatus,isDraft,[^\s\"]+", pipeline)
        ]
        assert all("isCrossRepository" in s for s in stale), (
            f"发现缺 isCrossRepository 的旧字段表：{stale}"
        )

    def test_triage_consumes_is_cross_repository_with_false_fallback(self) -> None:
        script = TRIAGE_SH.read_text(encoding="utf-8")
        assert ".isCrossRepository // false" in script, (
            "triage fork 过滤必须消费 isCrossRepository（缺失字段降级 false 即不豁免）"
        )
