"""Shared head-src git repo factory for run_shard.sh sandboxes (INFRA-1286 dedup).

INFRA-1286: the nested ``git`` helper had 100% AST similarity across
test_run_shard_error_classification and test_droid_review_exit137_resilience
(7 lines / 77 tokens each), triggering CODE_HYGIENE_DUPLICATE_BLOCK. Both
fixtures also built the same two-commit repo, so that setup lives here too.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_isolated_git(repo: Path, git_cfg: Path, *args: str) -> str:
    """Run git in ``repo`` with global/system config pinned to ``git_cfg``."""
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = str(git_cfg)
    env["GIT_CONFIG_SYSTEM"] = str(git_cfg)
    r = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True
    )
    return r.stdout.strip()


def init_two_commit_repo(head: Path, git_cfg: Path) -> tuple[str, str]:
    """Init ``head`` as a git repo with two commits on ``a.py``; return (base_sha, head_sha)."""
    ident = ("-c", "user.email=t@example.com", "-c", "user.name=t")
    run_isolated_git(head, git_cfg, "init", "-q")
    (head / "a.py").write_text("def f(x):\n    return x / 0\n", encoding="utf-8")
    run_isolated_git(head, git_cfg, "add", "a.py")
    run_isolated_git(head, git_cfg, *ident, "commit", "-qm", "base")
    base_sha = run_isolated_git(head, git_cfg, "rev-parse", "HEAD")
    (head / "a.py").write_text("def f(x):\n    return x // 0\n", encoding="utf-8")
    run_isolated_git(head, git_cfg, *ident, "commit", "-am", "head")
    head_sha = run_isolated_git(head, git_cfg, "rev-parse", "HEAD")
    return base_sha, head_sha
