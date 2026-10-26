#!/usr/bin/env bash
# PR merge-queue FIFO controller（FINAL-PLAN §2 合并 FIFO）
#
# 三要素（写清，契约测试锁定）：
#   1. 介质：label `merge-queue`（在队）/ `merge-queue-cooldown`（踢出冷却）
#   2. 排序键：在队 PR 按 createdAt 升序；队头 = 最老者
#   3. 唯一写者：本脚本（经 pr-queue-controller.yml）是入队/出队/踢出/冷却
#      的唯一写者；auto-merge 只读队头（isQueueHead），禁止自称队头直合、
#      禁止 mergeable 无序扫合。
#
# 配套：auto-merge max-parallel:1（单飞腿）+ triage mergeable 仅队头。
#
# 用法：
#   pr_queue_controller.sh tick                 # 全量：ensure → expire → enroll → kick
#   pr_queue_controller.sh compute-head         # stdin: PR JSON 数组 → stdout: 队头 number 或空
#   pr_queue_controller.sh select-mergeable     # stdin: triage-like PR 数组 → 仅保留队头 mergeable
#   pr_queue_controller.sh decide-kick <json>   # 判定是否踢出（纯函数，测用）
#   pr_queue_controller.sh ensure-labels        # 幂等建标
#
# 环境：
#   REPO / GH_TOKEN（写 label 需要）
#   HEAD_TIMEOUT_MINUTES   默认 90（队头墙钟；计时零点 = 成为队头/持标起，见 NOTES）
#   COOLDOWN_MINUTES       默认 60（≥ 一个 schedule 窗，防踢出立刻再入队头）
#   PR_QUEUE_DRY_RUN       =1 时只打日志不写 label

set -euo pipefail

LABEL_QUEUE="${PR_QUEUE_LABEL:-merge-queue}"
LABEL_COOLDOWN="${PR_QUEUE_COOLDOWN_LABEL:-merge-queue-cooldown}"
HEAD_TIMEOUT_MINUTES="${HEAD_TIMEOUT_MINUTES:-90}"
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-60}"
DRY_RUN="${PR_QUEUE_DRY_RUN:-0}"
REPO="${REPO:-${GITHUB_REPOSITORY:-}}"

log() { echo "[pr-queue] $*" >&2; }

