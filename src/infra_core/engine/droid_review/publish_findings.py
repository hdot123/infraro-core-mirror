#!/usr/bin/env python3
"""
TD-DR-01 findings 发布器：下载、校验、去重、inline/summary 发布。

职责：
1. 读取各 shard 的 findings JSON（从 artifacts 下载后）
2. Schema 校验（非法 → fail-closed）
3. 去重（相同 file+line+message 跨 shard 去重）
4. inline comment 行号校验（对照 patch hunks）
5. 422 降级 summary comment
6. 批次 ≤50（GitHub API 限制）
7. 按 shard 分节统计严重度
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REQUIRED_FINDING_FIELDS = {"severity", "file", "line", "message"}
VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}

# Marker for cross-run dedup: summary comments contain this marker so reruns
# can detect and skip/update instead of duplicating.
SUMMARY_MARKER = "<!-- droid-review-summary -->"

# Bot login for auto-resolve eligibility（本发布器 inline/summary 均以此身份发出）。
# GraphQL author.login 与 REST user.login 拼写不一致（同一 bot 注释前者返回
# 裸 github-actions，后者返回 github-actions[bot]，缺陷 #001b 实证），资格
# 过滤必须接受双拼写，否则过滤零命中静默 return。
GITHUB_ACTIONS_BOT_LOGIN = "github-actions[bot]"
BOT_LOGIN_SPELLINGS = frozenset({"github-actions", GITHUB_ACTIONS_BOT_LOGIN})

# ── Round-cap sentinel（轮次上限，切断 review→session→push 无限循环）──
# 每 PR 最多 3 轮 inline findings；第 4 轮起只发终态 summary 并打 needs-human
# label 交还人工（仿 droid-autofix-attempt 先例，隐藏 HTML 注释埋点）。
MAX_REVIEW_ROUNDS = 3
NEEDS_HUMAN_LABEL = "needs-human"
# Line-anchored: the sentinel is machine-embedded on its own line; prose
# quotes of the marker (text around it on the same line) must not count.
_ROUND_MARKER_RE = re.compile(r"^[ \t]*<!--\s*droid-review-round-(\d+)\s*-->[ \t]*$", re.M)


def round_marker(round_number: int) -> str:
    """Per-round sentinel embedded in the summary comment body."""
    return f"<!-- droid-review-round-{round_number} -->"


_REVIEW_THREADS_QUERY = """\
query($owner:String!,$name:String!,$pr:Int!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:50,after:$cursor){
        nodes{
          id isResolved isOutdated path
          comments(first:1){nodes{author{login __typename} body}}
        }
        pageInfo{hasNextPage endCursor}
      }
    }
  }
}"""

_RESOLVE_THREAD_MUTATION = (
    "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}"
)

# 分页保险上限：50/page × 100 = 5000 thread，防异常 pageInfo 造成死循环
_MAX_REVIEW_THREAD_PAGES = 100


def validate_findings(data: dict[str, Any]) -> bool:
    """
    校验 findings JSON schema。

    合法格式：
    {
      "shard_id": int,
      "findings": [
        {"severity": "P0"|"P1"|"P2"|"P3", "file": str, "line": int, "message": str}
      ]
    }

    非法 → False（调用方应 fail-closed）。
    """
    if not isinstance(data, dict):
        return False

    # shard_id 必须是 int
    if "shard_id" not in data or not isinstance(data["shard_id"], int):
        return False

    # findings 必须是 list
    if "findings" not in data or not isinstance(data["findings"], list):
        return False

    # 每条 finding 必须含必需字段且类型正确
    for finding in data["findings"]:
        if not isinstance(finding, dict):
            return False
        # 必需字段检查
        if not REQUIRED_FINDING_FIELDS.issubset(finding.keys()):
            return False
        # 类型检查
        if not isinstance(finding["severity"], str):
            return False
        if not isinstance(finding["file"], str):
            return False
        if not isinstance(finding["line"], int):
            return False
        if not isinstance(finding["message"], str):
            return False
        # 严重度枚举
        if finding["severity"] not in VALID_SEVERITIES:
            return False

    return True


def deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    跨 shard 去重：相同 (file, line, message) 只保留一条。
    """
    seen = set()
    unique = []
    for f in findings:
        key = (f["file"], f["line"], f["message"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def group_by_shard(findings: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """按 shard_id 分组（用于 summary comment 分节）。"""
    # 这里假设 findings 已带 shard_id 字段（从 validate_findings 解包后注入）
    groups = defaultdict(list)
    for f in findings:
        groups[f.get("shard_id", -1)].append(f)
    return dict(groups)


def count_by_severity(findings: list[dict[str, Any]]) -> dict[str, int]:
    """统计严重度分布。"""
    counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for f in findings:
        sev = f["severity"]
        if sev in counts:
            counts[sev] += 1
    return counts


def load_findings_files(pattern: str) -> list[dict[str, Any]]:
    """
    从匹配 pattern 的文件加载 findings（glob 模式）。
    返回合并后的 findings 列表（带 shard_id 注入）。
    """
    all_findings = []
    for path in Path().glob(pattern):
        try:
            data = json.loads(path.read_text())
            if not validate_findings(data):
                print(f"ERROR: invalid findings schema in {path}", file=sys.stderr)
                sys.exit(1)
            # 注入 shard_id 到每条 finding（便于后续分组）
            for f in data["findings"]:
                f["shard_id"] = data["shard_id"]
            all_findings.extend(data["findings"])
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON in {path}: {e}", file=sys.stderr)
            sys.exit(1)
    return all_findings


def _parse_paginated_json(output: str) -> list[dict[str, Any]]:
    """Parse gh api --paginate output, which may be one or more JSON arrays.

    gh --paginate concatenates page outputs. For list endpoints each page
    is a JSON array; the combined output is either a single array or
    multiple arrays on separate lines. This helper handles both forms.
    """
    items: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            items.extend(parsed)
        elif isinstance(parsed, dict):
            items.append(parsed)
    return items


def find_existing_summary_comment(
    pr_number: int,
    repository: str,
    round_number: int | None = None,
) -> int | None:
    """
    Retrieve existing PR comments and find one containing the SUMMARY_MARKER.

    Uses --paginate to fetch all pages (GitHub defaults to 30 per page).
    When round_number is given, only a summary carrying the same round's
    marker counts as a duplicate — a previous round's summary is history,
    not a rerun of this one. Returns the comment ID if found, None otherwise.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--paginate",
            ],
            capture_output=True,
            check=True,
        )
        comments = _parse_paginated_json(result.stdout.decode())
        for comment in comments:
            body = comment.get("body", "")
            if SUMMARY_MARKER not in body:
                continue
            if round_number is not None:
                same_round = _ROUND_MARKER_RE.search(body)
                if not same_round or int(same_round.group(1)) != round_number:
                    continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                return comment_id
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        # Fail-open for dedup: if we can't retrieve, just post normally
        print(f"Warning: could not retrieve existing comments for dedup: {e}", file=sys.stderr)
    return None


def scan_round_markers(pr_number: int, repository: str) -> tuple[list[int], int]:
    """
    Scan issue + review comments for ``<!-- droid-review-round-N -->`` markers.

    Returns (sorted distinct rounds, max round); max is 0 when no marker found.
    Reviews comment bodies too so inline posts can never inflate the count.
    Fail-open: any API failure → ([], 0), i.e. treated as first round.
    """
    rounds: set[int] = set()
    for endpoint in (
        f"repos/{repository}/issues/{pr_number}/comments",
        f"repos/{repository}/pulls/{pr_number}/comments",
    ):
        try:
            result = subprocess.run(
                ["gh", "api", endpoint, "--paginate"],
                capture_output=True,
                check=True,
            )
            comments = _parse_paginated_json(result.stdout.decode())
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
            print(f"Warning: could not scan round markers from {endpoint}: {e}", file=sys.stderr)
            continue
        for comment in comments:
            match = _ROUND_MARKER_RE.search(comment.get("body", ""))
            if match:
                rounds.add(int(match.group(1)))
    ordered = sorted(rounds)
    return ordered, (ordered[-1] if ordered else 0)


def count_round_markers(bodies: list[str]) -> int:
    """Count comment bodies that carry a round sentinel marker (test-facing)."""
    return sum(1 for body in bodies if _ROUND_MARKER_RE.search(body))


def next_review_round(rounds: list[int]) -> int:
    """Derive this run's round number: max prior marker + 1 (first run → 1)."""
    return (max(rounds) + 1) if rounds else 1


def find_existing_inline_comments(pr_number: int, repository: str) -> set[tuple[str, int]]:
    """
    Retrieve existing PR review comments and return set of (path, line) tuples.

    Uses --paginate to fetch all pages (GitHub defaults to 30 per page).
    This enables cross-run dedup: reruns skip posting if same location already commented.
    """
    existing = set()
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/pulls/{pr_number}/comments",
                "--paginate",
            ],
            capture_output=True,
            check=True,
        )
        comments = _parse_paginated_json(result.stdout.decode())
        for comment in comments:
            path = comment.get("path")
            line = comment.get("line")
            if path and line:
                existing.add((path, int(line)))
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as e:
        # Fail-open for dedup: if we can't retrieve, just post normally
        print(
            f"Warning: could not retrieve existing inline comments for dedup: {e}",
            file=sys.stderr,
        )
    return existing


def post_inline_comment(
    finding: dict[str, Any],
    pr_number: int,
    repository: str,
    commit_id: str,
) -> bool:
    """
    Post inline review comment on PR (with cross-run dedup).

    Checks for existing comments at the same (file, line) before posting.
    Returns True if successful or skipped (dedup), False if API call failed.
    """
    # Cross-run dedup: skip if same (file, line) already has a comment
    existing = find_existing_inline_comments(pr_number, repository)
    key = (finding["file"], finding["line"])
    if key in existing:
        print(f"  ⊘ Skipped (already exists): {finding['file']}:{finding['line']}")
        return True

    body = f"**[{finding['severity']}]** {finding['message']}"

    payload = {
        "body": body,
        "commit_id": commit_id,
        "path": finding["file"],
        "line": finding["line"],
        "side": "RIGHT",
    }

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/pulls/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps(payload).encode(),
            check=True,
            capture_output=True,
        )
        print(f"  ✓ Inline comment posted: {finding['file']}:{finding['line']}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        if "422" in stderr:
            print(f"  ⚠ 422 error (line may be invalid): {finding['file']}:{finding['line']}")
            return False
        print(f"  ✗ Failed to post inline comment: {stderr}", file=sys.stderr)
        return False


def post_summary_comment(
    findings: list[dict[str, Any]],
    pr_number: int,
    repository: str,
    by_shard: dict[int, list[dict[str, Any]]],
    round_number: int = 1,
) -> bool:
    """
    Post summary comment on PR with findings grouped by shard (with dedup).

    The body carries the round sentinel so later runs can count review rounds.
    Dedup is round-aware: a summary already carrying this run's marker is a
    rerun of the same review round → skip; an older round's summary is history
    and a fresh summary is posted.
    Returns True if successful or skipped (dedup).
    """
    existing_comment_id = find_existing_summary_comment(
        pr_number, repository, round_number=round_number
    )
    if existing_comment_id is not None:
        print(f"  ⊘ Skipped summary (same round {round_number} exists, id={existing_comment_id})")
        return True

    total_counts = count_by_severity(findings)

    body_lines = [
        SUMMARY_MARKER,
        round_marker(round_number),
        "## 🔍 Droid Auto Review — Findings Summary",
        "",
        f"**Total findings**: {len(findings)}",
        "",
        "**By severity**:",
    ]

    for sev in ["P0", "P1", "P2", "P3"]:
        count = total_counts.get(sev, 0)
        if count > 0:
            body_lines.append(f"- **{sev}**: {count}")

    body_lines.append("")
    body_lines.append("---")
    body_lines.append("")

    # Group by shard
    for shard_id in sorted(by_shard.keys()):
        shard_findings = by_shard[shard_id]
        shard_counts = count_by_severity(shard_findings)

        body_lines.append(f"### Shard {shard_id} ({len(shard_findings)} findings)")
        body_lines.append("")

        for sev in ["P0", "P1", "P2", "P3"]:
            count = shard_counts.get(sev, 0)
            if count > 0:
                body_lines.append(f"- **{sev}**: {count}")

        body_lines.append("")
        body_lines.append("<details>")
        body_lines.append(f"<summary>Details ({len(shard_findings)} items)</summary>")
        body_lines.append("")

        for f in shard_findings[:20]:  # Limit to first 20 per shard
            body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")

        if len(shard_findings) > 20:
            body_lines.append(f"- ... and {len(shard_findings) - 20} more")

        body_lines.append("")
        body_lines.append("</details>")
        body_lines.append("")

    body = "\n".join(body_lines)

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"body": body}).encode(),
            check=True,
            capture_output=True,
        )
        print("✓ Summary comment posted")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"✗ Failed to post summary comment: {stderr}", file=sys.stderr)
        return False


