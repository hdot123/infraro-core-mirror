"""Gate 1: interface reality (engine repo side).

Every declaration-repo template that calls into this engine must match the
engine's real declaration face:

* reusable-workflow ``uses`` targets must exist and expose ``workflow_call``;
* every ``with:`` key must be a declared input (an undeclared key passed means
  a run-level startup failure -- memory #1075);
* every required input/secrets entry without a default must be passed;
* the pinned ``@ref`` must be an existing git tag (references to deleted
  tags fail here);
* composite-action ``uses`` targets must exist with matching declared inputs.

Template source resolution: sibling declaration checkout (``DECL_REPO_DIR`` or
``../infraro``) when present, else the public GitHub surface of
``hdot123/infraro`` via ``gh api`` (charter round-3 item A4), else the public
source tarball for runners without ``gh`` auth. Faces live in
``templates/<stack>/*.yml`` (python/typescript), with a flat ``templates/*.yml``
fallback for older archives.

Known existing stock (watchdog/droid-review/governance per-key breaks) is
registered in the stock registry and owned by the follow-up features; NEW
unregistered breaks fail the gate.

Fail-closed (audit-defense-hardening): when the declaration templates cannot be
fetched, or a pinned ``vX.Y.Z`` ref cannot be checked because the engine tags are
unavailable, the gate reports an explicit error and exits non-zero instead of
silently passing -- an unverifiable interface face is a failure, not a pass.
"""

from __future__ import annotations

import io
import re
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from gate_common import (
    DECL_REPO_SLUG,
    ENGINE_REPO_SLUG,
    ENGINE_REPO_URL,
    REPO_ROOT,
    decl_dir,
    gh_api,
    load_yaml,
    registry_entries,
    remote_tags,
    workflow_call_face,
)

ENGINE_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ENGINE_ACTIONS = REPO_ROOT / "actions"

# 声明仓 hdot123/infraro 是公开仓：无凭证环境（CI runner 无 gh auth）经源码
# tarball 取模板面，避开 contents API 的未认证速率限制（60 req/h/IP）。
DECL_REPO_TARBALL = f"https://codeload.github.com/{DECL_REPO_SLUG}/tar.gz/refs/heads/main"

WF_REF_RE = re.compile(
    r"^(?P<repo>[^/]+/[^/]+)/(?P<path>\.github/workflows/(?P<name>[A-Za-z0-9_.-]+\.yml))@(?P<ref>\S+)$"
)
ACTION_REF_RE = re.compile(
    r"^(?P<repo>[^/]+/[^/]+)/(?P<path>actions/(?P<name>[A-Za-z0-9_-]+))@(?P<ref>\S+)$"
)


def _templates_in_dir(root: Path) -> dict[str, str]:
    """Templates directly under *root* as {filename: text} (flat ``*.yml``)."""
    if not root.is_dir():
        return {}
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(root.glob("*.yml"))}


def _templates_in_tree(root: Path) -> dict[str, str]:
    """Templates under *root*: flat ``*.yml`` plus one stack-dir level.

    Per-stack faces live in ``templates/python`` / ``templates/typescript``;
    older archives kept them flat. Deeper trees (``templates/<stack>/.github/``)
    are consumer workflow copies, not engine declaration faces, so they stay out
    of scope.
    """
    templates = _templates_in_dir(root)
    if not root.is_dir():
        return templates
    for stack in sorted(p for p in root.iterdir() if p.is_dir()):
        for name, text in _templates_in_dir(stack).items():
            templates.setdefault(name, text)
    return templates


