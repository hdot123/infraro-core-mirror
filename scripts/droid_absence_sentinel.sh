#!/usr/bin/env bash
# Droid 缺席哨兵（并入 watchdog；FINAL-PLAN §2 哨兵行）
#
# 判定（纯函数 / classify 子命令可单测）：
#   - ①②（substrate + quality）已绿，且墙钟超过 ABSENCE_TTL_SECONDS 仍无
#     droid-review 实例 → 缺席 → 显式红（由 caller 贴 check / 重派）
#   - quality-gate NOT_READY → 跳过（≠质量红；可重试、不升级骂街）
#   - 存在 in_progress/queued 的 droid-review check 或 workflow run → 不误杀
#   - 持 merge-queue 且尚未轮到（合法排队）且无「已绿过久」→ 不误伤
#
# 时限口径：默认 ABSENCE_TTL_SECONDS=3600 = 2× watchdog cron 窗（11,41 每
# 30min）。禁止写死「30min 空头支票」——单窗不足以覆盖投递抖动。
#
# 用法：
#   droid_absence_sentinel.sh classify   # stdin: 见下方 schema → stdout JSON decision
#   droid_absence_sentinel.sh tick       # 扫 open PR（需 GH_TOKEN/REPO）
#
# classify stdin schema（每 PR 一对象，或数组）:
#   {
#     "number": 1,
#     "head_sha": "...",
#     "labels": ["merge-queue"],
#     "now_epoch": 1700000000,
#     "gates_green_since_epoch": 1699990000,   # ①② 变绿时刻；缺省用 now-∞
#     "check_runs": [{"name","status","conclusion","not_ready"?}],
#     "droid_runs": [{"status":"in_progress"|"queued"|"completed",...}]
#   }

set -euo pipefail

ABSENCE_TTL_SECONDS="${ABSENCE_TTL_SECONDS:-3600}"
REPO="${REPO:-${GITHUB_REPOSITORY:-}}"
DRY_RUN="${ABSENCE_SENTINEL_DRY_RUN:-0}"
CHECK_NAME="${ABSENCE_CHECK_NAME:-droid-review}"

log() { echo "[absence-sentinel] $*" >&2; }