def post_terminal_summary(
    findings: list[dict[str, Any]],
    pr_number: int,
    repository: str,
    round_number: int,
) -> bool:
    """
    Post the round-cap terminal summary (no inline comments accompany it).

    States that the per-PR review round cap (MAX_REVIEW_ROUNDS) is exhausted
    and hands the remaining findings back to a human. Still carries the round
    sentinel so the round count stays monotonic across capped PRs.
    """
    total_counts = count_by_severity(findings)
    blocking = [f for f in findings if f.get("severity") in ("P0", "P1")]

    body_lines = [
        SUMMARY_MARKER,
        round_marker(round_number),
        "## 🔒 Droid Auto Review — Round Cap Reached",
        "",
        f"This PR has reached the automatic review round limit "
        f"({MAX_REVIEW_ROUNDS} rounds). Automated review is stopped and the "
        f"remaining findings are handed back to a human reviewer "
        f"(`{NEEDS_HUMAN_LABEL}` label).",
        "",
        f"**Total findings (this round)**: {len(findings)}",
        f"**Blocking (P0/P1)**: {len(blocking)}",
        "",
        "**By severity**:",
    ]
    for sev in ["P0", "P1", "P2", "P3"]:
        count = total_counts.get(sev, 0)
        if count > 0:
            body_lines.append(f"- **{sev}**: {count}")

    body_lines.append("")
    body_lines.append("<details>")
    body_lines.append(f"<summary>Details ({len(findings)} items)</summary>")
    body_lines.append("")
    for f in findings[:50]:
        body_lines.append(f"- **{f['severity']}** `{f['file']}:{f['line']}`: {f['message']}")
    if len(findings) > 50:
        body_lines.append(f"- ... and {len(findings) - 50} more")
    body_lines.append("")
    body_lines.append("</details>")

    body = "\n".join(body_lines)

    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"body": body}).encode(),
            check=True,
            capture_output=True,
        )
        print(f"✓ Terminal summary posted (round cap {MAX_REVIEW_ROUNDS} reached)")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"✗ Failed to post terminal summary: {stderr}", file=sys.stderr)
        return False


