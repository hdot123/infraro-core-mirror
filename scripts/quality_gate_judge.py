#!/usr/bin/env python3
"""Quality Gate single-shot judge (pipeline cutover · FINAL-PLAN §1.2 / §2 QG).

Replaces the legacy 200×30s poller. One API snapshot → one of three outcomes:

* SUCCESS   — all expected surfaces completed/success (exit 0)
* NOT_READY — a peer is missing / in-flight / cancelled-without-successor (exit 1)
* FAILURE   — a peer completed non-success (exit 1)

Always prints one machine-readable line::

    QG_RESULT={"outcome":"NOT_READY","expected":[...],"gates":{...},...}

Callers (Actions / sentinel / auto-merge) must treat NOT_READY as fail-closed
for ruleset (non-green) but distinct from quality FAILURE for retry/noise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Surfaces aggregated into the quality-gate check. droid-review is intentionally
# absent (independently required; triggered downstream on QG success).
PR_EXPECTED = ("ci-ok", "substrate-gate-suite", "qa-ok")
PUSH_EXPECTED = ("ci-ok", "substrate-gate-suite")

RED_CONCLUSIONS = frozenset(
    {
        "failure",
        "timed_out",
        "action_required",
        "neutral",
        "stale",
        "startup_failure",
    }
)

QG_RESULT_PREFIX = "QG_RESULT="


def expected_checks_for(upstream_event: str) -> tuple[str, ...]:
    """qa-ok only exists on pull_request (QA has no push trigger)."""
    if upstream_event == "pull_request":
        return PR_EXPECTED
    return PUSH_EXPECTED


def _latest_by_name(check_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per check name: prefer any non-completed run as pending; else latest by id."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for run in check_runs:
        name = run.get("name")
        if not isinstance(name, str):
            continue
        by_name.setdefault(name, []).append(run)

    latest: dict[str, dict[str, Any]] = {}
    for name, runs in by_name.items():
        pending = [r for r in runs if r.get("status") != "completed"]
        if pending:
            # Any in-flight instance → surface not ready (single-shot).
            pick = max(pending, key=lambda r: int(r.get("id") or 0))
            latest[name] = {
                "status": pick.get("status") or "pending",
                "conclusion": pick.get("conclusion"),
                "id": pick.get("id"),
            }
            continue
        completed = [
            r
            for r in runs
            if r.get("status") == "completed" and r.get("conclusion") not in (None, "cancelled")
        ]
        if completed:
            pick = max(completed, key=lambda r: int(r.get("id") or 0))
            latest[name] = {
                "status": "completed",
                "conclusion": pick.get("conclusion"),
                "id": pick.get("id"),
            }
            continue
        # Only cancelled (or empty conclusion) leftovers → treat as not ready.
        if runs:
            pick = max(runs, key=lambda r: int(r.get("id") or 0))
            latest[name] = {
                "status": pick.get("status") or "completed",
                "conclusion": pick.get("conclusion") or "cancelled",
                "id": pick.get("id"),
            }
    return latest


def judge_gates(
    *,
    expected: tuple[str, ...] | list[str],
    check_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure judge. Returns a result dict with outcome SUCCESS|NOT_READY|FAILURE."""
    latest = _latest_by_name(check_runs)
    gates: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    not_ready: list[str] = []
    failed: list[str] = []

    for name in expected:
        state = latest.get(name)
        if state is None:
            missing.append(name)
            gates[name] = {"status": "missing", "conclusion": None}
            continue
        status = state.get("status") or "missing"
        conclusion = state.get("conclusion")
        gates[name] = {"status": status, "conclusion": conclusion, "id": state.get("id")}
        if status != "completed":
            not_ready.append(name)
        elif conclusion == "success":
            continue
        elif conclusion in RED_CONCLUSIONS:
            failed.append(name)
        else:
            # cancelled / unknown completed → peer not stably ready
            not_ready.append(name)

    if failed:
        return {
            "outcome": "FAILURE",
            "expected": list(expected),
            "gates": gates,
            "failed": failed,
            "not_ready": not_ready,
            "missing": missing,
        }
    if missing or not_ready:
        return {
            "outcome": "NOT_READY",
            "expected": list(expected),
            "gates": gates,
            "failed": failed,
            "not_ready": not_ready + missing,
            "missing": missing,
        }
    return {
        "outcome": "SUCCESS",
        "expected": list(expected),
        "gates": gates,
        "failed": [],
        "not_ready": [],
        "missing": [],
    }


def fetch_check_runs(repository: str, commit_sha: str, token: str) -> list[dict[str, Any]]:
    """GET /repos/{repo}/commits/{sha}/check-runs (single page, per_page=100)."""
    url = f"https://api.github.com/repos/{repository}/commits/{commit_sha}/check-runs?per_page=100"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "infraro-core-quality-gate-judge",
        },
        method="GET",
    )
    try:
        # Hardcoded api.github.com; no user-controlled scheme (B310 exempt).
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"check-runs HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"check-runs network error: {exc}") from exc
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        raise RuntimeError("check-runs response missing check_runs array (auth/rate-limit?)")
    return runs


