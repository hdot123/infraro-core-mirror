#!/usr/bin/env bash
# Auto-merge triage script (INFRA-416 / INFRA-428)
# Classifies PRs by mergeable state, mergeStateStatus and statusCheckRollup.
#
# Input:  JSON array of PRs via stdin (gh pr view/list --json
#         number,mergeable,mergeStateStatus,isDraft,statusCheckRollup,labels)
# Output: JSON with 7 categories:
#           mergeable   (action: merge)
#           behind      (action: update-branch)
#           conflicting (action: notify)
#           pending     (action: wait)
#           stalled     (action: wait)
#           unknown     (action: skip)
#           do_not_merge (action: skip; do-not-merge label escape hatch)
# Exit:   always 0 (triage never fails workflow)
#
# Modes:
#   default            classify stdin, emit full JSON
#   --get-action N     classify stdin, emit the single action for PR number N
#                      (merge|update-branch|notify|wait|skip; exit 0; empty or
#                      invalid input also emits skip — triage must never fail
#                      the workflow)
#   --get-category N   classify stdin, emit the single category for PR number N
#                      (mergeable|behind|conflicting|pending|stalled|unknown|
#                      do_not_merge; missing PR -> unknown). Workflow uses this
#                      to derive auxiliary outputs (e.g. stalled flag) without
#                      duplicating classification logic.
#
# Classification precedence (one PR falls into exactly one category,
# decided by classify_pr in a single pass — categories are exclusive by
# construction, no cross-select races):
#   labels contains do-not-merge -> do_not_merge (skip; escape hatch, #314 根因)
#   mergeable=UNKNOWN    -> unknown     (skip; GitHub still computing)
#   mergeable=CONFLICTING-> conflicting (notify)
#   mergeStateStatus=BEHIND -> behind   (update-branch)
#   isDraft / DRAFT / BLOCKED -> pending (wait)
#   rollup empty or has incomplete checks -> pending (wait; early-fire guard)
#   rollup all reported, some not SUCCESS -> stalled (wait + escalate)
#   otherwise (MERGEABLE + green rollup)  -> mergeable (merge)
#
# Why these categories exist (INFRA-428):
# - BLOCKED/DRAFT previously fell into "mergeable" → every 10-min sweep fired
#   a merge attempt at an un-mergeable PR → red leg + the shared action's own
#   failure check run poisoned the head SHA (2026-08-18 self-poisoning
#   deadlock, PR #779 mechanism). wait = benign skip, schedule re-sweeps.
# - early-fire: pull_request_target(opened) / workflow_run of one workflow
#   can arrive before all required checks reported. mergeStateStatus alone is
#   async/cached and can be stale-CLEAN in that window; cross-checking
#   statusCheckRollup makes merge fire only when every check run reports
#   conclusion=SUCCESS. Rollup empty = no checks reported yet → fail-closed
#   wait (CI runs on every PR without paths filter, so an empty rollup is
#   always "checks not reported yet", never "no checks configured").
# - CONFLICTING wins over BEHIND: a conflicting PR must never be
#   update-branched into a blind merge.
# - do-not-merge label wins over everything (escape hatch, #314 事故根因修复):
#   label 是机器可读的「仅开 PR，不合并」信号，hit 即全腿静默（不 merge、
#   不 update-branch、不 notify 评论）——最高优先级；移除 label 即恢复候选。
#   修改前缺这层信号：全绿 sweep 直接卷走在飞 PR（#314 实锤）。

set -euo pipefail

GET_ACTION_PR=""
GET_CATEGORY_PR=""

