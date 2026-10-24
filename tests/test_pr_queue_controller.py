"""Contract tests for PR queue controller (processing label + workflow shell)."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from tests.shellcheck_helpers import assert_shellcheck_clean

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = REPO_ROOT / ".github/workflows/pr-queue-controller.yml"
SCRIPT = REPO_ROOT / "scripts/pr_queue_controller.sh"
GUIDE = REPO_ROOT / "docs/guides/pr-queue-processing.md"
ADVERSARIAL = REPO_ROOT / "docs/guides/pr-queue-adversarial-review.md"
SETUP_LABELS = REPO_ROOT / ".github/workflows/setup-labels.yml"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestPrQueueControllerWorkflow:
    def test_workflow_exists_and_ubuntu_only(self) -> None:
        data = _load_yaml(CONTROLLER)
        assert data["name"] == "PR Queue Controller"
        job = data["jobs"]["reconcile"]
        assert job["runs-on"] == "ubuntu-latest"

    def test_no_pr_head_checkout(self) -> None:
        raw = CONTROLLER.read_text(encoding="utf-8")
        assert "pull_request.head" not in raw or "default_branch" in raw
        steps = _load_yaml(CONTROLLER)["jobs"]["reconcile"]["steps"]
        checkout = steps[0]
        assert checkout["uses"].startswith("actions/checkout")
        assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"

    def test_concurrency_group(self) -> None:
        data = _load_yaml(CONTROLLER)
        assert data["concurrency"]["group"] == "infraro-pr-queue-controller"
        assert data["concurrency"]["cancel-in-progress"] is False

    def test_invokes_script(self) -> None:
        raw = CONTROLLER.read_text(encoding="utf-8")
        assert "scripts/pr_queue_controller.sh" in raw

    def test_workflow_run_watch_list(self) -> None:
        data = _load_yaml(CONTROLLER)
        triggers = data.get(True, {}) or data.get("on", {})
        wr = triggers["workflow_run"]
        names = set(wr["workflows"])
        assert names == {"CI", "QA", "Droid Auto Review", "Quality Gate"}

    def test_merge_permissions(self) -> None:
        perms = _load_yaml(CONTROLLER)["permissions"]
        assert perms["contents"] == "write"
        assert perms["pull-requests"] == "write"
        assert perms["checks"] == "read"

    def test_dispatch_token_required_for_merge(self) -> None:
        raw = CONTROLLER.read_text(encoding="utf-8")
        assert "DISPATCH_TOKEN" in raw
        assert "secrets.DISPATCH_TOKEN" in raw
        assert "::error::" in raw or "fail-loud" in raw.lower() or "required" in raw
        assert "pr_queue_controller.sh" in raw
        assert "${{ secrets.DISPATCH_TOKEN }}" in raw

    def test_workflow_links_processing_guide(self) -> None:
        raw = CONTROLLER.read_text(encoding="utf-8")
        assert "docs/guides/pr-queue-processing.md" in raw


class TestProcessingLabelInSetup:
    def test_processing_label_default(self) -> None:
        raw = SETUP_LABELS.read_text(encoding="utf-8")
        assert '"processing"' in raw or "'processing'" in raw

    def test_queue_jump_label_default(self) -> None:
        raw = SETUP_LABELS.read_text(encoding="utf-8")
        assert "queue-jump" in raw


class TestPrQueueProcessingGuide:
    def test_guide_covers_state_machine(self) -> None:
        raw = GUIDE.read_text(encoding="utf-8")
        assert "required_checks_rc" in raw
        assert "candidate_for_processing_label" in raw
        assert "全绿不回贴" in raw
        assert "冻队" in raw
        assert "wait_main_push" in raw or "main push" in raw

    def test_guide_covers_queue_jump(self) -> None:
        raw = GUIDE.read_text(encoding="utf-8")
        assert "queue-jump" in raw
        assert "不抢占" in raw
        assert "pick_next_processing_candidate" in raw
        assert "Setup Labels" in raw

    def test_guide_authoritative_semantics_table(self) -> None:
        raw = GUIDE.read_text(encoding="utf-8")
        assert "权威交付语义对照" in raw
        assert "已覆盖" in raw
        assert "零人工" in raw or "pipeline-maintained" in SCRIPT.read_text(encoding="utf-8")

    def test_guide_covers_main_push_wait(self) -> None:
        raw = GUIDE.read_text(encoding="utf-8")
        assert "main push" in raw.lower() or "main push" in raw
        assert "main_push_checks_rc" in raw
        assert "合入失败" in raw
        assert "流程结束" in raw

    def test_script_points_at_guide(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "docs/guides/pr-queue-processing.md" in raw

    def test_adversarial_review_doc(self) -> None:
        raw = ADVERSARIAL.read_text(encoding="utf-8")
        assert "遗漏 Top 5" in raw
        assert "交付设计核对" in raw or "权威语义" in raw
        assert "P0" in raw and "P1" in raw
        assert "workflow_dispatch" in raw
        assert "main push" in raw.lower() or "main push" in raw
        assert "Merge queue" in raw or "merge_group" in raw

    def test_check_runs_paginate(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "check-runs" in raw and "--paginate" in raw


class TestPrQueueScript:
    def test_script_documents_required_checks(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "substrate-gate-suite" in raw
        assert "quality-gate" in raw
        assert "droid-review" in raw
        assert "dependabot" in raw

    def test_reconcile_phases(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        for phase in (
            "PR_QUEUE_RECONCILE_PHASE: sanitize",
            "PR_QUEUE_RECONCILE_PHASE: expire queue-jump",
            "PR_QUEUE_RECONCILE_PHASE: queue-head",
            "PR_QUEUE_RECONCILE_PHASE: assign",
        ):
            assert phase in raw
        assert "reconcile_phase_auto_release_merged_holders" in raw
        assert "reconcile_phase_sanitize_labels" in raw
        assert "reconcile_phase_expire_queue_jumps" in raw
        assert "reconcile_phase_queue_head" in raw
        assert "reconcile_phase_assign_processing" in raw

    def test_required_checks_rc_contract(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "required_checks_rc" in raw
        assert "freeze queue" in raw or "queue frozen" in raw
        assert "must NOT receive" in raw or "prevents #358 relabel" in raw

    def test_no_relabel_when_head_all_green(self) -> None:
        """全绿出队后不得在同一 reconcile 轮把 processing 贴回同一 PR。"""
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "candidate_for_processing_label" in raw
        assert "pick_next_processing_candidate" in raw
        assert "skip processing assignment" in raw

    def test_queue_jump_no_preemption(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert 'QUEUE_JUMP_LABEL="queue-jump"' in raw
        assert "No preemption" in raw or "does not preempt" in raw
        assert "has_queue_jump" in raw
        assert "assign_processing_to_pr" in raw
        assert "consumed $QUEUE_JUMP_LABEL" in raw
        assert "queue-jump priority" in raw
        assert "processing_holder_count" in raw
        assert "choose_processing_holder_to_keep" in raw
        assert "remove_queue_jump" in raw
        assert "remove_processing" in raw
        # expire path must only strip queue-jump, never processing on other PRs
        expire_block = raw.split("reconcile_phase_expire_queue_jumps")[1].split(
            "reconcile_phase_queue_head"
        )[0]
        assert "remove_queue_jump" in expire_block
        assert "remove_processing" not in expire_block

    def test_auto_squash_merge_anchor(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "attempt_squash_merge" in raw
        assert "advance_all_green_queue_head" in raw
        assert "wait_main_push_then_maybe_release" in raw
        assert "gh pr merge" in raw and "--squash" in raw
        assert "GH_MERGE_TOKEN" in raw
        assert 'GH_TOKEN="$GH_MERGE_TOKEN" gh pr merge' in raw
        assert "pr-queue-merge-failed-" in raw
        assert "merge_failure_comment_exists" in raw
        assert "MAIN_PUSH_REQUIRED_CHECKS" in raw
        assert "main_push_checks_rc" in raw
        assert "release_processing_slot" in raw

    def test_log_writes_stderr_not_stdout(self) -> None:
        """P0: command substitution must capture only JSON/numbers, not log lines."""
        snippet = r"""
        set -euo pipefail
        log() { echo "[pr-queue] $*" >&2; }
        pick_next_processing_candidate() {
          log "assign processing to PR #999 (queue-jump priority)"
          echo '{"number":42,"headRefOid":"abc"}'
        }
        captured=$(pick_next_processing_candidate)
        printf '%s' "$captured" | python3 -c "import json,sys; json.load(sys.stdin)"
        """
        proc = subprocess.run(
            ["bash", "-c", snippet],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert "[pr-queue]" in proc.stderr
        assert "[pr-queue]" not in proc.stdout

    def test_script_log_function_uses_stderr(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert 'log() { echo "[pr-queue] $*" >&2; }' in raw

    def test_merge_failure_keeps_processing(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "keep processing (queue frozen)" in raw
        assert "仍保留" in raw
        assert "dequeue anyway" not in raw

    def test_merged_pr_processing_tracked(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "fetch_processing_labeled_prs" in raw
        assert "--state all" in raw

    def test_failed_checks_keep_processing(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        assert "queue frozen" in raw or "failing required checks" in raw

    def test_shellcheck_clean(self) -> None:
        if not shutil.which("shellcheck"):
            pytest.skip("shellcheck not installed")
        assert_shellcheck_clean(SCRIPT)

    def test_required_checks_rc_pending_does_not_enable_errexit_trap(self) -> None:
        """Pending (rc=1) must not re-enable errexit before returning from check helpers."""
        raw = SCRIPT.read_text(encoding="utf-8")
        req_block = raw.split("required_checks_rc()")[1].split("main_push_checks_rc")[0]
        assert "set -e" not in req_block.split("rc=$?")[1].split("return")[0]
        checks_block = raw.split("checks_list_rc_for_names()")[1].split("required_checks_rc")[0]
        assert "set +e" in checks_block
        assert "return 1" in checks_block
        assert "return 0\n}" in checks_block

    def test_checks_list_skipped_treated_as_pending_not_failure(self) -> None:
        raw = SCRIPT.read_text(encoding="utf-8")
        block = raw.split("checks_list_rc_for_names()")[1].split("required_checks_rc")[0]
        assert "skipped" in block
        assert "set +e" in block and "return 1" in block


class TestPrQueueControllerShellIntegration:
    """Behavioral tests with a stub gh — pending checks must exit 0 from main."""

    @staticmethod
    def _write_gh_mock(bin_dir: Path, *, check_conclusion: str = "skipped") -> None:
        gh = bin_dir / "gh"
        gh.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                c="{check_conclusion}"
                if [[ "$1" == "api" ]]; then
                  for arg in "$@"; do
                    if [[ "$arg" == *check-runs* ]]; then
                      printf '{{"check_runs":[{{"name":"substrate-gate-suite","status":"completed","conclusion":"%s","started_at":"2026-01-01T00:00:00Z"}},{{"name":"quality-gate","status":"completed","conclusion":"%s","started_at":"2026-01-01T00:00:01Z"}},{{"name":"droid-review","status":"completed","conclusion":"%s","started_at":"2026-01-01T00:00:02Z"}}]}}\\n' "$c" "$c" "$c"
                      exit 0
                    fi
                    if [[ "$arg" == *"/pulls?state=open"* ]]; then
                      echo '{{"number":99,"created_at":"2026-01-01T00:00:00Z","headRefOid":"abc123","isDraft":false,"isCrossRepository":false,"author":"dev","labels":[{{"name":"processing"}}]}}'
                      exit 0
                    fi
                  done
                fi
                if [[ "$1" == "pr" && "$2" == "list" ]]; then
                  if [[ "$*" == *"--jq"*"length"* ]]; then
                    echo 1
                    exit 0
                  fi
                  echo '{{"number":99,"created_at":"2026-01-01T00:00:00Z","state":"OPEN","labels":[{{"name":"processing"}}]}}'
                  exit 0
                fi
                if [[ "$1" == "pr" && "$2" == "view" ]]; then
                  echo '{{"number":99,"state":"OPEN","headRefOid":"abc123","author":{{"login":"dev"}},"isDraft":false,"isCrossRepository":false,"labels":[{{"name":"processing"}}]}}'
                  exit 0
                fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def test_reconcile_exits_zero_when_head_checks_skipped_pending(self, tmp_path: Path) -> None:
        mock_bin = tmp_path / "bin"
        mock_bin.mkdir()
        self._write_gh_mock(mock_bin, check_conclusion="skipped")
        env = {
            **subprocess.os.environ,
            "PATH": f"{mock_bin}:{subprocess.os.environ['PATH']}",
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), "test/repo", "token", "merge-token"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert "checks not complete" in proc.stderr
        assert "queue head PR #99" in proc.stderr

    def test_reconcile_handoff_assign_after_main_push_green(self, tmp_path: Path) -> None:
        """Merged holder + green main push releases slot; next FIFO PR gets processing."""
        mock_bin = tmp_path / "bin"
        mock_bin.mkdir()
        state_file = tmp_path / "queue_state"
        state_file.write_text("merged_wait", encoding="utf-8")
        gh = mock_bin / "gh"
        gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                state_file="$QUEUE_STATE_FILE"
                mode=$(cat "$state_file")
                if [[ "$1" == "api" ]]; then
                  for arg in "$@"; do
                    if [[ "$arg" == *check-runs* ]]; then
                      if [[ "$arg" == *"merge999"* ]]; then
                        printf '{"check_runs":[{"name":"substrate-gate-suite","status":"completed","conclusion":"success","started_at":"2026-01-01T00:00:00Z"},{"name":"quality-gate","status":"completed","conclusion":"success","started_at":"2026-01-01T00:00:01Z"}]}\\n'
                      else
                        printf '{"check_runs":[{"name":"substrate-gate-suite","status":"completed","conclusion":"skipped","started_at":"2026-01-01T00:00:00Z"},{"name":"quality-gate","status":"completed","conclusion":"skipped","started_at":"2026-01-01T00:00:01Z"},{"name":"droid-review","status":"completed","conclusion":"skipped","started_at":"2026-01-01T00:00:02Z"}]}\\n'
                      fi
                      exit 0
                    fi
                    if [[ "$arg" == *"/pulls?state=open"* ]]; then
                      echo '{"number":100,"created_at":"2026-01-02T00:00:00Z","headRefOid":"def456","isDraft":false,"isCrossRepository":false,"author":"dev","labels":[]}'
                      exit 0
                    fi
                  done
                fi
                if [[ "$1" == "pr" && "$2" == "list" ]]; then
                  if [[ "$*" == *"--label processing"* ]]; then
                    if [[ "$*" == *"--jq"*"length"* ]]; then
                      if [[ "$mode" == "merged_wait" ]]; then
                        echo 1
                      else
                        echo 0
                      fi
                      exit 0
                    fi
                    if [[ "$mode" == "merged_wait" ]]; then
                      echo '{"number":99,"created_at":"2026-01-01T00:00:00Z","state":"MERGED","labels":[{"name":"processing"}]}'
                    fi
                    exit 0
                  fi
                  exit 0
                fi
                if [[ "$1" == "pr" && "$2" == "view" ]]; then
                  num="$3"
                  if [[ "$num" == "99" ]]; then
                    echo '{"number":99,"state":"MERGED","mergeCommit":{"oid":"merge999"},"headRefOid":"abc123","author":{"login":"dev"},"isDraft":false,"isCrossRepository":false,"labels":[{"name":"processing"}]}'
                  else
                    echo '{"number":100,"state":"OPEN","headRefOid":"def456","author":{"login":"dev"},"isDraft":false,"isCrossRepository":false,"labels":[]}'
                  fi
                  exit 0
                fi
                if [[ "$1" == "pr" && "$2" == "edit" && "$*" == *"--remove-label processing"* ]]; then
                  echo released > "$state_file"
                  exit 0
                fi
                if [[ "$1" == "pr" && "$2" == "edit" && "$*" == *"--add-label processing"* ]]; then
                  echo assigned >> "$state_file"
                  exit 0
                fi
                exit 0
                """
            ),
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = {
            **subprocess.os.environ,
            "PATH": f"{mock_bin}:{subprocess.os.environ['PATH']}",
            "QUEUE_STATE_FILE": str(state_file),
        }
        proc = subprocess.run(
            ["bash", str(SCRIPT), "test/repo", "token", "merge-token"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert proc.returncode == 0, proc.stderr
        assert "main push green" in proc.stderr
        assert "assign processing to PR #100" in proc.stderr
        assert "assigned" in state_file.read_text(encoding="utf-8")
