"""Gate 3: public exposure scan + version four-source consistency (engine).

Sensitive-metadata scan over the git-tracked tree: production/LAN IPs, local
host paths, personal emails, runner-topology hostnames, 1Password references.
Findings under path prefixes registered in the stock registry (Gate 3 section)
are reported as owned existing stock; NEW findings fail the gate.

Self-reference note (charter round-3 item A2): the scanner's own regex
literals live in substrate/gates/ and that directory is excluded from the
pattern scan (the exclusion itself is registered in the registry).

Version four-source consistency (engine repo): pyproject.toml ==
.release-please-manifest.json == src/infra_core/__init__.__version__ ==
latest remote tag (the historical 0.18.3 ``__init__`` drift class).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from gate_common import (
    ENGINE_REPO_URL,
    REPO_ROOT,
    remote_tags,
    tracked_files,
)

SCAN_EXCLUDED_DIRS = frozenset({"substrate/gates", ".git/", ".venv"})

IP_RE = re.compile(r"\b(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\b")
LOCAL_PATH_RE = re.compile(r"(?P<path>/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RUNNER_RE = re.compile(r"\b(?P<host>ce-\d+|pve-runner-\d+|node-\d+)\b")
ONEPASS_RE = re.compile(r"(?P<op>op://[A-Za-z0-9/_.-]+|GitHub-PAT-[A-Za-z0-9-]+)")

IP_EXCLUDES = {"0.0.0.0", "127.0.0.1", "255.255.255.255"}
# Narrow, documented accepts (GitHub-standard values, not local exposure):
# - noreply/github.com/example.com mail domains (SCM address + fixtures);
# - /Users/runner and /home/runner are GitHub-hosted runner homes, not host paths.
EMAIL_ACCEPT_SUFFIXES = ("@users.noreply.github.com", "@github.com", "@example.com")
LOCAL_PATH_ACCEPT_SUFFIXES = ("/runner",)


def scan_patterns(path: Path) -> list[tuple[str, int, str]]:
    """Sensitive-metadata findings in one tracked text file."""
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    if any(rel.startswith(prefix) or f"/{prefix}/" in rel for prefix in SCAN_EXCLUDED_DIRS):
        return []
    if rel == "substrate/gate0-exemptions.md":
        # The registry quotes stock identifiers verbatim by design; its rows
        # ARE the registrations, so scanning it would double-count itself.
        return []
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\0" in raw[:8192]:
        return []
    findings: list[tuple[str, int, str]] = []
    for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        for match in IP_RE.finditer(line):
            ip = match.group("ip")
            if ip not in IP_EXCLUDES and ip.count(".") == 3:
                findings.append((rel, line_no, f"ip:{ip}"))
        for match in LOCAL_PATH_RE.finditer(line):
            value = match.group("path")
            if value.endswith(LOCAL_PATH_ACCEPT_SUFFIXES):
                continue
            findings.append((rel, line_no, f"local-path:{value}"))
        for match in EMAIL_RE.finditer(line):
            if match.group(0).endswith(EMAIL_ACCEPT_SUFFIXES):
                continue
            findings.append((rel, line_no, f"email:{match.group(0)}"))
        for match in RUNNER_RE.finditer(line):
            findings.append((rel, line_no, f"runner-topology:{match.group('host')}"))
        for match in ONEPASS_RE.finditer(line):
            findings.append((rel, line_no, f"1password:{match.group('op')}"))
    return findings


def registry_prefixes() -> list[str]:
    """Registered path prefixes from the Gate 3 section of the registry."""
    registry = REPO_ROOT / "substrate" / "gate0-exemptions.md"
    try:
        lines = registry.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    prefixes: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            in_section = line[3:].lstrip().startswith("Gate 3")
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith("`"):
            prefixes.append(cells[0].strip("`"))
    return prefixes


def read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def pyproject_version() -> str | None:
    text = read_text_or_none(REPO_ROOT / "pyproject.toml")
    if text is None:
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def manifest_version() -> str | None:
    try:
        data = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(".", data.get("infra-core"))
    return str(value) if value else None


def init_version() -> str | None:
    text = read_text_or_none(REPO_ROOT / "src" / "infra_core" / "__init__.py")
    if text is None:
        return None
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def check_version_four_source(errors: list[str]) -> None:
    sources = {
        "pyproject.toml": pyproject_version(),
        ".release-please-manifest.json": manifest_version(),
        "src/infra_core/__init__.py": init_version(),
    }
    tags = remote_tags(ENGINE_REPO_URL)
    semver_tags = sorted(t for t in tags if re.fullmatch(r"v\d+\.\d+\.\d+", t))
    sources["latest remote tag"] = semver_tags[-1].lstrip("v") if semver_tags else None
    values = {name: value for name, value in sources.items() if value is not None}
    print(f"- version four-source: {sources}")
    if len(values) < 4:
        missing = [name for name, value in sources.items() if value is None]
        errors.append(f"version sources unreadable: {missing}")
    elif len(set(values.values())) != 1:
        errors.append(f"VERSION DRIFT across four sources: {sources}")


def main() -> int:
    registry = registry_prefixes()
    findings: list[tuple[str, int, str]] = []
    for path in tracked_files(REPO_ROOT):
        findings.extend(scan_patterns(path))

    owned: list[tuple[str, int, str]] = []
    new: list[tuple[str, int, str]] = []
    for rel, line_no, detail in findings:
        if any(rel.startswith(prefix.rstrip("/")) if prefix else False for prefix in registry):
            owned.append((rel, line_no, detail))
        else:
            new.append((rel, line_no, detail))

    errors: list[str] = []
    check_version_four_source(errors)

    print("# Gate 3: Public Exposure Scan (engine repo)")
    print(f"- findings: {len(findings)} total (registered stock: {len(owned)}, NEW: {len(new)})")
    for rel, line_no, detail in owned:
        print(f"- REGISTERED STOCK {rel}:{line_no} {detail}")
    for rel, line_no, detail in new:
        print(f"- NEW EXPOSURE {rel}:{line_no} {detail}")
    for err in errors:
        print(f"- VERSION ERROR: {err}")

    if new or errors:
        print("FAIL: new exposures / version drift must be fixed or registered (owner + feature)")
        return 1
    print("PASS: no new exposures; version four-source consistent; existing stock registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
