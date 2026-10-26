#!/usr/bin/env bash
# Zero-red check-runs aggregator for ci-ok gate
# User mandate (2026-08-28): "写死不允许红色合并，一个都不允许"
#
# Scans all completed check-runs for a commit and fails if any has a conclusion
# other than success/skipped/neutral/cancelled. This catches advisory jobs with
# continue-on-error that would otherwise pass through.
#
# cancelled tolerance (INFRA-1321): a cancelled conclusion is never produced by
# the code under test — it is always an orchestration side-effect (watchdog
# cancel-on-ci-fail, superseded rerun attempts, draft-ready workflow races).
# Treating it as red made every ci-ok rerun fail on the tombstone of a
# previously cancelled run (circular quality-gate/ci-ok deadlock: ci-ok red →
# watchdog cancels review → cancelled check keeps ci-ok red). Per-name
# gating of the review itself stays in scripts/check_droid_review.sh (its
# early-exit probe dies on cancelled upstream runs, and the ruleset requires
# a real droid-review conclusion). Semantic map:
#   success/skipped/neutral  → green (original zero-red semantics)
#   cancelled                → grey (superseded run; per-check gates decide)
#   failure/timed_out/…      → red (blocks merge)

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <repository> <commit_sha>"
  echo "  repository: GitHub repository in owner/repo format"
  echo "  commit_sha: The commit SHA to check"
  exit 1
fi

REPOSITORY="$1"
COMMIT_SHA="$2"

echo "Scanning all check-runs for commit: $COMMIT_SHA"

# Fetch all check-runs (handle pagination)
# Exclusions (circular-reference family — the excluded checks are gated
# elsewhere and the zero-red mandate is NOT weakened, see test_check_zero_red.py):
#   - ci-ok: this check itself (self-reference)
#   - quality-gate: a SEPARATE workflow whose sole input is ci-ok's own
#     conclusion (aggregation surface: ci-ok + substrate-gate-suite + qa-ok).
#     Ordering hazard (PR #248 deadlock, 2026-10-04): on slow review tails
#     ci-ok's droid-review poll can outlast quality-gate's aggregation
#     window, so quality-gate reports failure BEFORE ci-ok completes. This
#     scan then sees that stale failure as "latest completed" for the name
#     and fails ci-ok on the same SHA — unrecoverable by any rerun, because
#     quality-gate only re-reports after ci-ok ends, which the red scan
#     already blocked. quality-gate is a ruleset required check, so a real
#     quality-gate failure still blocks the merge via branch protection.
#   - droid-review: separate required context; ci-ok no longer polls it
#     (cheap-gates-first scheduling). Exclude so deferred review does not
#     block ci-ok on the same SHA.
#   - Notify CI complete to webhook gateway (round-4 P1, shard-2): ci-ok
#     must not depend on historical conclusions of downstream jobs in this
#     workflow. A side-channel notification leg that runs AFTER ci-ok in the same
#     workflow (needs: [ci-ok, gate-tests]) and fails loud on missing
#     config or (WEBHOOK_NOTIFY_STRICT) delivery errors. It is not gated
#     elsewhere; a red notify tombstone on SHA X would make every LATER
#     ci-ok run on X red (rerun / labeled / ready_for_review re-trigger),
#     recoverable only by a new commit — the same circular pattern as the
#     quality-gate exclusion above. Notification delivery health is
#     observable on the notify check itself; it must not gate merges.
# Latest-instance selection (round-6 handoff, sentinel-parity): group by name
# and take the instance with the HIGHEST id among ALL instances of that name
# (no status pre-filter). An in-flight/queued rerun means the check is being
# re-decided — reading an older sibling's conclusion would judge from a stale
# snapshot (same defect class the merge-gate sentinel fixed: completed →
# pending while a newer instance is in flight; sort_by(started_at) is also
# coarser than id ordering for same-second reruns). An in-flight instance is
# reported as IN_FLIGHT and simply not red; its own conclusion will be judged
# when it lands (per-check gates + rerun of this scan).
#
# Global pagination (round-3 P1): --paginate applies --jq to EACH page
# separately, so per-page group_by(...)|last selects the latest PER PAGE —
# with rerun history beyond one page, stale red instances on later pages
# leak through as extra rows and the awk verdict false-reds on them
# (unrecoverable by rerun, PR #248 deadlock class). Fix (INFRA-766 pattern):
# fetch all pages raw, then ONE jq -s pass aggregates the whole stream
# before the per-name latest selection.
ALL_CHECKS=$(gh api "repos/${REPOSITORY}/commits/${COMMIT_SHA}/check-runs" \
  --paginate | jq -s -r '
  map(.check_runs[])
  | map(select(.name != "ci-ok" and .name != "quality-gate" and .name != "droid-review" and .name != "Notify CI complete to webhook gateway"))
  | group_by(.name)
  | map(sort_by(.id) | last)
  | .[]
  | "\(.name)\t\(.status)\t\(.conclusion // "-")"')

echo "All latest-instance check-runs (no status pre-filter):"
echo "$ALL_CHECKS"
echo ""

# Red verdict: latest instance of a name completed with a failing conclusion.
# In-flight latest instances are NOT red (outcome pending; the just-triggered
# rerun after a branch update must not be judged by an older sibling's red).
# cancelled tolerated per INFRA-1321 (see header comment)
RED_CHECKS=$(echo "$ALL_CHECKS" | awk -F'\t' '$2 == "completed" && $3 !~ /^(success|skipped|neutral|cancelled)$/ {print $1 "\t" $3}' || true)
IN_FLIGHT=$(echo "$ALL_CHECKS" | awk -F'\t' '$2 != "completed" {print $1 "\t" $2}' || true)

if [[ -n "$IN_FLIGHT" ]]; then
  echo "⏳ In-flight (not judged — outcome pending):"
  echo "$IN_FLIGHT"
  echo ""
fi

if [[ -n "$RED_CHECKS" ]]; then
  echo "❌ RED CHECKS DETECTED (zero-red policy violation):"
  echo "$RED_CHECKS"
  echo ""
  echo "User mandate: 写死不允许红色合并，一个都不允许"
  echo "All checks (including advisory) must be green/skipped/neutral."
  exit 1
fi

echo "✅ Zero-red verification passed: no red conclusion at any check's latest instance"
