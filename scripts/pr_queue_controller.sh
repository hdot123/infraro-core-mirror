#!/usr/bin/env bash
# PR queue controller — at most one PR holds `processing` (pipeline-maintained).
#
# Normal path: zero human label edits — reconcile assigns, merges, waits main push,
# releases, then assigns next. queue-jump is emergency-only; manual label tear is break-glass.
#
# Spec: docs/guides/pr-queue-processing.md
# Invoked from .github/workflows/pr-queue-controller.yml (default-branch code only).
set -euo pipefail

REPOSITORY="${1:?repository owner/name}"
GH_TOKEN="${2:?github token (labels, checks, comments)}"
GH_MERGE_TOKEN="${3:?DISPATCH_TOKEN PAT for gh pr merge — GITHUB_TOKEN suppresses push workflows on main}"

export GH_TOKEN
export GH_REPO="$REPOSITORY"

PROCESSING_LABEL="processing"
QUEUE_JUMP_LABEL="queue-jump"
DO_NOT_MERGE="do-not-merge"
MERGE_FAIL_SENTINEL_PREFIX="<!-- pr-queue-merge-failed-"

# PR head / ruleset dequeue surface (see docs/guides/fullstack-test-plan.md).
REQUIRED_CHECKS=(substrate-gate-suite quality-gate droid-review)
# After squash merge: hold slot until main@merge_commit push CI (pve) is green.
MAIN_PUSH_REQUIRED_CHECKS=(substrate-gate-suite quality-gate)

# stderr only — stdout is reserved for machine-readable captures ($(...), mapfile, jq).
log() { echo "[pr-queue] $*" >&2; }

declare -gA _CHECKS_RC_CACHE

has_label() {
  local labels_json="$1"
  local name="$2"
  echo "$labels_json" | jq -e --arg n "$name" 'any(.[]; .name == $n)' >/dev/null 2>&1
}

# May this PR ever sit in the queue at all? (draft / fork / do-not-merge)
pr_eligible() {
  local pr_json="$1"
  local draft cross
  draft=$(echo "$pr_json" | jq -r '.isDraft')
  cross=$(echo "$pr_json" | jq -r '.isCrossRepository')
  if [ "$draft" = "true" ]; then
    return 1
  fi
  if [ "$cross" = "true" ]; then
    return 1
  fi
  if has_label "$(echo "$pr_json" | jq -c '.labels')" "$DO_NOT_MERGE"; then
    return 1
  fi
  return 0
}

pr_head_sha() {
  local pr_json="$1"
  echo "$pr_json" | jq -r '.headRefOid // empty'
}

pr_author_login() {
  local pr_json="$1"
  echo "$pr_json" | jq -r '
    if (.author | type) == "string" then .author
    else .author.login // .user.login // ""
    end
  '
}

latest_check_conclusion() {
  local sha="$1"
  local check_name="$2"
  local runs_file conclusion
  runs_file=$(mktemp)
  if ! gh api -H "Accept: application/vnd.github+json" \
    "/repos/${REPOSITORY}/commits/${sha}/check-runs" --paginate > "$runs_file"; then
    rm -f "$runs_file"
    return 2
  fi
  conclusion=$(jq -s -r --arg n "$check_name" '
    [.[].check_runs // [] | .[]]
    | map(select(.name == $n)) as $runs
    | if ($runs | map(select(.status != "completed")) | length) > 0 then
        "pending"
      else
        ($runs | map(select(.conclusion != null and .conclusion != "cancelled"))
         | sort_by(.started_at) | last | .conclusion // "missing")
      end
  ' "$runs_file")
  rm -f "$runs_file"
  echo "$conclusion"
}

is_dependabot_author() {
  local login="$1"
  [ "$login" = "dependabot[bot]" ]
}

# Aggregate check-runs for named checks on one commit (names passed as remaining args).
# Returns: 0 all success, 1 pending/missing, 2 terminal failure.
checks_list_rc_for_names() {
  local sha="$1"
  local author_login="$2"
  shift 2
  local name conclusion pending=0 failed=0 check_api_rc

  for name in "$@"; do
    if [ "$name" = "droid-review" ] && is_dependabot_author "$author_login"; then
      log "dependabot PR — skip droid-review requirement"
      continue
    fi
    set +e
    conclusion=$(latest_check_conclusion "$sha" "$name")
    check_api_rc=$?
    if [ "$check_api_rc" -eq 2 ]; then
      log "check $name: check-runs API unavailable — treat as pending"
      pending=1
      continue
    fi
    case "$conclusion" in
      success) log "check $name: success" ;;
      missing|pending|skipped|"")
        log "check $name: not ready ($conclusion)"
        pending=1
        ;;
      failure|timed_out|action_required|neutral|stale)
        log "check $name: terminal non-success ($conclusion)"
        failed=1
        ;;
      *)
        log "check $name: unknown ($conclusion) — treat as pending"
        pending=1
        ;;
    esac
  done

  if [ "$failed" -eq 1 ]; then
    set +e
    return 2
  fi
  if [ "$pending" -eq 1 ]; then
    set +e
    return 1
  fi
  set +e
  return 0
}

