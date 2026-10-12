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
# Group by name and take latest completed to handle reruns
ALL_CHECKS=$(gh api "repos/${REPOSITORY}/commits/${COMMIT_SHA}/check-runs" \
  --paginate \
  --jq '[.check_runs[] | select(.status == "completed") | select(.name != "ci-ok" and .name != "quality-gate")] | group_by(.name) | map(sort_by(.started_at) | last) | .[] | "\(.name)\t\(.conclusion)"')

echo "All completed check-runs:"
echo "$ALL_CHECKS"
echo ""

# Check for any non-success conclusions
# cancelled tolerated per INFRA-1321 (see header comment)
RED_CHECKS=$(echo "$ALL_CHECKS" | grep -vE $'\t(success|skipped|neutral|cancelled)$' || true)

if [[ -n "$RED_CHECKS" ]]; then
  echo "❌ RED CHECKS DETECTED (zero-red policy violation):"
  echo "$RED_CHECKS"
  echo ""
  echo "User mandate: 写死不允许红色合并，一个都不允许"
  echo "All checks (including advisory) must be green/skipped/neutral."
  exit 1
fi

echo "✅ Zero-red verification passed: all check-runs are success/skipped/neutral/cancelled"
