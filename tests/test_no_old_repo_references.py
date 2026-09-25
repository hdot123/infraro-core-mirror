"""
Test that ensures no references to the old repository name exist in the codebase.
This is part of R-NEW1 three-line defense system (grep zero + no-old-repo-references contract test + provenance assertion).
"""

import subprocess
from pathlib import Path


def test_no_old_repo_literals():
    """Assert that hdot123-org/infra-core appears zero times in tracked files."""
    repo_root = Path(__file__).parent.parent

    # Get all tracked files and search for the old repo name
    result = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True)

    tracked_files = result.stdout.strip().split("\n")

    found_matches = []
    for file_path in tracked_files:
        if (
            file_path and file_path != "tests/test_no_old_repo_references.py"
        ):  # Exclude self to avoid false positives
            abs_file_path = repo_root / file_path
            if abs_file_path.exists() and abs_file_path.is_file():
                try:
                    content = abs_file_path.read_text(encoding="utf-8", errors="ignore")
                    if "hdot123-org/infra-core" in content:
                        found_matches.append(f"{file_path}: found 'hdot123-org/infra-core'")
                except Exception:
                    # Skip files that can't be read
                    continue

    assert not found_matches, (
        "Found references to old repository 'hdot123-org/infra-core' in the codebase:\n"
        + "\n".join(found_matches)
    )


def test_no_org_infra_core_references():
    """Additional check to ensure no old org references exist."""
    repo_root = Path(__file__).parent.parent

    # Get all tracked files and search for the old org pattern
    result = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True)

    tracked_files = result.stdout.strip().split("\n")

    found_matches = []
    for file_path in tracked_files:
        if (
            file_path and file_path != "tests/test_no_old_repo_references.py"
        ):  # Exclude self to avoid false positives
            abs_file_path = repo_root / file_path
            if abs_file_path.exists() and abs_file_path.is_file():
                try:
                    content = abs_file_path.read_text(encoding="utf-8", errors="ignore")
                    # Specifically look for the old repo pattern
                    if "hdot123-org/infra-core" in content:
                        found_matches.append(f"{file_path}: found 'hdot123-org/infra-core'")
                except Exception:
                    # Skip files that can't be read
                    continue

    assert not found_matches, (
        "Found specific references to old repo 'hdot123-org/infra-core':\n"
        + "\n".join(found_matches)
    )
