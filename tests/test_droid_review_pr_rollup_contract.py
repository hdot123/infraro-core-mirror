"""B 先行：droid-review-pr-rollup.yml 信封已删（负向契约）。

旧 #420 Option A 信封（pull_request + 200×30s 长等 + 刮 preflight 日志认 PR）
已退役。原生 `droid-review` check 改由 droid-review.yml 的 PRT 干活腿产出。
本文件锁「信封文件不存在 + 禁长等/刮日志形态回潮」。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLLUP_YML = REPO_ROOT / ".github/workflows/droid-review-pr-rollup.yml"
SHELL_YML = REPO_ROOT / ".github/workflows/droid-review.yml"


class TestDroidReviewEnvelopeRetired:
    def test_rollup_workflow_file_deleted(self) -> None:
        assert not ROLLUP_YML.exists(), (
            "droid-review-pr-rollup.yml 信封必须删除（B 先行；存档见 "
            "evidence/pipeline-adversarial-2026-10-25/archive-pre-cutover/）"
        )

    def test_shell_has_no_rollup_wait_or_log_scrape(self) -> None:
        raw = SHELL_YML.read_text(encoding="utf-8")
        for banned in (
            "MAX_ATTEMPTS=200",
            "Cheap-gates preflight for PR #",  # rollup 刮日志关联句式
            "event=workflow_run",  # 信封内轮询 raw workflow_run 列表
            "actions/jobs/",  # 刮 job logs
        ):
            assert banned not in raw, f"瘦形态禁残留：{banned!r}"
        # mirror job 不得作为可调度 job 复活（注释提及删除名允许）
        assert "\n  pr-head-check-mirror:" not in raw
        assert "name: pr-head-check-mirror" not in raw

    def test_shell_is_sole_droid_review_check_producer(self) -> None:
        raw = SHELL_YML.read_text(encoding="utf-8")
        assert "name: droid-review" in raw
        assert "pull_request_target" in raw
        assert "kick-after-qg" in raw
        assert "event=pull_request_target" in raw  # kick 按 PRT run 关联，非刮日志
