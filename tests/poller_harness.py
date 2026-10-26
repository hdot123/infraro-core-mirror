"""Shared check_droid_review poller test harness (INFRA-1303 dedup).

INFRA-1303: ``_run_poller`` had 99-100% AST similarity across 4 sites
(test_h1_alwayscheck_earlyexit: L313/L412/L507, three per-class copies of
``_run_poller`` + ``fake_bin``; test_check_droid_review: L368) — triggering
CODE_HYGIENE_DUPLICATE_BLOCK.

All variants are folded into this module:

- ``FAKE_CURL_TEMPLATE`` — the superset fake-curl shim. The h1 file's copy
  routes ``/actions/runs`` URLs to a ``runs`` behavior key (the early-exit
  run probe hits that endpoint); the older two-key copy in
  test_check_droid_review would misfile those URLs under ``pulls``. The
  superset keeps all existing behavior scripts working: keys that a test
  never exercises are simply never read.
- ``fake_curl_bin`` — pytest fixture: PATH shim with instant sleep (skips
  the 30s backoff) + the scripted fake curl.
- ``run_poller`` — invoke the poller script against the shim (fixed
  pull_request / hdot123/memory / deadbeefcafe / fake-token contract).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

FAKE_CURL_TEMPLATE = """#!/usr/bin/env python3
import json
import os
import sys

url = sys.argv[-1]
if "check-runs" in url:
    kind = "checkruns"
elif "/actions/runs" in url:
    kind = "runs"
else:
    kind = "pulls"
behaviors = json.loads(os.environ["FAKE_CURL_BEHAVIORS"])[kind]
state_dir = os.environ["FAKE_CURL_STATE_DIR"]
count_file = os.path.join(state_dir, kind + ".count")
try:
    count = int(open(count_file).read().strip())
except Exception:
    count = 0
count += 1
with open(count_file, "w") as fh:
    fh.write(str(count))
exit_code, body = behaviors[min(count - 1, len(behaviors) - 1)]
sys.stdout.write(body)
sys.exit(exit_code)
"""


@pytest.fixture
def fake_bin(tmp_path):
    """PATH shim: instant sleep (skip the 30s backoff) + scripted curl."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n")
    sleep.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL_TEMPLATE)
    curl.chmod(0o755)
    return bin_dir


def run_poller(poller_path: Path, fake_bin: Path, behaviors: dict) -> subprocess.CompletedProcess:
    """Run the poller against the fake-curl shim.

    Args:
        poller_path: One of the shipped check_droid_review.sh copies.
        fake_bin: The ``fake_bin`` fixture's shim directory.
        behaviors: Scripted ``(exit_code, body)`` sequences per fake-curl
            behavior key (``checkruns`` / ``runs`` / ``pulls``).

    Returns:
        The completed subprocess result (stdout/stderr captured).
    """
    state_dir = fake_bin.parent / "state"
    state_dir.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_CURL_BEHAVIORS"] = json.dumps(behaviors)
    env["FAKE_CURL_STATE_DIR"] = str(state_dir)
    return subprocess.run(
        [
            "bash",
            str(poller_path),
            "pull_request",
            "hdot123/memory",
            "deadbeefcafe",
            "fake-token",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