def _templates_from_entries(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Download the ``.yml`` files of a GitHub contents listing."""
    templates: dict[str, str] = {}
    for item in entries:
        name = item.get("name", "")
        url = item.get("download_url")
        if isinstance(name, str) and name.endswith(".yml") and isinstance(url, str):
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                templates[name] = resp.read().decode("utf-8")
    return templates


def _templates_from_github() -> dict[str, str]:
    """Templates via ``gh api`` (environments with gh credentials)."""
    listing = gh_api(f"repos/{DECL_REPO_SLUG}/contents/templates")
    if not isinstance(listing, list):
        return {}
    templates = _templates_from_entries(listing)
    for item in listing:
        if item.get("type") != "dir":
            continue
        sub = gh_api(f"repos/{DECL_REPO_SLUG}/contents/templates/{item.get('name')}")
        if isinstance(sub, list):
            for name, text in _templates_from_entries(sub).items():
                templates.setdefault(name, text)
    return templates


def _templates_from_public_tarball() -> dict[str, str]:
    """Templates from the public declaration repo without any credentials."""
    try:
        with urllib.request.urlopen(DECL_REPO_TARBALL, timeout=60) as resp:  # noqa: S310
            payload = resp.read()
    except (OSError, ValueError):
        return {}
    templates: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                parts = member.name.split("/")
                if not member.isfile() or len(parts) not in {3, 4}:
                    continue
                if parts[1] != "templates" or not parts[-1].endswith(".yml"):
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    templates.setdefault(parts[-1], handle.read().decode("utf-8"))
    except (tarfile.TarError, EOFError, OSError):
        return {}
    return templates


def fetch_decl_templates() -> dict[str, str]:
    """Declaration templates as {filename: text} from local sibling or GitHub.

    Resolution order: local sibling checkout (``DECL_REPO_DIR`` / ``../infraro``),
    then the authenticated ``gh api`` surface, then the public source tarball. An
    empty result means the declaration face is genuinely unverifiable -- the
    caller treats that as fail-closed.
    """
    local = decl_dir()
    if local is not None:
        templates = _templates_in_tree(local / "templates")
        if templates:
            return templates
    templates = _templates_from_github()
    return templates or _templates_from_public_tarball()


def _required_without_default(decl: dict[str, Any]) -> list[str]:
    """Keys declared required=True with no default in an inputs/secrets face."""
    missing = []
    for key, spec in decl.items():
        if isinstance(spec, dict) and spec.get("required") is True:
            if not spec.get("default"):
                missing.append(key)
    return missing


def check_workflow_call(template: str, step: dict[str, Any], errors: list[str]) -> None:
    """Validate one reusable-workflow call step against the engine face."""
    uses = str(step.get("uses", ""))
    match = WF_REF_RE.match(uses)
    if not match:
        errors.append(f"{template}: unparseable workflow uses ref: {uses}")
        return
    if match.group("repo") != ENGINE_REPO_SLUG:
        return  # not an engine target; other owners out of scope
    workflow_path = ENGINE_WORKFLOWS / match.group("name")
    if not workflow_path.exists():
        errors.append(f"{template}: references non-existent engine workflow: {match.group('name')}")
        return
    doc = load_yaml(workflow_path)
    face = workflow_call_face(doc)
    if not face:
        errors.append(f"{template}: target {match.group('name')} exposes no workflow_call")
        return
    inputs = face.get("inputs") or {}
    secrets = face.get("secrets") or {}

    passed_with = set((step.get("with") or {}).keys())
    passed_secrets = set((step.get("secrets") or {}).keys())
    for key in sorted(passed_with - set(inputs)):
        errors.append(
            f"{template}: passes UNDECLARED input '{key}' to {match.group('name')}"
            " (caller传未声明键 → run 级 startup_failure)"
        )
    for key in sorted(passed_secrets - set(secrets)):
        errors.append(f"{template}: passes UNDECLARED secret '{key}' to {match.group('name')}")
    for key in sorted(set(_required_without_default(inputs)) - passed_with):
        errors.append(f"{template}: missing REQUIRED input '{key}' for {match.group('name')}")
    for key in sorted(set(_required_without_default(secrets)) - passed_secrets):
        errors.append(f"{template}: missing REQUIRED secret '{key}' for {match.group('name')}")


def check_composite_action(template: str, step: dict[str, Any], errors: list[str]) -> None:
    """Validate one composite-action call step against the engine action.yml."""
    uses = str(step.get("uses", ""))
    match = ACTION_REF_RE.match(uses)
    if not match:
        errors.append(f"{template}: unparseable action uses ref: {uses}")
        return
    if match.group("repo") != ENGINE_REPO_SLUG:
        return
    action_file = ENGINE_ACTIONS / match.group("name") / "action.yml"
    if not action_file.exists():
        errors.append(f"{template}: references non-existent engine action: {match.group('name')}")
        return
    doc = load_yaml(action_file)
    inputs = doc.get("inputs") or {}
    passed_with = set((step.get("with") or {}).keys())
    for key in sorted(passed_with - set(inputs)):
        errors.append(
            f"{template}: passes UNDECLARED input '{key}' to action {match.group('name')}"
        )
    for key in sorted(set(_required_without_default(inputs)) - passed_with):
        errors.append(
            f"{template}: missing REQUIRED input '{key}' for action {match.group('name')}"
        )


def check_ref_alive(template: str, uses: str, ref: str, tags: set[str], errors: list[str]) -> None:
    """The pinned @ref must be an existing tag (dead references fail)."""
    if not ref.startswith("v"):
        errors.append(f"{template}: uses non-tag ref '{ref}' (must pin a vX.Y.Z tag)")
    elif tags and ref not in tags:
        errors.append(f"{template}: DEAD reference '{ref}' (not in {ENGINE_REPO_SLUG} tags)")


def check_docs_references(docs_dir: Path, tags: set[str], errors: list[str]) -> None:
    """Documented engine refs must exist (tag + workflow name, dead-ref sweep)."""
    if not docs_dir.is_dir():
        return
    pattern = re.compile(
        r"hdot123/infraro-core/(?:\.github/workflows/([A-Za-z0-9_.-]+\.yml)|actions/([A-Za-z0-9_-]+))@([\w.-]+)"
    )
    for md in sorted(docs_dir.rglob("*.md")):
        for workflow_name, action_name, ref in pattern.findall(
            md.read_text(encoding="utf-8", errors="replace")
        ):
            if workflow_name and not (ENGINE_WORKFLOWS / workflow_name).exists():
                errors.append(
                    f"{md.relative_to(REPO_ROOT)}: dead engine workflow ref {workflow_name}"
                )
            if action_name and not (ENGINE_ACTIONS / action_name).exists():
                errors.append(f"{md.relative_to(REPO_ROOT)}: dead engine action ref {action_name}")
            if ref.startswith("v") and tags and ref not in tags:
                errors.append(f"{md.relative_to(REPO_ROOT)}: DEAD tag reference @{ref}")


def main() -> int:
    templates = fetch_decl_templates()
    errors: list[str] = []
    checked = 0
    if not templates:
        errors.append(
            "declaration templates unavailable (no sibling checkout, gh api failed):"
            " template face unverifiable -- fail-closed (模板不可得，接口面不可验证)"
        )

    tags = remote_tags(ENGINE_REPO_URL)
    pinned_v_refs: list[str] = []
    for name, text in templates.items():
        try:
            doc = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: unparseable YAML: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        for job in (doc.get("jobs") or {}).values():
            if not isinstance(job, dict):
                continue
            uses = job.get("uses")
            if not isinstance(uses, str):
                continue
            checked += 1
            ref = uses.split("@")[-1] if "@" in uses else ""
            if ref.startswith("v"):
                pinned_v_refs.append(ref)
            check_ref_alive(name, uses, ref, tags, errors)
            if "/.github/workflows/" in uses:
                check_workflow_call(name, job, errors)
            elif "/actions/" in uses:
                check_composite_action(name, job, errors)

    tags_unavailable = bool(pinned_v_refs) and not tags
    if tags_unavailable:
        errors.append(
            "engine tags unavailable (git ls-remote failed):"
            f" {len(set(pinned_v_refs))} pinned v-ref(s) unverifiable --"
            " dead-reference sweep must not be skipped (tags 不可得，死引用检测不可跳过)"
        )
    fail_closed = (not templates) or tags_unavailable

    if (REPO_ROOT / "docs").is_dir():
        check_docs_references(REPO_ROOT / "docs", tags, errors)

    registered = registry_entries("Gate 1")
    owned, new = [], []
    for err in errors:
        if any(token and token in err for token in registered):
            owned.append(err)
        else:
            new.append(err)

    print("# Gate 1: Interface Reality (engine repo)")
    print(f"- template calls checked: {checked}; engine tags visible: {len(tags)}")
    for err in owned:
        print(f"- REGISTERED STOCK: {err}")
    for err in new:
        print(f"- NEW BREAK: {err}")
    print(f"- registered stock: {len(owned)}; new breaks: {len(new)}")

    if fail_closed:
        print("FAIL: interface face unverifiable -- fail-closed (模板/tags 不可得，不得静默放行)")
        return 1
    if new:
        print("FAIL: new interface breaks must be fixed or registered (owner + feature)")
        return 1
    print("PASS: no new interface breaks; existing stock registered and owned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