# required_checks_rc — PR head (pre-merge) required checks.
required_checks_rc() {
  local sha="$1" author="$2" key rc
  key="req|${sha}|${author}"
  if [[ -n "${_CHECKS_RC_CACHE[$key]+x}" ]]; then
    return "${_CHECKS_RC_CACHE[$key]}"
  fi
  set +e
  checks_list_rc_for_names "$sha" "$author" "${REQUIRED_CHECKS[@]}"
  rc=$?
  _CHECKS_RC_CACHE[$key]=$rc
  return "$rc"
}

# main_push_checks_rc — post-merge main push pve surface (no droid-review on push).
main_push_checks_rc() {
  local sha="$1" key rc
  key="main|${sha}"
  if [[ -n "${_CHECKS_RC_CACHE[$key]+x}" ]]; then
    return "${_CHECKS_RC_CACHE[$key]}"
  fi
  set +e
  checks_list_rc_for_names "$sha" "" "${MAIN_PUSH_REQUIRED_CHECKS[@]}"
  rc=$?
  _CHECKS_RC_CACHE[$key]=$rc
  return "$rc"
}

# Should we assign `processing`? pr_eligible and head not all-green (must NOT receive
# new processing when required_checks_rc == 0 — prevents #358 relabel).
candidate_for_processing_label() {
  local pr_json="$1"
  local sha author rc

  if ! pr_eligible "$pr_json"; then
    return 1
  fi
  sha=$(pr_head_sha "$pr_json")
  author=$(pr_author_login "$pr_json")
  if [ -z "$sha" ]; then
    return 1
  fi
  set +e
  required_checks_rc "$sha" "$author"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    log "head ${sha:0:7} already all required checks green — skip processing assignment"
    return 1
  fi
  return 0
}

remove_processing() {
  local pr_number="$1"
  gh pr edit "$pr_number" --remove-label "$PROCESSING_LABEL" 2>/dev/null || true
}

add_processing() {
  local pr_number="$1"
  gh pr edit "$pr_number" --add-label "$PROCESSING_LABEL"
}

remove_queue_jump() {
  local pr_number="$1"
  gh pr edit "$pr_number" --remove-label "$QUEUE_JUMP_LABEL" 2>/dev/null || true
}

has_queue_jump() {
  has_label "$(echo "$1" | jq -c '.labels')" "$QUEUE_JUMP_LABEL"
}

assign_processing_to_pr() {
  local pr_number="$1"
  local pr_json="$2"
  if [ "$(processing_holder_count)" -gt 0 ]; then
    log "PR #$pr_number — skip assign: processing slot occupied (queue-jump cannot preempt)"
    return 0
  fi
  add_processing "$pr_number"
  if has_queue_jump "$pr_json"; then
    log "PR #$pr_number consumed $QUEUE_JUMP_LABEL (assigned processing)"
    remove_queue_jump "$pr_number"
  fi
}

processing_holder_count() {
  gh pr list --repo "$REPOSITORY" --label "$PROCESSING_LABEL" --state all --json number \
    --jq 'length' 2>/dev/null || echo 0
}