def resolve_pr_number(
    repository: str,
    head_sha: str,
    token: str,
    *,
    hint: str | None = None,
) -> str | None:
    """Stable PR number: event hint first, else commits/{sha}/pulls (never head_branch)."""
    if hint and str(hint).strip().isdigit():
        return str(int(hint))
    url = f"https://api.github.com/repos/{repository}/commits/{head_sha}/pulls"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "infraro-core-quality-gate-judge",
        },
        method="GET",
    )
    try:
        # Hardcoded api.github.com; no user-controlled scheme (B310 exempt).
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"commits/pulls HTTP {exc.code}: {body[:500]}") from exc
    if not isinstance(payload, list) or not payload:
        return None
    number = payload[0].get("number")
    return str(number) if number is not None else None


def emit_result(result: dict[str, Any]) -> None:
    line = QG_RESULT_PREFIX + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    print(line, flush=True)


def outcome_exit_code(outcome: str) -> int:
    return 0 if outcome == "SUCCESS" else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repository", default=os.environ.get("REPOSITORY", ""))
    p.add_argument("--head-sha", default=os.environ.get("HEAD_SHA", ""))
    p.add_argument(
        "--upstream-event",
        default=os.environ.get("UPSTREAM_EVENT", "pull_request"),
        help="Originating workflow_run.event (pull_request|push|...)",
    )
    p.add_argument(
        "--pr-number-hint",
        default=os.environ.get("PR_NUMBER_HINT", ""),
        help="Optional PR number from workflow_run.pull_requests[0]",
    )
    p.add_argument(
        "--resolve-pr",
        action="store_true",
        help="Resolve/confirm PR number (required for pull_request upstream)",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "",
    )
    p.add_argument(
        "--check-runs-json",
        default="",
        help="Optional path to fixture JSON ({check_runs:[...]}) — skips API fetch",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    upstream = args.upstream_event or "pull_request"
    expected = expected_checks_for(upstream)

    pr_number: str | None = None
    if args.resolve_pr or upstream == "pull_request":
        if not args.repository or not args.head_sha or not args.token:
            if not args.check_runs_json:
                emit_result(
                    {
                        "outcome": "FAILURE",
                        "reason": "missing_resolve_inputs",
                        "expected": list(expected),
                    }
                )
                return 1
        if args.token and args.repository and args.head_sha:
            try:
                pr_number = resolve_pr_number(
                    args.repository,
                    args.head_sha,
                    args.token,
                    hint=args.pr_number_hint or None,
                )
            except RuntimeError as exc:
                emit_result(
                    {
                        "outcome": "FAILURE",
                        "reason": f"pr_resolve_error:{exc}",
                        "expected": list(expected),
                    }
                )
                return 1
        elif args.pr_number_hint and str(args.pr_number_hint).strip().isdigit():
            pr_number = str(int(args.pr_number_hint))
        if upstream == "pull_request" and not pr_number:
            emit_result(
                {
                    "outcome": "FAILURE",
                    "reason": "unresolved_pr",
                    "expected": list(expected),
                    "head_sha": args.head_sha,
                }
            )
            return 1

    if args.check_runs_json:
        payload = json.loads(Path(args.check_runs_json).read_text(encoding="utf-8"))
        check_runs = payload.get("check_runs", payload if isinstance(payload, list) else [])
        if not isinstance(check_runs, list):
            emit_result({"outcome": "FAILURE", "reason": "bad_fixture", "expected": list(expected)})
            return 1
    else:
        if not args.repository or not args.head_sha or not args.token:
            emit_result(
                {
                    "outcome": "FAILURE",
                    "reason": "missing_api_inputs",
                    "expected": list(expected),
                }
            )
            return 1
        try:
            check_runs = fetch_check_runs(args.repository, args.head_sha, args.token)
        except RuntimeError as exc:
            # Transient API errors → NOT_READY (peer may be fine; caller may retry
            # via next CI/QA completion). Never skip / fake success.
            emit_result(
                {
                    "outcome": "NOT_READY",
                    "reason": f"api_error:{exc}",
                    "expected": list(expected),
                    "pr_number": pr_number,
                    "head_sha": args.head_sha,
                }
            )
            return 1

    result = judge_gates(expected=expected, check_runs=check_runs)
    result["pr_number"] = pr_number
    result["head_sha"] = args.head_sha
    result["upstream_event"] = upstream
    emit_result(result)
    print(
        f"quality-gate judge: outcome={result['outcome']} "
        f"pr={pr_number or '-'} sha={args.head_sha or '-'} "
        f"expected={list(expected)}",
        flush=True,
    )
    return outcome_exit_code(str(result["outcome"]))


if __name__ == "__main__":
    sys.exit(main())
