#!/usr/bin/env bash
# Cheap-gates preflight for Droid Auto Review (CI + QA success before LLM shards).
#
# Usage: check_cheap_gates_green.sh <repository> <head_sha> <pr_number>
#
# Writes GitHub Actions step outputs (append to GITHUB_OUTPUT when ready):
#   ready=true|false
#   pr_number=...
#   head_sha=...
#
# Exit codes:
#   0 — evaluation complete (ready may be true or false)
#   1 — usage / API hard failure

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <repository> <head_sha> <pr_number>"
  exit 1
fi

REPOSITORY="$1"
HEAD_SHA="$2"
PR_NUMBER="$3"

if [[ -z "${GITHUB_OUTPUT:-}" ]]; then
  echo "GITHUB_OUTPUT must be set (Actions step context)"
  exit 1
fi

latest_check_state() {
  # Returns JSON {status,conclusion}. Exit 0 on parse OK; exit 2 on transient
  # gh/jq failure (caller must retry — never treat flake as gate failure).
  local check_name="$1"
  local payload
  if ! payload="$(gh api "repos/${REPOSITORY}/commits/${HEAD_SHA}/check-runs" --paginate 2>/dev/null)"; then
    echo "⚠ transient gh api error querying ${check_name}" >&2
    return 2
  fi
  if ! echo "$payload" | jq -s -r --arg n "$check_name" '
    map(.check_runs // [])
    | flatten
    | map(select(.name == $n))
    | sort_by(.id)
    | last
    | if . == null then {status: "missing", conclusion: "missing"}
      else {status: .status, conclusion: (.conclusion // "-")}
      end
  '; then
    echo "⚠ transient jq error parsing ${check_name}" >&2
    return 2
  fi
}

eval_gate() {
  # rc 0 = success; 1 = completed non-success; 2 = missing/in-flight/transient
  local check_name="$1"
  local state
  if ! state="$(latest_check_state "$check_name")"; then
    echo "⏳ ${check_name}: transient API error — will retry"
    return 2
  fi
  local status conclusion
  status=$(echo "$state" | jq -r '.status')
  conclusion=$(echo "$state" | jq -r '.conclusion')

  if [[ "$status" == "missing" ]]; then
    echo "⏳ ${check_name}: no check-run yet"
    return 2
  fi
  if [[ "$status" != "completed" ]]; then
    echo "⏳ ${check_name}: in-flight (${status})"
    return 2
  fi
  if [[ "$conclusion" == "success" ]]; then
    echo "✅ ${check_name}: success"
    return 0
  fi
  echo "⊘ ${check_name}: ${conclusion} (not starting Droid)"
  return 1
}

echo "Cheap-gates preflight for PR #${PR_NUMBER} @ ${HEAD_SHA}"

# pull_request bootstrap fires before CI/QA finish — poll while gates are
# missing/in-flight. workflow_run path keeps default 1 attempt (gates already
# completed when that event fires). Hard failure (rc=1) still exits immediately.
MAX_ATTEMPTS="${CHEAP_GATES_POLL_ATTEMPTS:-1}"
WAIT_SECONDS="${CHEAP_GATES_POLL_SECONDS:-30}"
if [[ ! "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::CHEAP_GATES_POLL_ATTEMPTS must be a positive integer (got: ${MAX_ATTEMPTS})"
  exit 1
fi
if [[ ! "$WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::CHEAP_GATES_POLL_SECONDS must be a positive integer (got: ${WAIT_SECONDS})"
  exit 1
fi

attempt=1
while true; do
  rc_ci=0
  rc_qa=0
  eval_gate "ci-ok" || rc_ci=$?
  eval_gate "qa-ok" || rc_qa=$?

  if [[ "$rc_ci" -eq 0 && "$rc_qa" -eq 0 ]]; then
    {
      echo "pr_number=${PR_NUMBER}"
      echo "head_sha=${HEAD_SHA}"
      echo "ready=true"
    } >>"$GITHUB_OUTPUT"
    echo "✅ ci-ok and qa-ok are green — Droid review may start"
    exit 0
  fi

  # rc=1 is a definitive completed non-success. rc=2 is pending/transient — poll.
  if [[ "$rc_ci" -eq 1 || "$rc_qa" -eq 1 ]]; then
    {
      echo "pr_number=${PR_NUMBER}"
      echo "head_sha=${HEAD_SHA}"
      echo "ready=false"
    } >>"$GITHUB_OUTPUT"
    echo "Deferred: cheap gate failed or skipped — Droid not scheduled"
    exit 0
  fi

  if [[ "$attempt" -ge "$MAX_ATTEMPTS" ]]; then
    break
  fi
  echo "Cheap gates still pending/transient (attempt ${attempt}/${MAX_ATTEMPTS}); sleeping ${WAIT_SECONDS}s..."
  sleep "$WAIT_SECONDS"
  attempt=$((attempt + 1))
done

{
  echo "pr_number=${PR_NUMBER}"
  echo "head_sha=${HEAD_SHA}"
  echo "ready=false"
} >>"$GITHUB_OUTPUT"
echo "Deferred: waiting for cheap gates to complete (exhausted ${MAX_ATTEMPTS} attempts)"
exit 0
