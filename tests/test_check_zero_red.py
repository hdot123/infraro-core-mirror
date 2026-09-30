"""Tests for the zero-red check-runs mechanism.

User mandate (2026-08-28): "写死不允许红色合并，一个都不允许"
These tests verify that the ci-ok gate correctly fails when any check-run
(including advisory jobs) has a non-success conclusion. INFRA-595: advisory
jobs carry no continue-on-error, so a failure is a red check-run that both
the needs.result gate and the GitHub API scan block.
"""

import subprocess
from pathlib import Path

import pytest
import yaml


class TestZeroRedScript:
    """Test the check_zero_red.sh script logic."""

    @pytest.fixture
    def script_path(self):
        """Path to the zero-red check script."""
        return Path(__file__).parent.parent / "scripts" / "check_zero_red.sh"

    def test_script_exists_and_executable(self, script_path):
        """Verify the script exists and is executable."""
        assert script_path.exists(), f"Script not found: {script_path}"
        assert script_path.stat().st_mode & 0o111, "Script is not executable"

    def test_script_usage_on_missing_args(self, script_path):
        """Script should print usage when called without arguments."""
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Usage:" in result.stderr or "Usage:" in result.stdout

    def test_script_logic_mock_success(self):
        """Test zero-red logic with all-success check-runs (mock)."""
        # Simulate the check-runs output format
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
type-bundle\tsuccess
advisory-bundle\tsuccess
advisory-bundle\tneutral
advisory-bundle\tskipped
ci-ok\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # All success/skipped/neutral should pass
        assert len(red_checks) == 0, "Should have no red checks when all are green"

    def test_script_logic_mock_failure_advisory_red(self):
        """Test zero-red logic with a red advisory check (mock)."""
        # Simulate a scenario where the advisory bundle fails
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
type-bundle\tsuccess
advisory-bundle\tfailure
ci-ok\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should detect the red advisory check
        assert len(red_checks) == 1
        assert "advisory-bundle" in red_checks[0]
        assert "failure" in red_checks[0]

    def test_script_logic_mock_multiple_red(self):
        """Test zero-red logic with multiple red checks (mock)."""
        # Simulate multiple failures
        mock_output = """pytest\tsuccess
lint-bundle\tfailure
advisory-bundle\tfailure
type-bundle\tsuccess"""

        # Parse the output the same way the script does
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                if conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should detect both red checks
        assert len(red_checks) == 2
        assert any("lint-bundle" in check for check in red_checks)
        assert any("advisory-bundle" in check for check in red_checks)

    def test_script_logic_mock_ci_ok_exclusion(self):
        """Test that ci-ok check is excluded to prevent circular self-reference (regression test)."""
        # Simulate a scenario where ci-ok has a failure but should be excluded
        mock_output = """pytest\tsuccess
lint-bundle\tsuccess
ci-ok\tfailure"""

        # Parse the output the same way the script does (excluding ci-ok)
        lines = mock_output.strip().split("\n")
        red_checks = []

        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                # This simulates the script's logic: exclude ci-ok and check for non-success conclusions
                if name != "ci-ok" and conclusion not in ["success", "skipped", "neutral"]:
                    red_checks.append(line)

        # Should have no red checks since ci-ok failure is excluded
        assert len(red_checks) == 0, "ci-ok failure should be excluded and not count as red"

    def test_script_logic_mock_name_deduplication(self):
        """Test that check-runs are deduplicated by name, taking the latest (regression test)."""
        # Simulate scenario with multiple runs of the same name, where the latest one is success
        mock_output = """pytest\tfailure
pytest\tsuccess
lint-bundle\tfailure
lint-bundle\tsuccess
type-check\tsuccess"""

        # Parse the output and simulate grouping by name, taking latest
        lines = mock_output.strip().split("\n")

        # Group by name and take the last occurrence for each name (simulating sort_by(.started_at) | last)
        check_dict = {}
        for line in lines:
            parts = line.split("\t")
            if len(parts) == 2:
                name, conclusion = parts
                # Overwrite with latest (last in list) - simulating the group_by(.name) | map(sort_by(.started_at) | last) pattern
                check_dict[name] = (name, conclusion)

        # Now check for non-success conclusions among unique names
        red_checks = []
        for name, (name_key, conclusion) in check_dict.items():
            if conclusion not in ["success", "skipped", "neutral"]:
                red_checks.append(f"{name_key}\t{conclusion}")

        # Should have no red checks since the latest of each name is success
        assert len(red_checks) == 0, (
            f"Latest conclusion of each name should be checked, got: {red_checks}"
        )

        # Verify that the dict contains the latest conclusion for each name
        assert check_dict["pytest"][1] == "success", (
            "Should take latest pytest conclusion (success)"
        )
        assert check_dict["lint-bundle"][1] == "success", (
            "Should take latest lint-bundle conclusion (success)"
        )
        assert check_dict["type-check"][1] == "success", (
            "Should take latest type-check conclusion (success)"
        )