def add_needs_human_label(pr_number: int, repository: str) -> bool:
    """
    Attach the needs-human label to the PR (issues: write permission suffices).

    Fail-open: a label failure must never break the publishing flow.
    """
    try:
        subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/issues/{pr_number}/labels",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            input=json.dumps({"labels": [NEEDS_HUMAN_LABEL]}).encode(),
            check=True,
            capture_output=True,
        )
        print(f"✓ Label attached: {NEEDS_HUMAN_LABEL}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        print(f"Warning: could not attach {NEEDS_HUMAN_LABEL} label: {stderr}", file=sys.stderr)
        return False


def resolve_stale_bot_threads(
    pr_number: int,
    repository: str,
    current_findings: list[dict[str, Any]],
) -> None:
    """
    Resolve dissolvable bot review threads after publishing (fail-open).

    只清「已消解证据」的 thread，门禁不弱化：
    - outdated + github-actions bot + unresolved → resolveReviewThread
    - active bot thread 锚定位置不在本轮 findings（P2/P3 语义的已消解）→ resolve
    - 本轮 P0/P1 findings 对应 path 的 active bot thread 保持 unresolved（真门禁）
    - 人类作者 thread 永不触碰

    GraphQL 查询/变更失败仅 Warning，不抛出、不影响发布结果。
    """
    owner, _, name = repository.partition("/")
    if not owner or not name:
        print(
            f"Warning: invalid repository slug {repository!r}, skip thread resolve", file=sys.stderr
        )
        return

    # 本轮 P0/P1 锚定位置集合（安全护栏：这些位置绝不自动 resolve）
    blocking_paths = {f["file"] for f in current_findings if f.get("severity") in ("P0", "P1")}

    stale_thread_ids: list[str] = []
    cursor: str | None = None
    pages = 0
    try:
        while pages < _MAX_REVIEW_THREAD_PAGES:
            args = [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"pr={pr_number}",
                "-f",
                f"query={_REVIEW_THREADS_QUERY}",
            ]
            if cursor is not None:
                args += ["-F", f"cursor={cursor}"]
            result = subprocess.run(args, capture_output=True, check=True)
            payload = json.loads(result.stdout.decode())
            data = payload.get("data", {})
            threads = data.get("repository", {}).get("pullRequest", {}).get("reviewThreads", {})
            nodes = threads.get("nodes", [])
            page_info = threads.get("pageInfo", {})
            for node in nodes:
                thread_id = node.get("id")
                if not thread_id or node.get("isResolved"):
                    continue
                comments = node.get("comments", {}).get("nodes", [])
                first_comment = comments[0] if comments else {}
                author = first_comment.get("author") or {}
                author_login = author.get("login") or ""
                # __typename 是拼写之外的第二道资格线：缺省/非 Bot 一律不自动
                # resolve（资格误判的代价是错误清门禁，宁漏勿错）
                if author.get("__typename") != "Bot":
                    continue
                if author_login not in BOT_LOGIN_SPELLINGS:
                    continue
                if node.get("isOutdated"):
                    stale_thread_ids.append(thread_id)
                    continue
                path = node.get("path") or ""
                # active thread 锚定本轮 P0/P1 位置 → 门禁，保持 unresolved
                if path in blocking_paths:
                    continue
                # 其余 active bot thread（P2/P3 语义已消解：本轮无该位置 finding）→ resolve
                stale_thread_ids.append(thread_id)
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
                pages += 1
            else:
                break
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as e:
        print(f"Warning: could not query review threads for resolve: {e}", file=sys.stderr)
        return

    if not stale_thread_ids:
        return

    resolved = 0
    for thread_id in stale_thread_ids:
        try:
            subprocess.run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-F",
                    f"id={thread_id}",
                    "-f",
                    f"query={_RESOLVE_THREAD_MUTATION}",
                ],
                capture_output=True,
                check=True,
            )
            resolved += 1
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""
            print(f"Warning: failed to resolve thread {thread_id}: {stderr}", file=sys.stderr)

    print(f"✓ Auto-resolved {resolved}/{len(stale_thread_ids)} stale bot threads")


