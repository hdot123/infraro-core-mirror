"""#420 Option A：droid-review-pr-rollup.yml — native head rollup carrier.

Locks the thin pull_request workflow that produces the required check name
``droid-review`` on PR head without secret-bearing legs:

* pull_request only (no workflow_run / dispatch / pull_request_target)
* job key + display name literally ``droid-review``
* BASE (base.sha) checkout only — dual-checkout proof; no HEAD scripts
* waits for cheap gates then raw Droid Auto Review workflow_run
* skipped ≠ success (raw skipped/cancelled never exit 0)
* minimal permissions (contents/actions/checks read — no Factory/BYOM secrets)
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLLUP_YML = REPO_ROOT / ".github/workflows/droid-review-pr-rollup.yml"


def _load() -> dict[str, Any]:
    return yaml.safe_load(ROLLUP_YML.read_text(encoding="utf-8"))


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get(True) or doc.get("on") or {}


def _job_runs(job: dict[str, Any]) -> str:
    return "\n".join(str(s["run"]) for s in (job.get("steps") or []) if "run" in s)


class TestDroidReviewPrRollupContract:
    def test_workflow_name(self) -> None:
        assert _load()["name"] == "Droid Review PR Rollup"

    def test_pull_request_only(self) -> None:
        triggers = _triggers(_load())
        assert set(triggers) == {"pull_request"}
        pr = triggers["pull_request"]
        assert pr["branches"] == ["main"]
        for banned in ("workflow_run", "workflow_dispatch", "pull_request_target", "push"):
            assert banned not in triggers

    def test_job_named_droid_review(self) -> None:
        jobs = _load()["jobs"]
        assert list(jobs) == ["droid-review"]
        assert jobs["droid-review"].get("name") == "droid-review"

    def test_same_source_open_guard(self) -> None:
        job_if = " ".join(str(_load()["jobs"]["droid-review"].get("if", "")).split())
        assert "github.event.pull_request.head.repo.full_name == github.repository" in job_if
        assert "github.event.pull_request.state == 'open'" in job_if

    def test_base_sha_checkout_only(self) -> None:
        job = _load()["jobs"]["droid-review"]
        checkouts = [
            s
            for s in (job.get("steps") or [])
            if str(s.get("uses", "")).startswith("actions/checkout@")
        ]
        assert len(checkouts) == 1, "exactly one checkout (BASE only)"
        assert checkouts[0]["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
        runs = _job_runs(job)
        assert "expected BASE checkout" in runs
        assert "refusing HEAD checkout" in runs
        assert "scripts/check_cheap_gates_green.sh" in runs

    def test_minimal_permissions_no_secrets(self) -> None:
        doc = _load()
        assert doc.get("permissions") == {"contents": "read"}
        perms = doc["jobs"]["droid-review"]["permissions"]
        assert perms == {"contents": "read", "actions": "read", "checks": "read"}
        assert "write" not in str(perms.values())
        raw = ROLLUP_YML.read_text(encoding="utf-8")
        for secret in ("FACTORY_API_KEY", "GO_GITHUB_RUN_TOKEN", "WORKER_CI_KEY", "secrets."):
            # GITHUB_TOKEN is allowed; other repo secrets are not
            if secret == "secrets.":
                assert "secrets.FACTORY" not in raw
                assert "secrets.GO_GITHUB" not in raw
                assert "secrets.WORKER" not in raw
            else:
                assert secret not in raw

    def test_skipped_never_maps_to_success(self) -> None:
        runs = _job_runs(_load()["jobs"]["droid-review"])
        assert "skipped" in runs
        assert "not success" in runs or "keep waiting" in runs
        # Forbidden fabricated green from older QG mirror revisions
        assert '[ "$DJ_CONCLUSION" = "success" ] || [ "$DJ_CONCLUSION" = "skipped" ]' not in runs
        assert "exit 0" in runs  # success path exists
        assert "DJ_CONCLUSION" in runs
        # cheap-gates not ready / failed → red (skipped≠success for required context)
        assert "cheap gates" in runs and "stays red" in runs
        assert "cheap gate failed or skipped" in runs

    def test_waits_for_real_workflow_run_droid(self) -> None:
        runs = _job_runs(_load()["jobs"]["droid-review"])
        assert "droid-review.yml/runs" in runs
        assert "event=workflow_run" in runs
        assert "Cheap-gates preflight for PR #" in runs
        assert "MAX_ATTEMPTS=200" in runs
        assert "transient gh api error" in runs
        assert _load()["jobs"]["droid-review"].get("timeout-minutes", 0) >= 120
        # #420 P1: cache dismissed run_ids — do not re-download logs every attempt
        assert "DISMISSED_FILE" in runs
        assert "MATCHED_RUN_ID" in runs

    def test_no_shard_or_secret_bearing_body(self) -> None:
        raw = ROLLUP_YML.read_text(encoding="utf-8")
        for banned in (
            "droid-review-shards.yml",
            "FACTORY_API_KEY",
            "run_shard.sh",
            "plan_shards.py",
            "Install Droid CLI",
            "Setup BYOM",
        ):
            assert banned not in raw
