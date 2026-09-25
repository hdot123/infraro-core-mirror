"""Gate 0: coverage completeness invariant (engine repo).

Every git-tracked top-level entry of the repository must fall inside at least
one checker's scan domain. Entries outside every domain are "unowned" and must
be registered in the stock registry (substrate/gate0-exemptions.md, Gate 0
section) with an owner and an owning feature -- an unregistered unowned entry
fails this gate.

Scan-domain map (engine repo, charter round-3 item A3: substrate/ is owned by
the gate suite itself):

======================  ====================================================
entry                   scan domain
======================  ====================================================
.github/                actionlint + SHA-pinning/ci-structure/guard contracts
src/                    ruff + mypy --strict + pytest
tests/                  pytest
scripts/                shellcheck + mypy --strict
actions/                composite-action contracts + SHA pinning
docs/                   doc classification guard + documentation contract
substrate/              substrate gate suite (this suite's own domain)
.evolution/             evolution governance workflows (paths filter) + evolution tests
webhook-scripts/        REGISTERED exemption zone (pending boundary ruling)
cf/                     REGISTERED exemption zone (pending boundary ruling)
pyproject.toml          pytest/deptry config + version sync
uv.lock                 setup-venv pinned install surface
release-please-*.json   release/version sync contracts
runner-tools.toml       runner toolchain contract
README.md/CHANGELOG.md  documentation contract + repo_health_check
test_shell_guards.sh    shellcheck
LICENSE                 REGISTERED exemption (static legal text)
======================  ====================================================
"""

from __future__ import annotations

from gate_common import REGISTRY_PATH, REPO_ROOT, registry_entries, tracked_files

# Entries fully inside a checker's scan domain (see module docstring).
COVERED_ENTRIES = frozenset(
    {
        ".github",
        ".gitignore",
        "src",
        "tests",
        "scripts",
        "actions",
        "docs",
        "substrate",
        ".evolution",
        "pyproject.toml",
        "uv.lock",
        "release-please-config.json",
        ".release-please-manifest.json",
        "runner-tools.toml",
        "README.md",
        "CHANGELOG.md",
        "test_shell_guards.sh",
    }
)


def top_level_entries() -> set[str]:
    """All git-tracked top-level entries (dirs keep a trailing slash label)."""
    entries: set[str] = set()
    for path in tracked_files(REPO_ROOT):
        rel = path.relative_to(REPO_ROOT)
        first = rel.parts[0]
        entries.add(f"{first}/" if len(rel.parts) > 1 else first)
    return entries


def main() -> int:
    if not REGISTRY_PATH.exists():
        print(f"FAIL: stock registry missing: {REGISTRY_PATH}")
        return 1

    entries = top_level_entries()
    unowned = sorted(e for e in entries if e.rstrip("/") not in COVERED_ENTRIES)
    registered = set(registry_entries("Gate 0"))

    def is_registered(entry: str) -> bool:
        return entry in registered or entry.rstrip("/") in registered

    unregistered = [entry for entry in unowned if not is_registered(entry)]

    print("# Gate 0: Coverage Completeness (engine repo)")
    print(f"- tracked top-level entries: {len(entries)}")
    print(f"- inside a checker scan domain: {len(entries & COVERED_ENTRIES)}")
    print(f"- unowned: {len(unowned)} (registered: {len(unowned) - len(unregistered)})")
    if unowned:
        print("- unowned entries:")
        for entry in unowned:
            marker = "registered" if is_registered(entry) else "UNREGISTERED"
            print(f"  - {entry} [{marker}]")
    print(f"- registry: {REGISTRY_PATH}")

    if unregistered:
        print("FAIL: unowned entries missing from the Gate 0 registry (owner + feature):")
        for entry in unregistered:
            print(f"  - {entry}")
        return 1
    print("PASS: every top-level entry is covered or registered (exemption = registration + owner)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