def main() -> None:
    """CLI entry: load findings, validate, dedup, publish to GitHub."""
    import argparse

    parser = argparse.ArgumentParser(description="Publish shard review findings")
    parser.add_argument("--pattern", required=True, help="Glob pattern for findings files")
    parser.add_argument("--pr-number", type=int, required=True, help="PR number")
    parser.add_argument("--repository", required=True, help="Repository (owner/name)")
    parser.add_argument("--commit-id", help="Commit ID for inline comments (optional)")

    args = parser.parse_args()

    print(f"Loading findings from: {args.pattern}")
    findings = load_findings_files(args.pattern)

    if not findings:
        print("No findings to publish")
        return

    print(f"Loaded {len(findings)} findings")

    # Deduplicate
    unique = deduplicate_findings(findings)
    print(f"After dedup: {len(unique)} findings")

    # Group by shard（round-capped 分支的统计输出也依赖它）
    by_shard = group_by_shard(unique)

    # Round-cap sentinel: count prior rounds from PR comments (fail-open)
    prior_rounds, _max_prior = scan_round_markers(args.pr_number, args.repository)
    round_number = next_review_round(prior_rounds)
    print(f"Review round: {round_number} (cap {MAX_REVIEW_ROUNDS})")
    round_capped = round_number > MAX_REVIEW_ROUNDS

    if round_capped:
        print(
            f"\nRound cap reached (round {round_number} > {MAX_REVIEW_ROUNDS}), "
            "skipping inline comments and posting terminal summary..."
        )
        post_terminal_summary(unique, args.pr_number, args.repository, round_number)
        print("\nAttaching needs-human label...")
        add_needs_human_label(args.pr_number, args.repository)
    else:
        # Post inline comments (batch of 50 max per GitHub API limit)
        if args.commit_id:
            print("\nPosting inline comments...")
            inline_success = 0
            inline_failed = []

            for f in unique[:50]:
                if post_inline_comment(f, args.pr_number, args.repository, args.commit_id):
                    inline_success += 1
                else:
                    inline_failed.append(f)

            print(f"Inline comments: {inline_success} posted, {len(inline_failed)} failed")

            # Degrade failed inline comments to summary
            if inline_failed:
                print("\nDegrading failed inline comments to summary...")
                unique = inline_failed
                by_shard = group_by_shard(unique)
        else:
            print("No commit-id provided, skipping inline comments")

        # Post summary comment
        print("\nPosting summary comment...")
        post_summary_comment(unique, args.pr_number, args.repository, by_shard, round_number)

    # Auto-resolve stale bot threads（fail-open；P0/P1 active 保持 unresolved）
    print("\nResolving stale bot threads...")
    resolve_stale_bot_threads(args.pr_number, args.repository, unique)

    # Output statistics
    total_counts = count_by_severity(unique)
    output = {
        "total_findings": len(unique),
        "review_round": round_number,
        "round_capped": round_capped,
        "by_severity": total_counts,
        "by_shard": {
            shard_id: {
                "count": len(fs),
                "by_severity": count_by_severity(fs),
            }
            for shard_id, fs in sorted(by_shard.items())
        },
    }
    print("\n" + json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