# ── 纯函数：从 PR 数组算队头（有 merge-queue、无 cooldown，按 createdAt）──
# stdin: JSON array of {number, createdAt, labels:[{name}|string]}
# stdout: head number or empty
compute_head() {
  jq -r --arg q "$LABEL_QUEUE" --arg c "$LABEL_COOLDOWN" '
    def names:
      ((.labels // []) |
        if type == "array"
        then map(if type == "object" then (.name? // empty) else . end | select(type == "string"))
        else (if type == "string" then [.] else [] end)
        end);
    [ .[]
      | select(any(names[]; . == $q))
      | select(any(names[]; . == $c) | not)
    ]
    | sort_by(.createdAt // "")
    | .[0].number // empty
  '
}

# ── 纯函数：mergeable 列表仅保留队头（禁止无序扫合）──
# stdin: JSON object with .mergeable array (triage shape) OR array of candidates
# env/arg: optional full fleet on fd via MERGE_FLEET_JSON for head among all queued
# If input is triage result, truncates .mergeable to head-only (or [] if head not in list).
select_mergeable_head_only() {
  local fleet_json="${MERGE_FLEET_JSON:-}"
  if [ -n "$fleet_json" ]; then
    local head
    head=$(echo "$fleet_json" | compute_head)
    jq -c --argjson head "${head:-null}" '
      if type == "object" and has("mergeable") then
        .mergeable = (
          if $head == null then []
          else [.mergeable[] | select(.number == $head)]
          end
        )
      elif type == "array" then
        if $head == null then []
        else [.[] | select(.number == $head)]
        end
      else .
      end
    '
  else
    # 无 fleet：在候选自身中按三要素算头（同批 mergeable + merge-queue）
    jq -c --arg q "$LABEL_QUEUE" --arg c "$LABEL_COOLDOWN" '
      def names(pr):
        ((pr.labels // []) |
          if type == "array"
          then map(if type == "object" then (.name? // empty) else . end | select(type == "string"))
          else (if type == "string" then [.] else [] end)
          end);
      def queued(pr):
        any(names(pr)[]; . == $q) and (any(names(pr)[]; . == $c) | not);
      def head_num(arr):
        [arr[] | select(queued(.))] | sort_by(.createdAt // "") | .[0].number // null;
      if type == "object" and has("mergeable") then
        (head_num(.mergeable)) as $h
        | .mergeable = (if $h == null then [] else [.mergeable[] | select(.number == $h)] end)
      elif type == "array" then
        (head_num(.)) as $h
        | if $h == null then [] else [.[] | select(.number == $h)] end
      else .
      end
    '
  fi
}

# ── 纯函数：是否踢出队头 ──
# stdin JSON: {head_since_epoch, now_epoch, head_timeout_minutes, is_in_progress?, is_legit_queued?}
# stdout: kick|keep + reason
decide_kick() {
  jq -r --argjson default_timeout "$HEAD_TIMEOUT_MINUTES" '
    (.head_timeout_minutes // $default_timeout) as $to
    | (.now_epoch - .head_since_epoch) as $age
    | if (.is_in_progress // false) then "keep\tin_progress"
      elif (.is_legit_queued // false) then "keep\tlegit_queued"
      elif $age >= ($to * 60) then "kick\ttimeout_age_sec=\($age)"
      else "keep\tage_sec=\($age)"
      end
  '
}

ensure_labels() {
  local repo="${1:-$REPO}"
  if [ -z "$repo" ]; then
    log "ERROR: REPO unset"
    return 1
  fi
  for spec in "${LABEL_QUEUE}:0E8A16:FIFO merge queue (controller-owned)" \
              "${LABEL_COOLDOWN}:BFDADC:FIFO kick cooldown (controller-owned)"; do
    IFS=: read -r name color desc <<<"$spec"
    if [ "$DRY_RUN" = "1" ]; then
      log "dry-run ensure label $name"
      continue
    fi
    if gh api "repos/$repo/labels/$name" >/dev/null 2>&1; then
      log "label exists: $name"
    else
      gh api -X POST "repos/$repo/labels" \
        -f name="$name" -f color="$color" -f description="$desc" >/dev/null \
        || log "warning: create label $name failed (may race)"
      log "label created: $name"
    fi
  done
}

_list_open_prs() {
  local repo="${1:-$REPO}"
  gh pr list --repo "$repo" --state open --limit 100 \
    --json number,createdAt,isDraft,labels,updatedAt,title
}

_add_label() {
  local repo="$1" pr="$2" label="$3"
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run add $label → #$pr"
    return 0
  fi
  gh pr edit "$pr" --repo "$repo" --add-label "$label" >/dev/null
}

_remove_label() {
  local repo="$1" pr="$2" label="$3"
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run remove $label → #$pr"
    return 0
  fi
  gh pr edit "$pr" --repo "$repo" --remove-label "$label" >/dev/null 2>/dev/null || true
}

expire_cooldowns() {
  local repo="${1:-$REPO}"
  local prs
  prs=$(_list_open_prs "$repo")
  local now
  now=$(date -u +%s)
  echo "$prs" | jq -c --arg c "$LABEL_COOLDOWN" '.[] | select(any(.labels[]; .name == $c))' | while read -r row; do
    local num updated age
    num=$(echo "$row" | jq -r '.number')
    updated=$(echo "$row" | jq -r '.updatedAt')
    # 冷却起点近似用 updatedAt（label 事件无廉价精确时戳；NOTES 标明）
    age=$(( now - $(date -u -d "$updated" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$updated" +%s) ))
    if [ "$age" -ge $((COOLDOWN_MINUTES * 60)) ]; then
      log "expire cooldown on #$num (age=${age}s)"
      _remove_label "$repo" "$num" "$LABEL_COOLDOWN"
    else
      log "cooldown hold #$num (age=${age}s < ${COOLDOWN_MINUTES}m)"
    fi
  done
}

enroll_eligible() {
  local repo="${1:-$REPO}"
  local prs
  prs=$(_list_open_prs "$repo")
  echo "$prs" | jq -c --arg q "$LABEL_QUEUE" --arg c "$LABEL_COOLDOWN" '
    .[]
    | select(.isDraft == false)
    | select(any(.labels[]; .name == "do-not-merge") | not)
    | select(any(.labels[]; .name == $c) | not)
    | select(any(.labels[]; .name == $q) | not)
  ' | while read -r row; do
    local num
    num=$(echo "$row" | jq -r '.number')
    log "enroll #$num → $LABEL_QUEUE"
    _add_label "$repo" "$num" "$LABEL_QUEUE"
  done
}

kick_stale_head() {
  local repo="${1:-$REPO}"
  local prs head
  prs=$(_list_open_prs "$repo")
  head=$(echo "$prs" | compute_head)
  if [ -z "$head" ]; then
    log "no queue head"
    echo "head="
    return 0
  fi
  log "queue head=#$head"
  local row updated now age decision
  row=$(echo "$prs" | jq -c --argjson n "$head" '.[] | select(.number == $n)')
  updated=$(echo "$row" | jq -r '.updatedAt')
  now=$(date -u +%s)
  age_epoch=$(date -u -d "$updated" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$updated" +%s)
  decision=$(jq -n \
    --argjson head_since_epoch "$age_epoch" \
    --argjson now_epoch "$now" \
    --argjson head_timeout_minutes "$HEAD_TIMEOUT_MINUTES" \
    --argjson is_in_progress false \
    --argjson is_legit_queued false \
    '{head_since_epoch:$head_since_epoch,now_epoch:$now_epoch,head_timeout_minutes:$head_timeout_minutes,is_in_progress:$is_in_progress,is_legit_queued:$is_legit_queued}' \
    | decide_kick)
  local action reason
  action=$(echo "$decision" | cut -f1)
  reason=$(echo "$decision" | cut -f2-)
  if [ "$action" = "kick" ]; then
    log "KICK head=#$head ($reason)"
    _remove_label "$repo" "$head" "$LABEL_QUEUE"
    _add_label "$repo" "$head" "$LABEL_COOLDOWN"
    # 可观测：队头踢出评论（幂等 sentinel）
    if [ "$DRY_RUN" != "1" ]; then
      local sentinel
      sentinel="<!-- pr-queue-kick-${head}-$(date -u +%Y%m%d%H) -->"
      local existing
      existing=$(gh pr view "$head" --repo "$repo" --json comments \
        --jq "[.comments[].body | select(contains(\"${sentinel}\"))] | length" 2>/dev/null || echo 0)
      if [ "${existing:-0}" = "0" ]; then
        gh pr comment "$head" --repo "$repo" --body "$(cat <<EOF
⏱️ **FIFO 队头踢出（冷却 ${COOLDOWN_MINUTES}m）**

队头超时（\`HEAD_TIMEOUT_MINUTES=${HEAD_TIMEOUT_MINUTES}\`，原因：${reason}）。
已移除 \`${LABEL_QUEUE}\`，打上 \`${LABEL_COOLDOWN}\`。冷却结束后由队列控制器重新入队。

${sentinel}
EOF
)" >/dev/null || log "warning: kick comment failed"
      fi
    fi
  else
    log "keep head=#$head ($reason)"
  fi
  echo "head=$head"
  echo "kick_action=$action"
}

tick() {
  local repo="${1:-$REPO}"
  if [ -z "$repo" ]; then
    log "ERROR: REPO unset"
    return 1
  fi
  ensure_labels "$repo"
  expire_cooldowns "$repo"
  enroll_eligible "$repo"
  kick_stale_head "$repo"
}

cmd="${1:-tick}"
case "$cmd" in
  tick) tick "${2:-}" ;;
  ensure-labels) ensure_labels "${2:-}" ;;
  compute-head) compute_head ;;
  select-mergeable) select_mergeable_head_only ;;
  decide-kick) decide_kick ;;
  expire-cooldowns) expire_cooldowns "${2:-}" ;;
  enroll) enroll_eligible "${2:-}" ;;
  kick) kick_stale_head "${2:-}" ;;
  *)
    echo "usage: $0 {tick|ensure-labels|compute-head|select-mergeable|decide-kick|...}" >&2
    exit 2
    ;;
esac