MODE="default"
if [[ "${1:-}" == "--get-action" || "${1:-}" == "--get-category" ]]; then
  MODE="$1"
  if [[ $# -lt 2 ]]; then
    echo "Usage: $0 [--get-action PR_NUMBER | --get-category PR_NUMBER]" >&2
    echo '{"mergeable":[],"behind":[],"conflicting":[],"pending":[],"stalled":[],"unknown":[]}'
    exit 0
  fi
  if [[ "$MODE" == "--get-action" ]]; then
    GET_ACTION_PR="$2"
  else
    GET_CATEGORY_PR="$2"
  fi
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--get-action PR_NUMBER | --get-category PR_NUMBER]" >&2
  echo '{"mergeable":[],"behind":[],"conflicting":[],"pending":[],"stalled":[],"unknown":[]}'
  exit 0
fi

EMPTY_RESULT='{"mergeable":[],"behind":[],"conflicting":[],"pending":[],"stalled":[],"unknown":[],"do_not_merge":[]}'

# Classify PRs and add action fields.
# `jq -e` would swallow the classification on unexpected shapes; plain jq
# with a fallback keeps triage total (empty result on any malformed input).
classify() {
  jq '
    def rollup(pr): (pr.statusCheckRollup // []);
    # green = at least one SUCCESS (防假绿下限) AND all checks are SUCCESS/SKIPPED/NEUTRAL.
    # Three-layer semantics:
    #   1. SKIPPED/NEUTRAL = branch protection alignment (non-blocking checks)
    #   2. any(SUCCESS) = anti-fake-green lower bound (all-SKIPPED rollup requires human review)
    #   3. null conclusion = still queued/in-progress (not counted as reported)
    # Failure conclusions: FAILURE, TIMED_OUT, CANCELLED, ACTION_REQUIRED.
    # Rationale (INFRA-428 + scrutiny R2 + scrutiny R1 blocking): qa.yml #830 added two
    # nightly jobs (Coverage Audit / Full Regression) that report SKIPPED on PR events.
    # PR #862 fixed the deadlock by accepting SKIPPED/NEUTRAL as non-failures. However,
    # the all() check without any(SUCCESS) allowed all-SKIPPED rollups to pass as mergeable,
    # creating a fake-green hole (reviewer-verified). The any(SUCCESS) guard ensures at
    # least one real success exists before allowing merge.
    def checks_green(pr):
      (rollup(pr) | length) > 0
      and (any(rollup(pr)[]; .conclusion == "SUCCESS"))
      and all(rollup(pr)[]; .conclusion == "SUCCESS" or .conclusion == "SKIPPED" or .conclusion == "NEUTRAL");
    # complete = every check run has reported a non-null conclusion.
    # has("conclusion") alone is not enough: the rollup of a queued/
    # in_progress check contains "conclusion": null, and jq has() would
    # treat it as reported. A reported check has a conclusion STRING.
    def checks_complete(pr):
      (rollup(pr) | length) > 0
      and all(rollup(pr)[]; (.conclusion // "") | type == "string" and length > 0);

    # do-not-merge 逃生舱（#314 事故根因修复）：labels 含 do-not-merge（大小写
    # 不敏感）即 do_not_merge。labels 缺失/为空/形状异常 → // [] 兜底为不命中，
    # 旧取数形状（无 labels 字段）行为完全不变。
    def has_do_not_merge(pr):
      any((pr.labels // [])[]; ((.name // "") | ascii_downcase) == "do-not-merge");

    def classify_pr(pr):
      if has_do_not_merge(pr) then "do_not_merge"
      elif pr.mergeable == "UNKNOWN" then "unknown"
      elif pr.mergeable == "CONFLICTING" then "conflicting"
      elif pr.mergeStateStatus == "BEHIND" then "behind"
      elif (pr.isDraft // false) or pr.mergeStateStatus == "DRAFT" or pr.mergeStateStatus == "BLOCKED" then "pending"
      elif (rollup(pr) | length) == 0 then "pending"
      elif checks_green(pr) then "mergeable"
      elif checks_complete(pr) then "stalled"
      else "pending"
      end;

    def action_for(cat):
      if cat == "mergeable" then "merge"
      elif cat == "behind" then "update-branch"
      elif cat == "conflicting" then "notify"
      elif cat == "do_not_merge" then "skip"
      elif cat == "pending" then "wait"
      elif cat == "stalled" then "wait"
      else "skip"
      end;

    map(. as $pr | {pr: $pr, cat: classify_pr($pr)})
    | {
        mergeable: [.[] | select(.cat == "mergeable") | .pr + {action: action_for(.cat)}],
        behind: [.[] | select(.cat == "behind") | .pr + {action: action_for(.cat)}],
        conflicting: [.[] | select(.cat == "conflicting") | .pr + {action: action_for(.cat)}],
        pending: [.[] | select(.cat == "pending") | .pr + {action: action_for(.cat)}],
        stalled: [.[] | select(.cat == "stalled") | .pr + {action: action_for(.cat)}],
        unknown: [.[] | select(.cat == "unknown") | .pr + {action: action_for(.cat)}],
        do_not_merge: [.[] | select(.cat == "do_not_merge") | .pr + {action: action_for(.cat)}]
      }
  ' 2>/dev/null || echo "$EMPTY_RESULT"
}

# Read JSON from stdin
input=$(cat)

# Handle empty or invalid JSON
if ! echo "$input" | jq empty 2>/dev/null; then
  echo "Invalid or empty JSON input, returning empty triage result" >&2
  if [[ -n "$GET_ACTION_PR" ]]; then
    echo "skip"
  elif [[ -n "$GET_CATEGORY_PR" ]]; then
    echo "unknown"
  else
    echo "$EMPTY_RESULT"
  fi
  exit 0
fi

TRIAGE_RESULT=$(echo "$input" | classify)

if [[ -n "$GET_ACTION_PR" || -n "$GET_CATEGORY_PR" ]]; then
  # 每类只含互斥子集，按类别优先级查一次即可得到该 PR 的唯一归属
  CATEGORY=$(echo "$TRIAGE_RESULT" | jq -r --argjson pr "${GET_ACTION_PR:-$GET_CATEGORY_PR}" '
    if ([.do_not_merge[] | select(.number == $pr)] | length) > 0 then "do_not_merge"
    elif ([.behind[] | select(.number == $pr)] | length) > 0 then "behind"
    elif ([.conflicting[] | select(.number == $pr)] | length) > 0 then "conflicting"
    elif ([.pending[] | select(.number == $pr)] | length) > 0 then "pending"
    elif ([.stalled[] | select(.number == $pr)] | length) > 0 then "stalled"
    elif ([.mergeable[] | select(.number == $pr)] | length) > 0 then "mergeable"
    else "unknown"
    end
  ')
  if [[ -n "$GET_CATEGORY_PR" ]]; then
    echo "$CATEGORY"
  else
    case "$CATEGORY" in
      behind) echo "update-branch" ;;
      conflicting) echo "notify" ;;
      do_not_merge) echo "skip" ;;
      pending|stalled) echo "wait" ;;
      mergeable) echo "merge" ;;
      *) echo "skip" ;;
    esac
  fi
  exit 0
fi

echo "$TRIAGE_RESULT"
