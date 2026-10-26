"""Tests for scripts/check_cheap_gates_green.sh (cheap gates before Droid)."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_cheap_gates_green.sh"


@pytest.mark.business_policy
def test_script_requires_github_output():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "GITHUB_OUTPUT" in text
    assert "ci-ok" in text and "qa-ok" in text


@pytest.mark.business_policy
def test_script_emits_ready_output_keys():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "ready=true" in text or "ready=false" in text
    assert "head_sha=" in text
    assert "pr_number=" in text


@pytest.mark.business_policy
def test_script_supports_bounded_poll_for_pull_request_bootstrap():
    """Caller may poll while gates pending; attempts/seconds validated."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "CHEAP_GATES_POLL_ATTEMPTS" in text
    assert "CHEAP_GATES_POLL_SECONDS" in text
    assert r"^[1-9][0-9]*$" in text or "^[1-9][0-9]*$" in text
    assert "transient gh api error" in text
    assert "return 2" in text
