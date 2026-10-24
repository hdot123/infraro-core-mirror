#!/bin/bash
# Check droid-review status for the current PR/commit
# Exit 0 if droid-review passed, exit 1 if failed or not found
# Skip gracefully for non-PR events (push to main)
#
# H1 observability ④⑤ (h1-alwayscheck-earlyexit, 2026-09-30):
#   - Early exit (④): if the upstream Droid Auto Review run for THIS SHA
#     already ended without a check (failure/cancelled/neutral family), or
#     the check is pinned to a non-PR-head SHA (stale-head — e.g. the R10
#     release-please relock race where pull_request_target locks the
#     pre-relock head), exit 1 immediately with a cause judgment instead of
#     burning the whole polling budget on a dead end.
#   - Head-drift guard (⑤, C4 contract): a success conclusion may only be
#     granted while the checked SHA is still the PR head. When the PR head
#     has drifted away from the checked SHA, this poller must never report
#     success for the stale SHA — the observed outcome is downgraded to
#     neutral semantics (merge blocked via the no-valid-review path), never
#     a fake green for an old SHA.
#   - Budget (④): 90 attempts × 30s = 45 min, raised from 60×30s ≈ 30 min
#     (itself tightened from the original 120×30s ≈ 60 min). Derivation: the
#     upstream aggregate ceiling is 135 min (45 min shard timeout + 90 min
#     aggregate timeout, droid-review.yml budget layers), so a poller budget
#     at or beyond that ceiling can never out-wait a healthy run — it only
#     delays ci-ok's red. 45 min stays strictly below the quality-gate
#     aggregation window (200×30s ≈ 100 min, raised from 130×30s after the
#     PR #248 slow-tail deadlock) — the poll may consume up to half of that
#     window while ci-ok's own 10 needs-jobs finish queueing first — and
#     still covers the observed 8-15 min healthy review with 3x headroom;
#     the early-exit probes normally end dead ends far sooner.

set -euo pipefail

# Input: GitHub event name, repository, commit SHA, GitHub token
EVENT_NAME="${1}"
REPOSITORY="${2}"
COMMIT_SHA="${3}"
GH_TOKEN="${4}"

# Retry configuration — see header for the budget derivation.
MAX_ATTEMPTS=90
WAIT_SECONDS=30

# Early-exit scan cadence: the workflow-run probe runs on attempt 1, then
# every Nth attempt. The check-runs query itself still runs every attempt.
EARLY_EXIT_SCAN_INTERVAL=5

# Stale-head grace: number of probe scans that only observe (without
# judging) a head mismatch before declaring stale-head fatal. One full
# observation cycle absorbs GitHub eventing eventual consistency; a push
# (the only way a head moves) is authoritative in the pulls API.
STALE_HEAD_GRACE_SCANS=1

# For push events (not pull_request), skip gracefully
if [ "$EVENT_NAME" != "pull_request" ]; then
  echo "Not a pull_request event, skipping droid-review check"
  exit 0
fi

# Early-exit with cause judgment (always exit 1 — fail-closed).
die() {
  echo "✗ FAIL: $1"
  exit 1
}

# Dependabot PRs cannot access FACTORY_API_KEY (GitHub secret restriction),
# so droid-review cannot run for them. We check the PR author and allow
# Dependabot PRs to merge when droid-review is skipped/neutral/failed.

# Check if this commit belongs to a Dependabot PR.
# Dependabot PRs cannot access FACTORY_API_KEY (GitHub secret restriction),
# so droid-review cannot run for them.
# Returns 0 (true) if Dependabot, 1 (false) otherwise.
is_dependabot_pr() {
  local pr_info pr_author
  # Fail-closed guard: a transient network/API error while probing the
  # author must NOT silently misjudge it (droid-review P2 finding,
  # 2026-08-29). Treat as non-Dependabot: worst case the merge is blocked
  # and a rerun recovers — a merge is never wrongly allowed.
  pr_info=$(curl -s -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPOSITORY}/commits/${COMMIT_SHA}/pulls") || {
    echo "⚠ transient network error probing PR author, treating as non-Dependabot (fail-closed)"
    return 1
  }
  pr_author=$(echo "$pr_info" | jq -r '.[0].user.login // ""') || {
    echo "⚠ transient API error parsing PR author, treating as non-Dependabot (fail-closed)"
    return 1
  }
  if [ "$pr_author" = "dependabot[bot]" ]; then
    return 0
  fi
  return 1
}

# Current PR head SHA via the commits→pulls association of the checked SHA.
# Prints the head SHA, or empty when the probe fails or the SHA has no PR
# association. Always exits 0 (caller decides conservatively on empty).
current_pr_head() {
  local pr_info
  pr_info=$(curl -s -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPOSITORY}/commits/${COMMIT_SHA}/pulls") || {
    echo ""
    return 0
  }
  echo "$pr_info" | jq -r '.[0].head.sha // ""' || echo ""
}

