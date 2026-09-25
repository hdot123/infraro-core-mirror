#!/usr/bin/env python3
"""Mechanical snapshot transform for the public mirror (infraro-core-mirror).

This script is part of the mirror's sync pipeline. It is the *only* place that
knows how an upstream release snapshot differs from the upstream tree, so the
published mirror is reproducible: run it again on the same snapshot and the
result is byte-identical (every rule is idempotent).

Two deterministic transforms are applied:

1. Desensitization (public-exposure discipline).
   The mirror is a *new* public repository, so it must not carry the
   sensitive-metadata classes that the engine's own public-exposure gate scans
   for:

     * private-network IPv4 literals  ->  ``[REDACTED-IP]``
     * internal topology hostnames    ->  ``[REDACTED-HOST]``
     * local home-directory paths     ->  ``/Users/[USER]/`` / ``/home/[USER]/``

   GitHub-hosted runner homes (``/Users/runner``, ``/home/runner``) are
   preserved: they are the platform's own paths, not a local host path.

2. Self-reference rewrite (the mirror must be a working drop-in anchor).
   Inside the mirror's own executable surface (``.github/workflows/**`` and
   ``actions/**``) every reference to the engine repository is repointed at the
   mirror itself, and commit-SHA pins are re-pinned to the release tag being
   mirrored:

     ``hdot123/infraro-core/<path>@<40-hex sha>``
         ->  ``hdot123/infraro-core-mirror/<path>@<tag>``

   Without this, a consumer that anchors ``uses:`` at the mirror would still
   clone/install the private source repository (whose commit SHAs do not exist
   in the mirror) and fail. Documentation, changelog and script surfaces are
   deliberately left untouched so they keep pointing at the true source.

Usage:
    python3 scripts/mirror_snapshot.py <snapshot-root> --tag <tag>
    python3 scripts/mirror_snapshot.py --check <snapshot-root> --tag <tag>

Exit codes:
    0 - transform applied (or, with ``--check``, the snapshot is clean)
    1 - findings remain / verification failed
    2 - usage or I/O error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = frozenset({".git"})
BINARY_PROBE_BYTES = 8192

# Directories whose contents are the mirror's own executable surface.
REWRITE_ROOTS = (".github/workflows", "actions")

IP_PLACEHOLDER = "[REDACTED-IP]"
HOST_PLACEHOLDER = "[REDACTED-HOST]"

PRIVATE_IP_RE = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r")\b"
)
TOPOLOGY_HOST_RE = re.compile(r"\b(?:ce|node|pve-runner)-\d{1,3}\b")
LOCAL_HOME_RE = re.compile(r"/(?P<root>Users|home)/(?P<user>[A-Za-z0-9_.-]+)(?=/|$)")

ENGINE_SLUG = "hdot123/infraro-core"
MIRROR_SLUG = "hdot123/infraro-core-mirror"
ENGINE_SLUG_RE = re.compile(re.escape(ENGINE_SLUG) + r"(?!-mirror)")
MIRROR_SHA_PIN_RE = re.compile(
    re.escape(MIRROR_SLUG) + r"(?P<mid>(?:\.git)?(?:/[^\s@]+)?)@[0-9a-f]{40}\b"
)

# (kind, regex) pairs used both to redact and to verify.
REDACTION_RULES = (
    ("private-ip", PRIVATE_IP_RE),
    ("topology-host", TOPOLOGY_HOST_RE),
)


def _redact_local_home(text: str):
    """Redact local home paths, preserving GitHub-hosted runner homes.

    Returns (new_text, real_replacement_count): the count only covers matches
    that were actually rewritten, so the transform stays idempotent.
    """
    counter = {"n": 0}

    def _repl(match: re.Match) -> str:
        if match.group("user") == "runner":
            return match.group(0)
        counter["n"] += 1
        return "/{root}/[USER]".format(root=match.group("root"))

    return LOCAL_HOME_RE.sub(_repl, text), counter["n"]


def _is_binary(raw: bytes) -> bool:
    return b"\0" in raw[:BINARY_PROBE_BYTES]


def _in_rewrite_surface(rel: Path) -> bool:
    parts = rel.parts
    for root in REWRITE_ROOTS:
        root_parts = Path(root).parts
        if len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts:
            return True
    return False


def transform_text(text: str, rel: Path, tag: str):
    """Return (transformed_text, change_kinds)."""
    kinds = []

    for kind, pattern in REDACTION_RULES:
        text, hits = pattern.subn(
            IP_PLACEHOLDER if kind == "private-ip" else HOST_PLACEHOLDER, text
        )
        if hits:
            kinds.append("{kind}:{hits}".format(kind=kind, hits=hits))

    text, hits = _redact_local_home(text)
    if hits:
        kinds.append("local-home:{hits}".format(hits=hits))

    if _in_rewrite_surface(rel):
        text, hits = ENGINE_SLUG_RE.subn(MIRROR_SLUG, text)
        if hits:
            kinds.append("self-slug:{hits}".format(hits=hits))
        text, hits = MIRROR_SHA_PIN_RE.subn(
            lambda m: MIRROR_SLUG + m.group("mid") + "@" + tag, text
        )
        if hits:
            kinds.append("sha-pin:{hits}".format(hits=hits))

    return text, kinds


def find_findings(text: str, rel: Path):
    """Return the residual sensitive findings in a (transformed) text."""
    findings = []
    for kind, pattern in REDACTION_RULES:
        findings.extend(
            "{kind}:{value}".format(kind=kind, value=m.group(0))
            for m in pattern.finditer(text)
        )
    for match in LOCAL_HOME_RE.finditer(text):
        if match.group("user") != "runner":
            findings.append("local-home:{value}".format(value=match.group(0)))
    if _in_rewrite_surface(rel) and ENGINE_SLUG_RE.search(text):
        findings.append("engine-self-reference")
    return findings


def iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        raw = path.read_bytes()
        if _is_binary(raw):
            continue
        yield path, rel, raw


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", help="snapshot root directory")
    parser.add_argument("--tag", required=True, help="release tag being mirrored")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only; do not modify the snapshot",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print("ERROR: not a directory: {root}".format(root=root), file=sys.stderr)
        return 2

    changed = 0
    counters = {}
    residual = []

    for path, rel, raw in iter_text_files(root):
        text = raw.decode("utf-8", errors="surrogateescape")

        if args.check:
            residual.extend(
                "{rel}: {finding}".format(rel=rel, finding=finding)
                for finding in find_findings(text, rel)
            )
            continue

        new_text, kinds = transform_text(text, rel, args.tag)
        if kinds:
            for kind in kinds:
                name, _, count = kind.partition(":")
                counters[name] = counters.get(name, 0) + int(count)
            if new_text != text:
                changed += 1
                path.write_bytes(new_text.encode("utf-8", errors="surrogateescape"))

    if args.check:
        if residual:
            print("FAIL: {n} residual finding(s)".format(n=len(residual)))
            for line in residual[:50]:
                print("  " + line)
            return 1
        print("OK: no residual sensitive metadata in snapshot")
        return 0

    print("transformed files: {n}".format(n=changed))
    for name in sorted(counters):
        print("  {name}: {count} replacement(s)".format(name=name, count=counters[name]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
