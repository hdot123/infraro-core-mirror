"""Gate 2: cross-repository drift inspection (cron + CI surface).

Checks (charter: substrate 门2):

* GitHub-visible surface (runs in CI with GITHUB_TOKEN): frozen old-world
  repos must have zero in-flight PRs and zero unmerged branches; both new-world
  repos must expose the substrate faces (gate2 workflow on engine main, gate
  CI job + substrate manual on declaration main).
* Local faces (only when the local file exists; in CI these degrade to
  LOCAL-ONE registry notes instead of failing): ``~/.factory/config/
  repositories.yml`` vs reality, Worker ``ERROR_REPO_MAP`` vs
  repositories.yml.

Findings on the GitHub-visible surface fail the gate (advisory job absorbs
them); registered stock is reported with owner + owning feature. Local-only
findings are reported as LOCAL-ONE items assigned to the bookkeeping features.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from gate_common import (
    DECL_REPO_SLUG,
    ENGINE_REPO_SLUG,
    FROZEN_REPO_SLUGS,
    REPO_ROOT,
    decl_dir,
    gh_api,
    registry_entries,
)

# 归属（substrate milestone 队列）：存量红项逐条映射到后续清理 feature。
OWNER_FROZEN = "legacy-repo-disposition"
OWNER_REGISTRY = "substrate-inventory-bookkeeping"
OWNER_WORKER = "substrate-foundations-linear-webhook"


def check_frozen_repos() -> tuple[list[tuple[str, str]], list[str]]:
    """In-flight PRs / unmerged branches on the frozen old-world repos."""
    findings: list[tuple[str, str]] = []
    unverifiable: list[str] = []
    for slug in FROZEN_REPO_SLUGS:
        pulls = gh_api(f"repos/{slug}/pulls?state=open&per_page=100")
        if pulls is None:
            unverifiable.append(f"{slug}: not visible with this token (private or missing)")
            continue
        for pr in pulls:
            findings.append((slug, f"in-flight OPEN PR #{pr.get('number')}: {pr.get('title')}"))
        branches = gh_api(f"repos/{slug}/branches?per_page=100")
        if branches is None:
            continue
        for branch in branches:
            name = branch.get("name", "")
            if name and name not in {"main", "master", "develop"}:
                findings.append((slug, f"residual branch: {name}"))
    return findings, unverifiable


def check_substrate_faces() -> list[tuple[str, str]]:
    """Substrate faces present (own checkout or main; cross-repo via main).

    The own-repo face accepts the current checkout, so a PR introducing the
    face is not flagged before merge; the cross-repo face is only visible on
    main and heals when the sibling PR merges (transient advisory red is the
    honest pre-merge state).
    """
    import base64

    findings: list[tuple[str, str]] = []
    gate2_local = REPO_ROOT / ".github" / "workflows" / "gate2-cross-repo-inspection.yml"
    gate2_main = (
        gh_api(
            f"repos/{ENGINE_REPO_SLUG}/contents/.github/workflows/gate2-cross-repo-inspection.yml?ref=main"
        )
        is not None
    )
    if not gate2_local.exists() and not gate2_main:
        findings.append((ENGINE_REPO_SLUG, "gate2 inspection workflow missing (checkout + main)"))

    decl_face = False
    decl_local = decl_dir()
    if decl_local is not None and (decl_local / ".github" / "workflows" / "ci.yml").exists():
        text = (decl_local / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        decl_face = "substrate-gate-tests" in text
    if not decl_face:
        decl_ci = gh_api(f"repos/{DECL_REPO_SLUG}/contents/.github/workflows/ci.yml?ref=main")
        if isinstance(decl_ci, dict):
            content = base64.b64decode(decl_ci.get("content", "")).decode("utf-8", "replace")
            decl_face = "substrate-gate-tests" in content
        elif decl_ci is None:
            findings.append((DECL_REPO_SLUG, "ci workflow unreadable (missing on main)"))
    if not decl_face:
        findings.append((DECL_REPO_SLUG, "gate-tests advisory job missing on declaration main"))
    return findings


def load_repositories_yml() -> dict[str, Any] | None:
    """Local repositories.yml (user runtime config; read-only, LOCAL face)."""
    config_path = Path.home() / ".factory" / "config" / "repositories.yml"
    if not config_path.exists():
        return None
    try:
        with open(config_path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception:  # noqa: BLE001
        return None


def registered_repo_slugs(config: dict[str, Any]) -> set[str]:
    """githubRepo slugs registered anywhere in repositories.yml.

    The user runtime config nests repos under ``teams[*].repositories[*]``
    (fields repoKey/repoPath/githubRepo); walk every mapping so a structural
    refactor upstream cannot silently blind this check.
    """
    slugs: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            github_repo = node.get("githubRepo")
            if github_repo:
                slugs.add(str(github_repo))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    return slugs


def check_local_registry(config: dict[str, Any] | None) -> tuple[list[tuple[str, str]], list[str]]:
    """LOCAL face: repositories.yml vs the new-world repos."""
    findings: list[tuple[str, str]] = []
    local_one: list[str] = []
    if config is None:
        local_one.append("repositories.yml not present in this environment (LOCAL-ONE)")
        return findings, local_one
    slugs = registered_repo_slugs(config)
    for slug in (ENGINE_REPO_SLUG, DECL_REPO_SLUG):
        if slug not in slugs:
            findings.append((slug, "missing from repositories.yml (LOCAL registry face)"))
    return findings, local_one


def check_worker_routing(config: dict[str, Any] | None) -> tuple[list[tuple[str, str]], list[str]]:
    """LOCAL face: Worker ERROR_REPO_MAP vs repositories.yml slugs.

    The CF Worker source lives in hdot123/webhook (not readable with this
    repo's token); when a local webhook checkout exists we parse
    ERROR_REPO_MAP from its worker source, otherwise this stays a LOCAL-ONE
    registry item.
    """
    findings: list[tuple[str, str]] = []
    local_one: list[str] = []
    webhook_dir = None
    override = os.environ.get("WEBHOOK_REPO_DIR")
    candidates = (
        [Path(override)]
        if override
        else [
            Path.home() / "webhook",
            Path.home() / "factory" / "webhook",
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            webhook_dir = candidate
            break
    if webhook_dir is None or config is None:
        local_one.append(
            "Worker ERROR_REPO_MAP vs repositories.yml 双侧一致性不可本地验证（LOCAL-ONE）"
        )
        return findings, local_one
    worker_sources = [
        p
        for p in webhook_dir.rglob("*.js")
        if p.is_file() and "ERROR_REPO_MAP" in p.read_text(encoding="utf-8", errors="replace")
    ]
    if not worker_sources:
        local_one.append("webhook checkout present but ERROR_REPO_MAP source not found (LOCAL-ONE)")
        return findings, local_one
    slugs = registered_repo_slugs(config)
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in worker_sources)
    for slug in sorted(slugs):
        if slug not in text:
            worker = worker_sources[0].name
            findings.append(
                (
                    slug,
                    f"registered in repositories.yml but absent from {worker} ERROR_REPO_MAP",
                )
            )
    return findings, local_one


def main() -> int:
    frozen, unverifiable = check_frozen_repos()
    github_findings = frozen + check_substrate_faces()
    config = load_repositories_yml()
    local_findings, local_one = check_local_registry(config)
    worker_findings, worker_local_one = check_worker_routing(config)
    local_one.extend(worker_local_one)

    registered = set(registry_entries("Gate 2"))
    stock = [f for f in github_findings if any(tok and tok in f[1] for tok in registered)]
    new = [f for f in github_findings if f not in stock]

    print("# Gate 2: Cross-repo Drift Inspection")
    print(f"- GitHub-visible findings: {len(github_findings)} (registered stock: {len(stock)})")
    for slug, detail in stock:
        print(f"- REGISTERED STOCK [{slug}] {detail}")
    for slug, detail in new:
        print(f"- NEW FINDING [{slug}] {detail}")
    for note in unverifiable:
        print(
            f"- UNVERIFIABLE: {note} (GitHub-visible-surface limit; not red, tracked in registry)"
        )
    if local_findings or worker_findings:
        print(f"- LOCAL-face findings: {len(local_findings) + len(worker_findings)}")
        for slug, detail in local_findings + worker_findings:
            owner = OWNER_WORKER if "ERROR_REPO_MAP" in detail else OWNER_REGISTRY
            print(f"- LOCAL RED [{slug}] {detail} (owner feature: {owner})")
    for item in local_one:
        print(f"- LOCAL-ONE: {item} (owner features: {OWNER_REGISTRY} / {OWNER_WORKER})")

    exit_code = 0
    if new:
        print("FAIL: new GitHub-visible drift must be fixed or registered (owner + feature)")
        exit_code = 1
    if local_findings or worker_findings:
        print("RED (local face): cleanup owned by the follow-up features listed above")
        exit_code = 1
    if exit_code == 0:
        print("PASS: GitHub-visible surface clean; local faces verified or LOCAL-ONE registered")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
