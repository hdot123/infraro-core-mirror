#!/usr/bin/env bash
# Auto-merge triage script (INFRA-416 / INFRA-428 / v3.2.2 §3.2)
# Classifies PRs by mergeable state, mergeStateStatus and REST three-gate verdict.
#
# Input:  JSON array of PRs via stdin (gh pr view/list --json
#         number,mergeable,mergeStateStatus,isDraft,statusCheckRollup,labels,headRefOid,createdAt,isCrossRepository
#         + checkRuns: REST check-runs(head_sha) enrichment injected by the caller —
#         triage 本体零 API 副作用)
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
#   default            classify stdin, emit full JSON (behind 类截 behind=1，
#                      round-6 shard-9 ②：FIFO 合并出口语义)
#   --get-action N     classify stdin, emit the single action for PR number N
#                      (merge|update-branch|notify|wait|skip; exit 0; empty or
#                      invalid input also emits skip — triage must never fail
#                      the workflow; round-6 shard-9 ②：查询模式不截断——
#                      被截 behind PR 报真实分类 update-branch)
#   --get-category N   classify stdin, emit the single category for PR number N
#                      (mergeable|behind|conflicting|pending|stalled|unknown|
#                      do_not_merge; missing PR -> unknown; 查询模式不截断，
#                      同 --get-action). Workflow uses this
#                      to derive auxiliary outputs (e.g. stalled flag) without
#                      duplicating classification logic.
#
# Green verdict (v3.2.2 §3.2 三门 REST 绿判；round-3 ① 排序键换轴修订):
#   required_gate_success = REST check-runs(head_sha) 上
#   substrate-gate-suite / quality-gate / droid-review 三门各取「全部实例
#   （不按 status 预过滤）」中 check-run id 最新者（id 单调分配永非 null），
#   仅当该实例 status=completed 且 conclusion=success 才算绿。
#   mergeable = required_gate_success AND PR mergeable API AND 非 do-not-merge
#   AND FIFO 队头（见下）。已落定：success|failure；可合并：仅全 success；
#   skipped/neutral/missing/pending → 非 mergeable。statusCheckRollup 仅辅助
#   pending；mergeable 判定禁止走 rollup 绿判（#372 假绿根因契约化封堵）。
#
# FIFO（FINAL-PLAN §2 / 替换 v3.2.2 §3.4 无序 mergeable）：
#   三要素：① label 介质 merge-queue（冷却 merge-queue-cooldown）；② 排序键
#   createdAt；③ 队头唯一写者 = pr-queue-controller（本脚本只读 isQueueHead）。
#   mergeable 仅队头：须持 merge-queue、无 cooldown、且 isQueueHead==true
#   （caller 按 label 舰队计算注入；缺字段时 default 模式按本批 createdAt 截 1）。
#   禁止 mergeable 无序扫合；禁止快路径自称队头直合。
#   behind=1 截断仍保留（update-branch 单飞）；merge matrix max-parallel:1。
#   NOT_READY（quality-gate failure + checkRuns[].not_ready）：≠质量红 → pending
#   （可重试、不升级 stalled 骂街），ruleset 面仍非绿（fail-closed）。
#
# Classification precedence (one PR falls into exactly one category,
# decided by classify_pr in a single pass — categories are exclusive by
# construction, no cross-select races):
#   labels contains do-not-merge -> do_not_merge (skip; escape hatch, #314 根因)
#   mergeable=UNKNOWN    -> unknown     (skip; GitHub still computing)
#   mergeable=CONFLICTING-> conflicting (notify)
#   mergeStateStatus=BEHIND -> behind   (update-branch)
#   isDraft / DRAFT / BLOCKED -> pending (wait)
#   quality-gate NOT_READY only -> pending (wait; 可重试，不 stalled)
#   rollup empty or has incomplete checks -> pending (wait; early-fire guard)
#   rollup reported non-success (stalled leg) -> stalled (wait + escalate)
#   MERGEABLE + REST 三门全 success + FIFO 队头 -> mergeable (merge)
#   MERGEABLE + 三门绿但非队头/未入队 -> pending (wait; FIFO)
#
# Why these categories exist (INFRA-428):
# - BLOCKED/DRAFT previously fell into "mergeable" → every 10-min sweep fired
#   a merge attempt at an un-mergeable PR → red leg + the shared action's own
#   failure check run poisoned the head SHA (2026-08-18 self-poisoning
#   deadlock, PR #779 mechanism). wait = benign skip, schedule re-sweeps.
# - early-fire: pull_request_target(opened) / workflow_run of one workflow
#   can arrive before all required checks reported. mergeStateStatus alone is
#   async/cached and can be stale-CLEAN in that window; cross-checking
#   statusCheckRollup keeps pending until every check run has reported a
#   conclusion. Rollup empty = no checks reported yet → fail-closed wait
#   (CI runs on every PR without paths filter, so an empty rollup is
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
#
# $1 = TRUNCATE ("no-truncate" skips truncate_fleet, see TRUNCATE_FLEET note).
classify() {
  local TRUNCATE_FLEET="${1:-truncate}"
  jq --argjson truncate "$([ "$TRUNCATE_FLEET" = "truncate" ] && echo true || echo false)" '
    def rollup(pr): (pr.statusCheckRollup // []);
    # 输入防御（round-6 shard-9 ③，round-9 sh9 ① fail-closed 修订）：取数形状
    # 漂移（labels 落成字符串/数字/null）时，labels[] 遍历使整个 jq 程序失败
    # → EMPTY_RESULT → 全部分类静默归零。归一规则必须保持 do-not-merge 真值：
    # 标量字符串按字面值入列、字符串数组条目按字面值入列——round-6 版
    # 「标量 → []」把 "do-not-merge" 字符串漂移静默洗成无标签（逃生门
    # fail-open，PR 误判 mergeable）。仅非字符串标量按「无标签」语义丢弃
    # （防御面 = 防炸穿，不是真值改写）。两处消费
    # （eligible_for_update_branch / has_do_not_merge）走同一归一函数。
    def label_names(pr):
      ((pr.labels // []) |
        if type == "array"
        then map(if type == "object" then (.name? // empty) else . end
                 | select(type == "string"))
        else (if type == "string" then [.] else [] end)
        end);
    # FIFO truncate（FINAL-PLAN §2）：behind=1；mergeable 再保险只留队头 1。
    # classify_pr 已要求 isQueueHead+merge-queue；此处防批量误入与缺字段回落。
    # 查询模式不截断。
    def eligible_for_update_branch(pr):
      pr as $p
      | (($p.isCrossRepository // false) | not)
      and ((label_names($p) | index("do-not-merge")) == null);
    def has_merge_queue(pr):
      any(label_names(pr)[]; . == "merge-queue")
      and (any(label_names(pr)[]; . == "merge-queue-cooldown") | not);
    def is_fifo_head(pr):
      (pr.isQueueHead // false) == true;
    # 重赋值前先把数组绑到临时变量（|= RHS 上下文是数组本身）。
    def truncate_fleet(result):
      if $truncate then
        result
        | (.behind // []) as $b
        | .behind = ($b
            | map(select(eligible_for_update_branch(.)))
            | sort_by(.createdAt // "")
            | .[0:1])
        | (.mergeable // []) as $m
        | .mergeable = (
            ($m | map(select(is_fifo_head(.) and has_merge_queue(.))) | sort_by(.createdAt // "") | .[0:1])
            as $explicit
            | if ($explicit | length) > 0 then $explicit
              else ($m | map(select(has_merge_queue(.))) | sort_by(.createdAt // "") | .[0:1])
              end
          )
      else
        result
      end;
    # v3.2.2 §3.2 三门 REST 绿判：绿判唯一入口 = REST check-runs(head_sha)
    # （调用方以 checkRuns 字段富集，triage 本体零 API 副作用）。
    # 三门 = substrate-gate-suite / quality-gate / droid-review（Ruleset
    # required 名单）；各取「全部实例（不按 status 预过滤）」中 check-run
    # id 最新者，仅当该实例 status=completed 且 conclusion=success 才算绿。
    # skipped/neutral/missing/pending 一律非绿。checkRuns 缺失/形状异常 →
    # fail-closed 非绿（绝不退化回 rollup 绿判——SKIPPED≈通过 + any(SUCCESS)
    # 旁路 SUCCESS 即 #372 假绿根因）。
    def required_gates: ["substrate-gate-suite", "quality-gate", "droid-review"];
    # jq 函数参数是 call-by-name filter：gate 必须显式 gate as $gate 绑定，
    # 否则 gate 在 map(select(...)) 的元素上下文求值变成 . == . 恒假。
    # round-2 #6/#7 + round-3 ①（#381/#168 终局分级）fail-closed 实例选择：
    # 不得先按 status=="completed" 过滤再取最新——同门更新的 in_progress/queued
    # 实例被忽略会让旧 success 冒充「最新实例」（labeled/ready_for_review 重触发
    # 窗口 → 误合并，fail-open）。排序键 = check-run id（GitHub 单调分配、
    # 永非 null）：started_at/completed_at 都有 null-and-never-populated 语义——
    # 裸 sort_by(started_at) 把 null 排最前（#7：取消实例被旧 success 顶替），
    # 9999 哨兵把 null 一律当最新（round-3 ① #168 类回摆：旧 null-cancelled
    # 永久压制新 success → 永久拒合）。id 唯一同时满足三面：新 in_progress
    # （#6）、新 cancelled（#7）id 更大压制旧 success；旧 null-cancelled
    # id 更小被新 success 取代（①）。
    def gate_latest_completed(pr; gate):
      gate as $gate
      | ((pr.checkRuns // {}) | (.check_runs // []))
      | map(select(.name == $gate and .id != null))
      | sort_by(.id // -9007199254740991)
      | last;
    # 绿判收敛点必须验证最新实例本身 completed+success：最新实例仍在
    # in_progress/queued → 非绿（rollup 未报齐面 → pending，交下一轮 sweep）。
    def required_gate_success(pr):
      all(required_gates[]; gate_latest_completed(pr; .) as $run
          | $run != null and $run.status == "completed" and $run.conclusion == "success");
    # complete = every check run has reported a grey-green conclusion.
    # has("conclusion") alone is not enough: the rollup of a queued/
    # in_progress check contains "conclusion": null, and jq has() would
    # treat it as reported. Worse, any non-empty STRING counted as reported —
    # so three-gates-green + red bypass leg (FAILURE/CANCELLED/TIMED_OUT)
    # classified mergeable and the stalled branch was unreachable
    # (round-1 P1-1). Grey-green map matches the ci-ok zero-red scan:
    # reported = success/skipped/neutral only; red conclusions are not
    # reported (→ stalled via has_red_conclusion); null/empty = not yet
    # reported (→ pending, early-fire fail-closed).
    # statusCheckRollup 仅辅助 pending（未报齐 → wait），不参与 mergeable。
    # round-3 ②：报齐/红腿判定与绿判同轴——latest-per-name。rollup 条目按
    # 检查名分组取最后出现者（数组序 = GitHub 上报序，与 REST 侧 .id 单调性
    # 同构的时序代理）。any-instance 语义（旧）会把被取代的旧红/旧取消残留
    # 当红腿，绿 PR 永久 stalled（round-2 修复回摆）；只有「同检查名最新
    # 实例」代表该检查的当前状态。无名条目（name null/缺失）共享单桶取
    # 最后者——退化数据保守面，不阻塞有名检查路径。
    def latest_per_name(pr):
      reduce rollup(pr)[] as $item ({}; .[$item.name // ""] = $item);
    def checks_reported(pr):
      (rollup(pr) | length) > 0
      and all(latest_per_name(pr)[];
        ((.conclusion // "") | ascii_downcase) as $c
        | $c == "success" or $c == "skipped" or $c == "neutral"
      );
    # 红腿探测（round-1 P1-1 的 stalled 可达性腿 + round-3 ② latest 修订）：
    # 同名旁路检查最新实例红 → stalled（wait + escalate），绝不 mergeable。
    # cancelled 在零红扫描（ci-ok 侧）按编排副作用豁免，但旁路 rollup 上按
    # 红处理——被取消的腿必须以 stalled 显面，而非「已报齐可合并」。
    # 被取代的旧红实例（同检查名后随新实例）不再是红腿——残留红 + 新绿 =
    # 绿（round-2 修复回摆 #381 补丁：any-red-wins 与门判 latest-wins 不同
    # 轴会把绿 PR 永久 stalled）。
    def has_red_conclusion(pr):
      any(latest_per_name(pr)[];
        ((.conclusion // "") | ascii_downcase) as $c
        | $c == "failure" or $c == "cancelled" or $c == "timed_out" or $c == "action_required"
      );

    # NOT_READY ≠ 质量红（FINAL-PLAN §1.2）：quality-gate 最新实例 failure 且
    # checkRuns[].not_ready==true → 可重试 pending，不升级 stalled 骂街。
    # ruleset 面仍非绿（required_gate_success 要求 success）。
    def quality_gate_not_ready(pr):
      gate_latest_completed(pr; "quality-gate") as $qg
      | $qg != null
        and ($qg.status == "completed")
        and (($qg.conclusion // "") | ascii_downcase) == "failure"
        and ($qg.not_ready == true);

    # rollup 红若仅反映 quality-gate NOT_READY（无其它红腿名），不当地 stalled。
    def red_only_quality_not_ready(pr):
      quality_gate_not_ready(pr)
      and (
        [latest_per_name(pr) | to_entries[]
          | select((.value.conclusion // "" | ascii_downcase) as $c
              | $c == "failure" or $c == "cancelled" or $c == "timed_out" or $c == "action_required")
          | .key
        ] | map(select(. != "quality-gate" and . != "")) | length
      ) == 0;

    # do-not-merge 逃生舱（#314 事故根因修复）：labels 含 do-not-merge（大小写
    # 不敏感）即 do_not_merge。labels 缺失/为空/形状异常 → label_names 兜底为
    # 不命中（round-6 shard-9 ③：labels[] 直接遍历遇标量形状会炸穿整个 jq，
    # 全部分类塌缩 EMPTY_RESULT——分类照常进行；round-9 sh9 ①：字符串形状
    # 的真值不再被洗掉，见 label_names 注释）。
    def has_do_not_merge(pr):
      any(label_names(pr)[]; ascii_downcase == "do-not-merge");

    def classify_pr(pr):
      if has_do_not_merge(pr) then "do_not_merge"
      elif pr.mergeable == "UNKNOWN" then "unknown"
      elif pr.mergeable == "CONFLICTING" then "conflicting"
      elif pr.mergeStateStatus == "BEHIND" then "behind"
      elif (pr.isDraft // false) or pr.mergeStateStatus == "DRAFT" or pr.mergeStateStatus == "BLOCKED" then "pending"
      elif (rollup(pr) | length) == 0 then "pending"
      # NOT_READY：可重试，不 stalled（§1.2）
      elif red_only_quality_not_ready(pr) then "pending"
      # has_red_conclusion 先于 checks_reported：红腿（报齐非灰绿）必须 stalled
      # 可达——旧语义红 conclusion 也算「已报齐」，mergeable 分支把它当 pass-through
      # 吞掉，stalled 永不可达（round-1 P1-1）。
      elif has_red_conclusion(pr) then "stalled"
      # 规格 §3.2 + FIFO：三门绿且队头（merge-queue + isQueueHead）才 mergeable。
      elif required_gate_success(pr) and checks_reported(pr)
           and has_merge_queue(pr) and is_fifo_head(pr) then "mergeable"
      # 三门绿但未入队/非队头 → FIFO pending（禁止自称队头直合）
      elif required_gate_success(pr) and checks_reported(pr) then "pending"
      elif checks_reported(pr) then "stalled"
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
    | truncate_fleet(.)
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

# round-6 shard-9 ②：查询模式跳过 truncate_fleet——被 FIFO 截掉的 behind PR
# 在查询面上必须报真实分类（behind/update-branch），否则调用方无法区分
# 「真 skip」与「本轮被 FIFO 推迟」。default 模式截断语义不变。
TRIAGE_RESULT=$(echo "$input" | classify "$([ -n "$GET_ACTION_PR$GET_CATEGORY_PR" ] && echo no-truncate || echo truncate)")

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
