"""droid-review BYOM public endpoint routing test（F2 round 7 fix）。

Regression protection: BYOM LLM call baseUrl must use public endpoint
(https://ai.lumivane.dpdns.org/v1 via CF AiGateway), with real API key
injection to enable hosted runner access.

Background: Original tailnet-only endpoint (node1.tail5e888.ts.net) is not
accessible from hosted runners (ubuntu-latest), causing droid exec timeout
with reason=model_provider_unreachable. This change enables public endpoint
access with proper API key injection via environment variable to avoid
logging secrets.
"""

import json

import pytest
import yaml

pytestmark = pytest.mark.integration

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# BYOM routing contract covers two carriers: self repo droid-review.yml + reusable workflow
# droid-review-shards.yml (since M4, memory-core and other thin callers' sharding pipeline uses latter)
WORKFLOW_PATHS = [
    REPO_ROOT / ".github/workflows/droid-review.yml",
    REPO_ROOT / ".github/workflows/droid-review-shards.yml",
]

# Backward compatibility alias (old reference point)
WORKFLOW_PATH = WORKFLOW_PATHS[0]

BYOM_STEP_NAME = "Write BYOM settings file"
HEREDOC_DELIMITER = "SETTINGS_EOF"


def _get_byom_step(workflow_path: Path) -> dict:
    """Locate the step that writes BYOM settings in review-shard job."""
    data = yaml.safe_load(workflow_path.read_text())
    shard_job = data["jobs"]["review-shard"]
    for step in shard_job.get("steps", []):
        if step.get("name") == BYOM_STEP_NAME:
            return step
    pytest.fail(f"{workflow_path.name}: review-shard job must have a {BYOM_STEP_NAME!r} step")


def _get_step_comment_block(workflow_path: Path, step_name: str) -> str:
    """Extract the step's YAML comment block (# prefixed lines).

    yaml.safe_load discards comments; prerequisite (/etc/hosts entries) is documented in
    the comment block between name: and run: lines, read the raw content by line extraction.
    """
    raw = workflow_path.read_text().splitlines()
    step_lines = [i for i, line in enumerate(raw) if f"name: {step_name}" in line]
    assert step_lines, f"{workflow_path.name}: step {step_name!r} not found in workflow source"

    # Find the start of the comment section after the step name
    start_idx = step_lines[0] + 1
    comments: list[str] = []

    for i in range(start_idx, len(raw)):
        line = raw[i]
        if line.strip().startswith("#"):
            comments.append(line)
        elif line.strip() == "":
            # Skip empty lines
            continue
        elif line.lstrip().startswith("run:"):
            # Reached the run section, stop here
            break
        else:
            # Reached another YAML property or new step, stop here
            break

    return "\n".join(comments)


def _extract_settings_block(workflow_path: Path) -> str:
    """Extract the embedded settings JSON text from the run: block."""
    run_script = _get_byom_step(workflow_path)["run"]
    lines = run_script.splitlines()
    start = end = None
    for idx, line in enumerate(lines):
        if f"<< '{HEREDOC_DELIMITER}'" in line or f"<< '{HEREDOC_DELIMITER}'" in line:
            start = idx + 1
        elif line.strip() == HEREDOC_DELIMITER and start is not None:
            end = idx
            break
    assert start is not None, f"heredoc << '{HEREDOC_DELIMITER}' not found in BYOM step"
    assert end is not None, f"heredoc terminator {HEREDOC_DELIMITER} not found"
    return "\n".join(lines[start:end])


def _load_settings(workflow_path: Path) -> dict:
    """Parse embedded settings JSON (indentation is harmless to json.loads, full validation)."""
    return json.loads(_extract_settings_block(workflow_path))


@pytest.fixture(params=WORKFLOW_PATHS, ids=[p.name for p in WORKFLOW_PATHS])
def wf_path(request) -> Path:
    return request.param


class TestByomPublicRouting:
    """BYOM baseUrl must use public endpoint for hosted runner access, with API key injection.

    Covers two carriers: self repo droid-review.yml and reusable droid-review-shards.yml.
    """

    def test_baseurl_points_to_public_kong(self, wf_path):
        """baseUrl must be https://ai.lumivane.dpdns.org/custom-node01/v1 (public CF AiGateway)."""
        settings = _load_settings(wf_path)
        model = settings["customModels"][0]
        assert model["baseUrl"] == "https://ai.lumivane.dpdns.org/custom-node01/v1", (
            "BYOM baseUrl must use public endpoint for hosted runner access: "
            "tailnet-only endpoint node1.tail5e888.ts.net is not accessible from ubuntu-latest"
        )

    def test_public_endpoint_present_in_active_config(self, wf_path):
        """Active configuration (heredoc JSON block) must include public endpoint."""
        block = _extract_settings_block(wf_path)
        assert "ai.lumivane.dpdns.org" in block, (
            "Active BYOM configuration must reference public endpoint ai.lumivane.dpdns.org"
        )
        assert "custom-node01" in block, (
            "Active BYOM configuration must reference correct path ai.lumivane.dpdns.org/custom-node01/v1"
        )

    def test_settings_block_is_valid_json(self, wf_path):
        """Embedded settings block must be valid JSON (workflow only validates after write, intercept here)."""
        settings = _load_settings(wf_path)
        assert isinstance(settings, dict)
        assert len(settings["customModels"]) == 1

    def test_api_key_is_empty_placeholder_in_heredoc(self, wf_path):
        """API key should be empty in heredoc, with real key injected via python script."""
        settings = _load_settings(wf_path)
        model = settings["customModels"][0]
        # The heredoc contains an empty API key, which is later injected via python script
        # to avoid logging secrets in the workflow run logs
        assert model["apiKey"] == "", (
            "API key in heredoc should be empty placeholder, real key injected via python script"
        )

    def test_python_api_key_injection_present(self, wf_path):
        """Verify that the python API key injection script is present in the run block."""
        run_script = _get_byom_step(wf_path)["run"]
        assert "python3 -c" in run_script and "NVIDIA_KONG_PROXY_KEY" in run_script, (
            "BYOM settings must include python script to inject API key from environment variable"
        )

    def test_hosted_runner_routing_comment_present(self, wf_path):
        """Verify that the comment explains hosted runner routing requirement."""
        run_script = _get_byom_step(wf_path)["run"]
        # /etc/hosts prerequisite documentation in step's name: and run: comment blocks,
        # or in run: content — combine both for checking.
        comments = _get_step_comment_block(wf_path, BYOM_STEP_NAME)
        combined = comments + "\n" + run_script
        # Check for both English and Chinese terms (public endpoint = 公网直达 in Chinese)
        has_hosted_runner = "hosted runner" in combined
        has_public = "public" in combined or "公网" in combined
        assert has_hosted_runner and has_public, (
            "BYOM step must document hosted runner public endpoint requirement"
        )
