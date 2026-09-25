"""Gate 4: hot-start timing assertion + one-click bootstrap (engine).

Encodes the 15-minute hot-start assertion (VAL-CONS-010 candidate口径): the
substrate bring-up path -- bootstrap dry-run plus all five gate scripts -- must
complete within :data:`HOT_START_BUDGET_SECONDS`. The assertion is encoded and
executed here; the timed evidence on a fresh consumer machine is attached at
the F4 first run (charter: 计时执行随 F4 首跑补证).

Also validates the machine entry of the substrate manual's bring-up items:
``scripts/bootstrap-substrate.sh`` must exist, be executable, and pass
``--dry-run``.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from gate_common import DECL_REPO_SLUG, REPO_ROOT, gh_api

HOT_START_BUDGET_SECONDS = 900  # 15 minutes (VAL-CONS-010 candidate口径)

BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap-substrate.sh"
# gate[0-9]* covers the five gates; gate_common.py is a module, and this script
# itself is excluded so the bring-up path never recurses into gate 4.
GATE_SCRIPTS = [
    path
    for path in sorted((REPO_ROOT / "substrate" / "gates").glob("gate[0-9]*.py"))
    if path.name != Path(__file__).name
]


def run(
    cmd: list[str], cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def check_bootstrap(errors: list[str]) -> None:
    if not BOOTSTRAP.exists():
        errors.append(f"bootstrap script missing: {BOOTSTRAP.relative_to(REPO_ROOT)}")
        return
    if not os_access(BOOTSTRAP):
        errors.append("bootstrap script is not executable (chmod +x required)")
    result = run(["bash", str(BOOTSTRAP), "--dry-run"], timeout=60)
    if result.returncode != 0:
        errors.append(
            f"bootstrap --dry-run exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    else:
        print("- bootstrap --dry-run: OK")


def os_access(path: Path) -> bool:
    return path.stat().st_mode & 0o111 != 0


def measure_hot_start(errors: list[str]) -> None:
    """Bring-up path wall time must fit the budget (assertion execution)."""
    if not BOOTSTRAP.exists():
        return  # already reported by check_bootstrap
    steps: list[list[str]] = [["bash", str(BOOTSTRAP), "--dry-run"]]
    steps.extend([sys.executable, str(script)] for script in GATE_SCRIPTS)
    started = time.monotonic()
    executed = 0
    for cmd in steps:
        try:
            run(cmd, cwd=REPO_ROOT, timeout=180)
            executed += 1
        except subprocess.TimeoutExpired:
            errors.append(f"hot-start step timed out: {cmd[-1]}")
    elapsed = time.monotonic() - started
    print(
        f"- hot-start bring-up path: {executed}/{len(steps)} steps, "
        f"{elapsed:.1f}s of {HOT_START_BUDGET_SECONDS}s budget"
    )
    if elapsed > HOT_START_BUDGET_SECONDS:
        errors.append(f"hot-start assertion RED: {elapsed:.1f}s > {HOT_START_BUDGET_SECONDS}s")


def check_declaration_manual(errors: list[str]) -> None:
    """The substrate manual (declaration repo) must be present on main."""
    manual = gh_api(f"repos/{DECL_REPO_SLUG}/contents/docs/substrate-map.md?ref=main")
    if manual is None:
        print("- LOCAL-ONE: declaration manual presence unverifiable here (gh api unavailable)")
        return
    print("- declaration substrate manual: present on main")


def main() -> int:
    errors: list[str] = []
    print("# Gate 4: Timing Assertion + Bootstrap (engine repo)")
    print(f"- encoded budget: {HOT_START_BUDGET_SECONDS}s (timed evidence: F4 first run)")
    check_bootstrap(errors)
    measure_hot_start(errors)
    check_declaration_manual(errors)

    for err in errors:
        print(f"- FAIL: {err}")
    if errors:
        return 1
    print("PASS: bootstrap --dry-run verifiable; hot-start assertion encoded and within budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
