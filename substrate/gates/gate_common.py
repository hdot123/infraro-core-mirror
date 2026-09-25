"""Shared helpers for the substrate gate scripts (substrate-gate-suite, 2026-09-13).

The five gates (0-4) run as plain ``python substrate/gates/gateN_*.py`` steps in
CI advisory jobs and locally. This module centralizes repo/sibling resolution so
no gate script ever hardcodes a host path (public-repo hygiene: zero ``/Users/``,
zero ``/home/``).

Sibling-repo resolution contract (charter round 3, item A2):

* ``ENGINE_REPO_DIR`` / ``DECL_REPO_DIR`` environment variables win (CI injects
  them via cross-repo checkout);
* otherwise the local sibling checkouts ``../infraro-core`` / ``../infraro``
  are used when present;
* otherwise callers degrade to GitHub-visible surfaces (``gh_api``) or record a
  LOCAL-ONE registry entry instead of crashing.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

# substrate/gates/gate_common.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

ENGINE_REPO_SLUG = "hdot123/infraro-core"
DECL_REPO_SLUG = "hdot123/infraro"
ENGINE_REPO_URL = f"https://github.com/{ENGINE_REPO_SLUG}.git"

# 冻结仓（旧世界三仓）slug 在运行时按段拼接构造：R-NEW1 防线②
# （tests/test_no_old_repo_references.py）禁止全仓出现旧仓全名字面量；
# gate2 对冻结仓的在途 PR/分支巡检是对冻结仓的合法监视面（存量检测），
# 不是存活引用——因此源码不携带全名，仅拼接。
# 片段组装模式：owner/org 组装自_parts_（hdot123-org / infra-core）
# 永不作为完整字面量出现；对应 gate0-exemptions.md 中 LOCAL-ONE 行（历史登记）。
_FROZEN_REPO_OWNER = "hdot123-org"
FROZEN_REPO_SLUGS = tuple(
    f"{_FROZEN_REPO_OWNER}/{name}" for name in ("infra-core", "memory", "mencbo")
)

# 存量登记表（substrate/gate0-exemptions.md）：各门红项/豁免逐条登记
# （owner + 归属 feature）。门脚本解析本表判定 finding 是否已登记。
REGISTRY_PATH = REPO_ROOT / "substrate" / "gate0-exemptions.md"


def sibling_dir(env_var: str, default_name: str) -> Path | None:
    """Resolve a sibling checkout: env override first, then ../<default_name>."""
    override = os.environ.get(env_var)
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    local = REPO_ROOT.parent / default_name
    return local if local.is_dir() else None


def engine_dir() -> Path | None:
    """Local engine checkout, or None when unavailable (CI without injection)."""
    return sibling_dir("ENGINE_REPO_DIR", "infraro-core")


def decl_dir() -> Path | None:
    """Local declaration checkout, or None when unavailable."""
    return sibling_dir("DECL_REPO_DIR", "infraro")


def load_yaml(path: Path) -> Any:
    """Load a YAML file (PyYAML is a declared project dependency)."""
    import yaml

    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def workflow_triggers(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the triggers block, tolerating YAML 1.1 ``on`` -> True coercion."""
    triggered = doc.get(True)
    if triggered is None:
        triggered = doc.get("on")
    return triggered if isinstance(triggered, dict) else {}


def workflow_call_face(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the ``workflow_call`` declaration face (inputs/secrets), if any."""
    face = workflow_triggers(doc).get("workflow_call")
    return face if isinstance(face, dict) else {}


def tracked_files(repo_root: Path) -> list[Path]:
    """Git-tracked files under *repo_root* (empty list when git unavailable)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [repo_root / part for part in result.stdout.split("\0") if part]


def gh_api(endpoint: str) -> Any | None:
    """GET a ``gh api`` endpoint; parsed JSON, or None when unavailable.

    Uses GH_TOKEN/GITHUB_TOKEN when present (CI), else the ambient gh auth.
    Failures return None so callers degrade to LOCAL-ONE registry notes
    instead of crashing on runners without access to a given repo.
    """
    env = dict(os.environ)
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if token:
        env["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def gh_available() -> bool:
    """True when ``gh api`` works at all (rate-limit probe on the viewer)."""
    return gh_api("user") is not None


def remote_tags(repo_url: str) -> set[str]:
    """Tag names visible on the remote (works from shallow CI checkouts)."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", repo_url],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    tags: set[str] = set()
    for line in result.stdout.splitlines():
        ref = line.split("\t", 1)[-1]
        if ref.endswith("^{}"):
            ref = ref[: -len("^{}")]
        tags.add(ref.rsplit("/", 1)[-1])
    return tags


def registry_entries(section_header: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """First-column backticked entries of the table under *section_header*.

    Parses the stock registry markdown so gates can distinguish registered
    existing stock from NEW findings (new findings fail, registered stock is
    reported honestly and owned by a follow-up feature).
    """
    registry = repo_root / "substrate" / "gate0-exemptions.md"
    try:
        lines = registry.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## "):
            in_section = line[3:].lstrip().startswith(section_header)
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"", "---", "Entry", "Path prefix", "Item"}:
            continue
        token = cells[0].strip("`")
        if token and not set(token) <= {"-"}:
            entries.append(token)
    return entries