# Among duplicate processing labels, keep the active queue holder (not merely oldest PR).
choose_processing_holder_to_keep() {
  local num state pr_json sha author rc pri best="" best_pri=-1
  for num in "${PROCESSING_CANDIDATES[@]}"; do
    pri=0
    state=$(gh pr view "$num" --json state --jq '.state' 2>/dev/null || echo "")
    case "$state" in
      MERGED) pri=100 ;;
      OPEN)
        pr_json=$(gh pr view "$num" --json headRefOid,author,isDraft,isCrossRepository,labels)
        if ! pr_eligible "$pr_json"; then
          pri=1
        else
          sha=$(pr_head_sha "$pr_json")
          author=$(pr_author_login "$pr_json")
          set +e
          required_checks_rc "$sha" "$author"
          rc=$?
          set -e
          case "$rc" in
            0) pri=95 ;;
            1) pri=80 ;;
            2) pri=70 ;;
            *) pri=10 ;;
          esac
        fi
        ;;
      *) pri=0 ;;
    esac
    if [ -z "$best" ] || [ "$pri" -gt "$best_pri" ] || { [ "$pri" -eq "$best_pri" ] && [ "$num" -lt "$best" ]; }; then
      best_pri=$pri
      best="$num"
    fi
  done
  echo "$best"
}

merge_failure_comment_exists() {
  local pr_number="$1"
  local head_sha="$2"
  local sentinel="${MERGE_FAIL_SENTINEL_PREFIX}${head_sha} -->"
  gh api "/repos/${REPOSITORY}/issues/${pr_number}/comments" --paginate \
    --jq '.[].body' 2>/dev/null | grep -qF "$sentinel"
}