class TestCIWorkflowZeroRed:
    """Test that the CI workflow includes zero-red enforcement."""

    def test_ci_ok_includes_advisory_in_needs(self):
        """Verify ci-ok job lists the advisory bundle in needs.

        2026-08-29 bundle 化：advisory 三 job（advisory-dependency-security-scan
        / advisory-deptry / advisory-telemetry-audit）合并为 advisory-bundle，
        零红语义由该 bundle 承接。
        """
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        assert "advisory-bundle" in content, "Should have advisory-bundle job (bundle 化后零红载体)"

    def test_ci_ok_checks_advisory_jobs(self):
        """Verify ci-ok job actually checks advisory bundle results (not just prints)."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Find the ci-ok job section
        ci_ok_start = content.find("  ci-ok:")
        assert ci_ok_start != -1, "ci-ok job not found"

        # Extract ci-ok section (up to next top-level job or end)
        ci_ok_section = content[ci_ok_start:]

        # Verify the advisory bundle is checked with FAILED=1 logic
        # INFRA-595: advisory must NOT set continue-on-error (which makes
        # .result always "success"); with it removed, .result is the real
        # conclusion and this gate is effective.
        assert "advisory-bundle.result" in ci_ok_section, (
            "Should check advisory-bundle result (bundle 化后零红判定载体)"
        )

        # Verify they set FAILED=1 on non-success
        assert "FAILED=1" in ci_ok_section, "Should set FAILED=1 on failure"

    def test_ci_ok_includes_zero_red_api_scan(self):
        """Verify ci-ok includes the zero-red aggregation step via GitHub API."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Check for the zero-red step
        assert "Zero-red aggregation" in content or "zero-red" in content.lower(), (
            "Should have a zero-red aggregation step"
        )
        # The zero-red logic is delegated to the standalone script
        assert "check_zero_red.sh" in content, "Should invoke the check_zero_red.sh script"


class TestZeroRedPolicy:
    """Test the zero-red policy documentation and intent."""

    def test_user_mandate_in_ci(self):
        """Verify the user mandate comment is present in CI."""
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Check for the user mandate reference
        assert "写死不允许红色合并" in content or "zero-red" in content.lower(), (
            "Should reference the user mandate or zero-red policy"
        )

    def test_advisory_jobs_no_continue_on_error(self):
        """INFRA-595: advisory must NOT have continue-on-error (zero-red).

        continue-on-error masks failures: needs.<job>.result reports the
        post-masking value (always "success"), making the ci-ok gate a no-op
        (proven in run 33129232081). Zero-red requires red check-runs.
        2026-08-29 bundle 化：advisory-bundle 为三 advisory 的零红载体。

        2026-09-18 解冻判据③：gate-tests 已转正 required check，五个门步不再保留
        continue-on-error（存量红暴露为 failure，阻断合并）。job 级 continue-on-error
        始终全面禁止（infra 失败保持红可见）。
        """
        ci_yml = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        content = ci_yml.read_text()

        # Advisory bundle must exist
        assert "advisory-bundle:" in content, "Should have advisory-bundle job"

        jobs = yaml.safe_load(content)["jobs"]
        gate_job = "gate-tests"
        gate_steps = 0
        for name, job in jobs.items():
            assert not job.get("continue-on-error"), (
                f"job {name} 不得设置 job 级 continue-on-error（零红铁律；"
                "job 级 c-o-e 的 check-run 仍记 failure 且掩蔽 infra 红）"
            )
            for step in job.get("steps") or []:
                if step.get("continue-on-error"):
                    assert name == gate_job, (
                        f"步级 continue-on-error 仅允许出现在 {gate_job} 的门步：{name}"
                    )
                    gate_steps += 1

        # 先红着上线裁定已转正 required：五个门步全部无步级 continue-on-error（转正 required check）
        assert gate_steps == 0, (
            f"gate-tests 转正后应有 0 个步级 continue-on-error（转正 required check）：存量红需暴露为 failure，found {gate_steps}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