# Early-exit probe (H1 ④⑤), invoked on attempt 1 and every
# EARLY_EXIT_SCAN_INTERVAL-th attempt. Return codes:
#   0 — keep polling (no verdict; includes transient failures, which the
#       caller retried internally)
#   dies via `die` (exit 1) on a decisive early-exit verdict
workflow_run_probe() {
  local scan_count="$1"
  local runs_body run_case pr_head_now

  # Part A — upstream run outcome for THIS SHA. head_sha-filtered, so every
  # returned run belongs to the checked commit; if any of them is still
  # active, keep waiting (same two-phase discipline as the check-run race
  # guard). Only when ALL runs on this SHA are completed does the latest
  # conclusion become a verdict: a failed/cancelled/neutral terminal state
  # means no check run will ever appear — poll no more.
  runs_body=$(curl -s -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPOSITORY}/actions/runs?head_sha=${COMMIT_SHA}&per_page=100") || return 0
  run_case=$(echo "$runs_body" | jq -r --arg cs "$COMMIT_SHA" '
    [(.workflow_runs // [])[]]
    | map(select(.name == "Droid Auto Review"
                 and .event == "pull_request_target"
                 and .head_sha != null
                 and .head_sha != ""))
    | if length == 0 then "none"
      elif any(.[]; .status != "completed") then "active"
      else
        (sort_by(.run_number) | last)
        | if (.conclusion // "") == "" then "pending"
          else .conclusion
        end
    end
  ') || return 0
  case "$run_case" in
    failure|timed_out|startup_failure|action_required)
      die "early-exit: upstream droid-review run concluded '${run_case}' for SHA ${COMMIT_SHA} (cause: upstream run failed — no check run will appear); aborting poll"
      ;;
    cancelled|stale)
      die "early-exit: upstream droid-review run concluded '${run_case}' for SHA ${COMMIT_SHA} (cause: run cancelled/superseded — rerun droid-review instead of waiting); aborting poll"
      ;;
    neutral)
      die "early-exit: upstream droid-review run concluded 'neutral' (cause: no eligible shard jobs — draft/dependabot gate skipped the pipeline); aborting poll"
      ;;
    *)
      # none (runs not visible yet) / active / pending / success: the check
      # run surface decides; keep polling it.
      :
      ;;
  esac

  # Part B — stale-head judgment (R10 family): the PR head moved away from
  # the checked SHA, so branch protection will never look at a check we are
  # waiting for on this SHA. One observing scan, then judge. A transient
  # probe failure (empty) keeps polling — never a false drift verdict.
  pr_head_now="$(current_pr_head)"
  if [ -n "$pr_head_now" ] && [ "$pr_head_now" != "$COMMIT_SHA" ]; then
    if [ "$scan_count" -le "$STALE_HEAD_GRACE_SCANS" ]; then
      echo "stale-head probe: PR head ${pr_head_now} differs from checked SHA (grace ${scan_count}/${STALE_HEAD_GRACE_SCANS}) — observing"
    else
      die "early-exit: check pinned to a non-PR-head SHA (stale-head) — checked ${COMMIT_SHA}, current PR head ${pr_head_now} (cause: head drifted after trigger, e.g. R10 relock race — a check on this SHA can never gate the merge); aborting poll"
    fi
  fi
  return 0
}