post_merge_failure_comment() {
  local pr_number="$1"
  local head_sha="$2"
  local reason="$3"
  local sentinel="${MERGE_FAIL_SENTINEL_PREFIX}${head_sha} -->"
  local body
  if merge_failure_comment_exists "$pr_number" "$head_sha"; then
    log "merge failure comment already on PR #$pr_number for ${head_sha:0:7} — skip duplicate"
    return 0
  fi
  body=$(cat <<EOF
${sentinel}
**PR 队列**：三门 required check 已全绿，但自动 squash 合入失败；\`processing\` **仍保留**（队列冻住直至修复）。

原因：${reason}

请处理（例如 resolve review conversation、解决冲突）后重新 push 或修复合入条件。
EOF
)
  gh pr comment "$pr_number" --body "$body" 2>/dev/null || true
}

# attempt_squash_merge — merge when PR head required checks are green.
# Returns 0 if merged (or already merged); 1 if merge skipped or failed.
attempt_squash_merge() {
  local pr_number="$1"
  local pr_json sha author state mergeable merge_state checks_rc merge_err err_text current_state
  pr_json=$(gh pr view "$pr_number" --json number,state,headRefOid,isDraft,isCrossRepository,mergeable,mergeStateStatus,labels,author)
  state=$(echo "$pr_json" | jq -r '.state')
  if [ "$state" = "MERGED" ]; then
    log "PR #$pr_number already merged"
    return 0
  fi
  if [ "$state" != "OPEN" ]; then
    log "PR #$pr_number not OPEN ($state) — skip merge"
    return 1
  fi
  if ! pr_eligible "$pr_json"; then
    log "PR #$pr_number ineligible for merge — skip merge"
    return 1
  fi
  sha=$(echo "$pr_json" | jq -r '.headRefOid')
  author=$(pr_author_login "$pr_json")
  set +e
  required_checks_rc "$sha" "$author"
  checks_rc=$?
  set -e
  if [ "$checks_rc" -ne 0 ]; then
    log "PR #$pr_number required checks not all green — skip merge"
    return 1
  fi

  mergeable=$(echo "$pr_json" | jq -r '.mergeable')
  merge_state=$(echo "$pr_json" | jq -r '.mergeStateStatus // ""')
  if [ "$mergeable" = "UNKNOWN" ]; then
    sleep 5
    mergeable=$(gh pr view "$pr_number" --json mergeable --jq '.mergeable' 2>/dev/null || echo "UNKNOWN")
  fi
  if [ "$mergeable" != "MERGEABLE" ]; then
    log "PR #$pr_number not MERGEABLE ($mergeable / $merge_state)"
    post_merge_failure_comment "$pr_number" "$sha" "GitHub 报告不可合并（mergeable=${mergeable}, mergeStateStatus=${merge_state}）。"
    return 1
  fi

  log "PR #$pr_number head ${sha:0:7} — attempting squash merge (DISPATCH_TOKEN PAT)"
  merge_err=$(mktemp)
  if GH_TOKEN="$GH_MERGE_TOKEN" gh pr merge "$pr_number" --squash --delete-branch 2>"$merge_err"; then
    rm -f "$merge_err"
    log "PR #$pr_number merged successfully"
    return 0
  fi
  err_text=$(cat "$merge_err" 2>/dev/null || true)
  rm -f "$merge_err"
  current_state=$(gh pr view "$pr_number" --json state --jq '.state' 2>/dev/null || echo "UNKNOWN")
  if [ "$current_state" = "MERGED" ]; then
    log "PR #$pr_number already merged (race)"
    return 0
  fi
  log "PR #$pr_number merge failed: $err_text"
  post_merge_failure_comment "$pr_number" "$sha" "${err_text:-merge API 失败（见 Actions 日志）}"
  return 1
}

release_processing_slot() {
  local pr_number="$1"
  remove_processing "$pr_number"
  remove_queue_jump "$pr_number"
  log "released processing slot (PR #$pr_number)"
}

# Hold slot until merge commit main push checks pass (substrate-gate-suite + quality-gate).
wait_main_push_then_maybe_release() {
  local pr_number="$1"
  local pr_json="${2:-}"
  local merge_commit rc state

  if [ -z "$pr_json" ]; then
    pr_json=$(gh pr view "$pr_number" --json number,state,mergeCommit,headRefOid,labels)
  fi
  state=$(echo "$pr_json" | jq -r '.state')
  merge_commit=$(echo "$pr_json" | jq -r '.mergeCommit.oid // empty')

  if [ "$state" != "MERGED" ]; then
    log "PR #$pr_number not MERGED ($state) — cannot wait on main push"
    return 0
  fi
  if [ -z "$merge_commit" ]; then
    log "PR #$pr_number merged but merge commit not visible — keep processing"
    QUEUE_HEAD_NUM="$pr_number"
    return 0
  fi

  set +e
  main_push_checks_rc "$merge_commit"
  rc=$?
  set -e

  case "$rc" in
    0)
      log "main push checks green on merge commit ${merge_commit:0:7} — release slot"
      release_processing_slot "$pr_number"
      ;;
    2)
      log "main push checks failed on ${merge_commit:0:7} — keep processing (queue frozen)"
      QUEUE_HEAD_NUM="$pr_number"
      ;;
    *)
      log "waiting for main push CI on merge commit ${merge_commit:0:7} — keep processing"
      QUEUE_HEAD_NUM="$pr_number"
      ;;
  esac
}

# PR head all-green: merge (if needed), then wait for main push before releasing slot.
advance_all_green_queue_head() {
  local pr_number="$1"
  local pr_json merge_rc

  pr_json=$(gh pr view "$pr_number" --json number,state,mergeCommit,headRefOid,author,isDraft,isCrossRepository,labels)
  if [ "$(echo "$pr_json" | jq -r '.state')" = "MERGED" ]; then
    wait_main_push_then_maybe_release "$pr_number" "$pr_json"
    return 0
  fi

  set +e
  attempt_squash_merge "$pr_number"
  merge_rc=$?
  set -e
  if [ "$merge_rc" -ne 0 ]; then
    log "auto-merge failed for PR #$pr_number — keep processing (queue frozen)"
    QUEUE_HEAD_NUM="$pr_number"
    return 0
  fi

  pr_json=$(gh pr view "$pr_number" --json number,state,mergeCommit,headRefOid,labels)
  wait_main_push_then_maybe_release "$pr_number" "$pr_json"
}

fetch_open_prs() {
  gh api "/repos/${REPOSITORY}/pulls?state=open&sort=created&direction=asc&per_page=100" \
    --jq '.[] | {
      number,
      created_at,
      headRefOid: .head.sha,
      isDraft: .draft,
      isCrossRepository: (.head.repo.full_name != .base.repo.full_name),
      author: .user.login,
      labels: [.labels[] | {name: .name}]
    }'
}

# PRs carrying `processing` (open or merged — merged may still hold slot during main push wait).
fetch_processing_labeled_prs() {
  gh pr list --repo "$REPOSITORY" --label "$PROCESSING_LABEL" --state all --limit 50 \
    --json number,state,createdAt,labels \
    --jq 'sort_by(.createdAt) | .[] | {
      number,
      created_at: .createdAt,
      state,
      labels: [.labels[] | {name: .name}]
    }'
}

# PR_QUEUE_RECONCILE_PHASE: auto-release — MERGED + main push green → strip processing (no human).
reconcile_phase_auto_release_merged_holders() {
  local line num state pr_json merge_commit rc
  for line in "${PROCESSING_LINES[@]}"; do
    state=$(echo "$line" | jq -r '.state')
    [ "$state" != "MERGED" ] && continue
    num=$(echo "$line" | jq -r '.number')
    pr_json=$(gh pr view "$num" --json state,mergeCommit)
    merge_commit=$(echo "$pr_json" | jq -r '.mergeCommit.oid // empty')
    if [ -z "$merge_commit" ]; then
      continue
    fi
    set +e
    main_push_checks_rc "$merge_commit"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      log "MERGED PR #$num main push green — auto-release processing (pipeline)"
      release_processing_slot "$num"
    fi
  done
}

# PR_QUEUE_RECONCILE_PHASE: sanitize — drop processing on ineligible open PRs; dedupe extras.
reconcile_phase_sanitize_labels() {
  local line num state
  mapfile -t PROCESSING_LINES < <(fetch_processing_labeled_prs | jq -c '.')
  reconcile_phase_auto_release_merged_holders
  mapfile -t PROCESSING_LINES < <(fetch_processing_labeled_prs | jq -c '.')
  PROCESSING_CANDIDATES=()

  for line in "${PROCESSING_LINES[@]}"; do
    num=$(echo "$line" | jq -r '.number')
    state=$(echo "$line" | jq -r '.state')
    case "$state" in
      OPEN)
        pr_json=$(gh pr view "$num" --json isDraft,isCrossRepository,labels)
        if pr_eligible "$pr_json"; then
          PROCESSING_CANDIDATES+=("$num")
        else
          log "open PR #$num ineligible but has processing — removing label"
          remove_processing "$num"
        fi
        ;;
      MERGED)
        PROCESSING_CANDIDATES+=("$num")
        ;;
      *)
        log "PR #$num state=$state with processing — removing label"
        remove_processing "$num"
        ;;
    esac
  done

  if [ "${#PROCESSING_CANDIDATES[@]}" -gt 1 ]; then
    local keep
    keep=$(choose_processing_holder_to_keep)
    log "multiple processing labels — keep active holder PR #$keep, strip duplicates (never preempt via queue-jump)"
    for num in "${PROCESSING_CANDIDATES[@]}"; do
      if [ "$num" != "$keep" ]; then
        remove_processing "$num"
      fi
    done
    PROCESSING_CANDIDATES=("$keep")
  fi
}

# PR_QUEUE_RECONCILE_PHASE: expire queue-jump on stale open PRs.
reconcile_phase_expire_queue_jumps() {
  local line num labels_json sha author rc
  for line in "${PR_LINES[@]}"; do
    labels_json=$(echo "$line" | jq -c '.labels')
    if ! has_label "$labels_json" "$QUEUE_JUMP_LABEL"; then
      continue
    fi
    num=$(echo "$line" | jq -r '.number')
    if ! pr_eligible "$line"; then
      log "PR #$num ineligible — remove $QUEUE_JUMP_LABEL"
      remove_queue_jump "$num"
      continue
    fi
    sha=$(pr_head_sha "$line")
    author=$(pr_author_login "$line")
    if [ -z "$sha" ]; then
      continue
    fi
    set +e
    required_checks_rc "$sha" "$author"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      log "PR #$num head all-green — expire $QUEUE_JUMP_LABEL"
      remove_queue_jump "$num"
    fi
  done
}

# PR_QUEUE_RECONCILE_PHASE: queue-head — merge / wait main push / freeze.
reconcile_phase_queue_head() {
  local active_pr="" pr_json state sha author rc
  QUEUE_HEAD_NUM=""

  if [ "${#PROCESSING_CANDIDATES[@]}" -eq 1 ]; then
    active_pr="${PROCESSING_CANDIDATES[0]}"
  fi
  if [ -z "$active_pr" ]; then
    return 0
  fi

  pr_json=$(gh pr view "$active_pr" --json number,headRefOid,author,isDraft,isCrossRepository,labels,state,mergeCommit)
  state=$(echo "$pr_json" | jq -r '.state')

  if [ "$state" = "MERGED" ]; then
    log "queue head PR #$active_pr merged — wait for main push CI before releasing slot"
    wait_main_push_then_maybe_release "$active_pr" "$pr_json"
    return 0
  fi

  if [ "$state" != "OPEN" ]; then
    log "queue head PR #$active_pr not open ($state) — remove processing"
    remove_processing "$active_pr"
    return 0
  fi
  if ! pr_eligible "$pr_json"; then
    log "queue head PR #$active_pr no longer eligible — remove processing"
    remove_processing "$active_pr"
    return 0
  fi

  sha=$(pr_head_sha "$pr_json")
  author=$(pr_author_login "$pr_json")
  set +e
  required_checks_rc "$sha" "$author"
  rc=$?
  set -e

  case "$rc" in
    0)
      log "queue head PR #$active_pr head ${sha:0:7} all required checks green — merge + wait main push"
      advance_all_green_queue_head "$active_pr"
      ;;
    2)
      log "queue head PR #$active_pr has failing required checks — keep processing (queue frozen)"
      QUEUE_HEAD_NUM="$active_pr"
      ;;
    *)
      log "queue head PR #$active_pr checks not complete — keep processing"
      QUEUE_HEAD_NUM="$active_pr"
      ;;
  esac
}

# No preemption — only called when no PR holds processing.
pick_next_processing_candidate() {
  local line
  for line in "${PR_LINES[@]}"; do
    if has_queue_jump "$line"; then
      if candidate_for_processing_label "$line"; then
        echo "$line"
        return 0
      fi
    fi
  done
  for line in "${PR_LINES[@]}"; do
    if candidate_for_processing_label "$line"; then
      echo "$line"
      return 0
    fi
  done
  return 1
}

# PR_QUEUE_RECONCILE_PHASE: assign — queue-jump pass, then FIFO.
reconcile_phase_assign_processing() {
  local line num holders
  holders=$(processing_holder_count)
  if [ "${holders:-0}" -gt 0 ]; then
    log "processing slot occupied ($holders holder(s)) — skip assign (queue-jump does not preempt)"
    return 0
  fi
  if ! line=$(pick_next_processing_candidate); then
    log "no eligible PR for processing"
    return 0
  fi
  num=$(echo "$line" | jq -r '.number')
  if has_queue_jump "$line"; then
    log "assign processing to PR #$num (queue-jump priority)"
  else
    log "assign processing to PR #$num (FIFO queue head)"
  fi
  assign_processing_to_pr "$num" "$line"
}

main() {
  log "reconcile start for $REPOSITORY"

  mapfile -t PR_LINES < <(fetch_open_prs | jq -c '.')

  reconcile_phase_sanitize_labels

  if [ "${#PR_LINES[@]}" -eq 0 ] && [ "${#PROCESSING_CANDIDATES[@]}" -eq 0 ]; then
    log "no open PRs and no processing holder"
    exit 0
  fi

  if [ "${#PR_LINES[@]}" -gt 0 ]; then
    reconcile_phase_expire_queue_jumps
  fi
  reconcile_phase_queue_head

  if [ -n "${QUEUE_HEAD_NUM:-}" ]; then
    log "queue head PR #${QUEUE_HEAD_NUM} — done"
    exit 0
  fi

  if [ "${#PR_LINES[@]}" -eq 0 ]; then
    log "no open PRs for assign"
    exit 0
  fi

  reconcile_phase_assign_processing
  exit 0
}

main "$@"