classify() {
  jq -c --argjson ttl "$ABSENCE_TTL_SECONDS" --arg check "$CHECK_NAME" '
    def as_arr: if type == "array" then . else [.] end;
    def names(pr): ((pr.labels // []) | map(if type == "object" then (.name // empty) else . end));
    def runs(pr): (pr.check_runs // []);
    def latest(pr; gate):
      [runs(pr)[] | select(.name == gate and .id != null)] | sort_by(.id // -1) | last;
    def gate_success(pr; gate):
      latest(pr; gate) as $r
      | $r != null and $r.status == "completed" and ($r.conclusion // "") == "success";
    def quality_not_ready(pr):
      latest(pr; "quality-gate") as $r
      | $r != null and $r.status == "completed"
        and ($r.conclusion // "") == "failure" and ($r.not_ready == true);
    def droid_instances(pr):
      [runs(pr)[] | select(.name == $check)];
    def droid_in_progress(pr):
      any(droid_instances(pr)[]; .status == "in_progress" or .status == "queued")
      or any((pr.droid_runs // [])[]; .status == "in_progress" or .status == "queued");
    def droid_absent(pr):
      (droid_instances(pr) | length) == 0;
    def age_ok(pr):
      ((pr.now_epoch // 0) - (pr.gates_green_since_epoch // 0)) >= $ttl;
    def legit_queued(pr):
      any(names(pr)[]; . == "merge-queue" or . == "droid-review-queue");
    as_arr
    | map(
        if quality_not_ready(.) then
          {number, decision: "skip_not_ready", reason: "quality-gate NOT_READY (retryable, not quality-red)"}
        elif (gate_success(.; "substrate-gate-suite") | not) then
          {number, decision: "skip_gates", reason: "substrate not green"}
        elif (gate_success(.; "quality-gate") | not) then
          {number, decision: "skip_gates", reason: "quality-gate not green"}
        elif droid_in_progress(.) then
          {number, decision: "skip_in_progress", reason: "droid-review in_progress/queued (do not kill)"}
        elif (droid_absent(.) | not) then
          {number, decision: "skip_present", reason: "droid-review instance exists"}
        elif (age_ok(.) | not) then
          {number, decision: "skip_ttl", reason: ("within TTL " + ($ttl|tostring) + "s")}
        elif legit_queued(.) and ((.force_absence // false) | not) then
          # 合法排队：未过「已绿过久」强制面时不误伤；TTL 已过则仍显式红
          # （硬门 3：pending 最长等待 → 哨兵转红）。此处 age_ok 已真 → 继续 mark。
          {number, decision: "mark_absent", reason: "absent past TTL (queued but overdue)", head_sha, action: "explicit_red"}
        else
          {number, decision: "mark_absent", reason: "absent past TTL", head_sha, action: "explicit_red"}
        end
      )
  '
}

post_explicit_red() {
  local pr="$1" sha="$2" reason="$3"
  if [ -z "$sha" ]; then
    log "PR #$pr: no head sha; skip post"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "dry-run explicit red PR #$pr sha=$sha ($reason)"
    return 0
  fi
  # 贴同名 droid-review failure，ruleset 面显式非绿（缺席≠skip 假绿）
  local payload
  payload=$(jq -n \
    --arg name "$CHECK_NAME" \
    --arg sha "$sha" \
    --arg title "ABSENCE_SENTINEL: not-started" \
    --arg summary "Watchdog absence sentinel: droid-review missing after TTL=${ABSENCE_TTL_SECONDS}s. reason=$reason. NOT a quality-red escalation path for NOT_READY." \
    '{
      name: $name,
      head_sha: $sha,
      status: "completed",
      conclusion: "failure",
      output: {title: $title, summary: $summary}
    }')
  if echo "$payload" | gh api -X POST "repos/$REPO/check-runs" --input - >/dev/null; then
    log "posted explicit red on #$pr ($sha)"
  else
    log "warning: check-run post failed for #$pr"
  fi
}

tick() {
  if [ -z "$REPO" ]; then
    log "ERROR: REPO unset"
    return 1
  fi
  local now prs
  now=$(date -u +%s)
  prs=$(gh pr list --repo "$REPO" --state open --limit 50 --json number,headRefOid,labels,updatedAt)
  local decisions="[]"
  while read -r row; do
    [ -z "$row" ] && continue
    local num sha
    num=$(echo "$row" | jq -r '.number')
    sha=$(echo "$row" | jq -r '.headRefOid // empty')
    local checks='[]'
    if [ -n "$sha" ]; then
      checks=$(gh api "repos/$REPO/commits/$sha/check-runs" --paginate \
        | jq -s '[.[] | .check_runs // [] | if type=="array" then .[] else empty end]
                 | map(select(.id!=null) | {
                     id, name, status, conclusion,
                     not_ready: (((.output.title//"")+"\n"+(.output.summary//"")+"\n"+(.name//""))|test("NOT_READY"))
                   })' 2>/dev/null || echo '[]')
    fi
    local droid_runs
    droid_runs=$(gh api "repos/$REPO/actions/runs?head_sha=$sha&per_page=20" \
      --jq '[.workflow_runs[] | select(.name=="Droid Auto Review") | {id,status,conclusion}]' 2>/dev/null || echo '[]')
    # gates_green_since：无精确时戳时用 updatedAt 近似（NOTES 标明）
    local since
    since=$(echo "$row" | jq -r '.updatedAt')
    local since_epoch
    since_epoch=$(date -u -d "$since" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$since" +%s)
    local payload
    payload=$(jq -n \
      --argjson number "$num" \
      --arg head_sha "$sha" \
      --argjson labels "$(echo "$row" | jq '.labels')" \
      --argjson now_epoch "$now" \
      --argjson gates_green_since_epoch "$since_epoch" \
      --argjson check_runs "$checks" \
      --argjson droid_runs "$droid_runs" \
      '{number:$number,head_sha:$head_sha,labels:$labels,now_epoch:$now_epoch,
        gates_green_since_epoch:$gates_green_since_epoch,check_runs:$check_runs,droid_runs:$droid_runs}')
    local dec
    dec=$(echo "$payload" | classify | jq -c '.[0]')
    decisions=$(echo "$decisions" | jq -c --argjson d "$dec" '. + [$d]')
    local decision
    decision=$(echo "$dec" | jq -r '.decision')
    if [ "$decision" = "mark_absent" ]; then
      post_explicit_red "$num" "$sha" "$(echo "$dec" | jq -r '.reason')"
    else
      log "PR #$num → $decision ($(echo "$dec" | jq -r '.reason'))"
    fi
  done < <(echo "$prs" | jq -c '.[]')
  echo "$decisions"
}

cmd="${1:-tick}"
case "$cmd" in
  classify) classify ;;
  tick) tick ;;
  *)
    echo "usage: $0 {classify|tick}" >&2
    exit 2
    ;;
esac