# Poll for droid-review completion
SCAN_COUNT=0
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "Attempt $attempt/$MAX_ATTEMPTS: Querying check runs for commit $COMMIT_SHA in $REPOSITORY..."

  # Query check runs for this commit.
  # Network resilience (droid-review P1 finding, 2026-08-29): a curl-level
  # failure (timeout/DNS/refused) must NOT kill the poller under `set -e` —
  # one transient blip would break the retry loop and turn ci-ok red.
  # Treat as transient and retry.
  CHECKS=$(curl -s -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/${REPOSITORY}/commits/${COMMIT_SHA}/check-runs?check_name=droid-review") \
    || {
      echo "⚠ transient network error querying check runs, retrying in ${WAIT_SECONDS}s..."
      sleep "$WAIT_SECONDS"
      continue
    }

  # Decision logic — two-phase to prevent stale-check race condition.
  #
  # Race scenario (confirmed: run 32435306919/32435603998):
  #   When a PR transitions from draft to ready, a stale concluded check
  #   (e.g., 'skipped' from draft era) may coexist with a brand-new
  #   in_progress review on the same SHA. The old single-phase logic
  #   would immediately read the stale conclusion → ci-ok sees failure
  #   → cancel-on-ci-fail kills the running review → merge deadlock.
  #
  # Fix: Phase 1 checks for any active (non-completed) check runs first.
  # If found, return 'pending' to keep polling. Only when all runs are
  # completed does Phase 2 read the latest non-cancelled conclusion.
  #
  # Dependabot skip exception is unaffected: when no active runs exist,
  # Phase 2 reads the 'skipped' conclusion and the Dependabot check below
  # handles it as before.
  #
  # API-level resilience: a rate-limit/5xx body is valid JSON but has no
  # .check_runs, so jq exits non-zero ("Cannot iterate over null") — under
  # `set -e` that would kill the poller just like a curl failure. Same
  # transient-retry treatment.
  STATUS=$(echo "$CHECKS" | jq -r '
    # Phase 1: active-check guard — any non-completed run means keep waiting
    if (.check_runs | map(select(.status != "completed")) | length > 0) then
      "pending"
    else
      # Phase 2: all runs completed — read latest non-cancelled conclusion
      # "skipped" is NOT excluded (Dependabot path needs it)
      # "cancelled" is excluded (dual-trigger race where one run was cancelled)
      .check_runs
      | map(select(.conclusion != null and .conclusion != "cancelled"))
      | sort_by(.started_at)
      | last
      | .conclusion // "pending"
    end
  ') || {
    echo "⚠ transient API error parsing check runs, retrying in ${WAIT_SECONDS}s..."
    sleep "$WAIT_SECONDS"
    continue
  }

  echo "droid-review conclusion: $STATUS"

  # Early-exit scan (H1 ④⑤): on attempt 1, then every EARLY_EXIT_SCAN_INTERVAL
  # attempts, judge the upstream run / PR head before spending more budget.
  # The check run is read first each turn — the probe only accelerates the
  # dead-end exits, it never overrides a posted check.
  if [ "$(( (attempt - 1) % EARLY_EXIT_SCAN_INTERVAL ))" -eq 0 ]; then
    SCAN_COUNT=$((SCAN_COUNT + 1))
    workflow_run_probe "$SCAN_COUNT"
  fi

  # Decision logic
  if [ "$STATUS" = "success" ]; then
    # C4 head-drift guard (H1 ⑤): success may only be granted while the
    # checked SHA is still the PR head. A success conclusion observed on a
    # stale SHA is NOT a valid review of the current head — downgrade to
    # neutral semantics (merge blocked via the no-valid-review path),
    # never report success for an old SHA.
    PR_HEAD_NOW="$(current_pr_head)"
    if [ -n "$PR_HEAD_NOW" ] && [ "$PR_HEAD_NOW" != "$COMMIT_SHA" ]; then
      echo "○ droid-review reported success for stale head ${COMMIT_SHA} (current PR head is ${PR_HEAD_NOW}) — head-drift guard downgrades to neutral (C4: never success for an old SHA)"
      if is_dependabot_pr; then
        echo "○ PR is from Dependabot — allowing merge despite neutral review"
        exit 0
      fi
      exit 1
    fi
    # Probe failure (empty) does NOT fake a drift judgment: the head cannot
    # move without a push, and a push creates a new run on the new head that
    # the stale-head probe flags; an unverifiable probe here conservatively
    # grants the observed conclusion.
    echo "✓ droid-review passed"
    exit 0
  elif [ "$STATUS" = "neutral" ] || [ "$STATUS" = "skipped" ]; then
    if is_dependabot_pr; then
      echo "○ droid-review was $STATUS but PR is from Dependabot (FACTORY_API_KEY not accessible), allowing merge"
      exit 0
    fi
    echo "✗ BLOCK: droid-review was skipped/neutral — security audit did not run. Merge not allowed."
    exit 1
  elif [ "$STATUS" = "failure" ]; then
    if is_dependabot_pr; then
      echo "○ droid-review failed but PR is from Dependabot (FACTORY_API_KEY not accessible), skipping"
      exit 0
    fi
    echo "✗ FAIL: droid-review failed"
    exit 1
  elif [ "$STATUS" = "pending" ] || [ "$STATUS" = "null" ] || [ -z "$STATUS" ]; then
    if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
      echo "⚠ droid-review still running, waiting ${WAIT_SECONDS}s before retry..."
      sleep "$WAIT_SECONDS"
    else
      echo "⚠ droid-review not complete after $MAX_ATTEMPTS attempts"
      echo "This may indicate:"
      echo "  - droid-review is stuck or taking too long"
      echo "  - droid-review was not triggered (check workflow configuration)"
      echo "  - droid-review check run was not created"
      echo "  - upstream run failed before posting a check (the run-outcome probe normally catches this first)"
      exit 1
    fi
  else
    echo "? Unknown droid-review status: $STATUS"
    exit 1
  fi
done

# Loop exhausted without a decision (e.g. persistent transient errors with
# the guards above continuing past every attempt) — fail closed. Never fall
# through with exit 0: ci-ok would read that as "droid-review passed".
echo "✗ FAIL: exhausted $MAX_ATTEMPTS attempts — droid-review never completed"
exit 1
