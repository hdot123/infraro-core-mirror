#!/usr/bin/env bash
# shellcheck disable=SC2034
# reconcile-evolution.sh — 拉取式对账，发现孤立的 evolution finding 并补触发
# 设计：push(webhook) + pull(本脚本) = 最终一致
#
# 核心逻辑：直接查询 Linear API（而非 GitHub Issue body），因为 Linear native
# sync 不会在 GitHub Issue body 中写入 Linear ref linkback。
#
# 调度：launchd 每小时的 :15 和 :45 运行（与 scanner cron :00/:30 错开 15 分钟）
# 串行：每次只补触发一个 Issue（per-team 锁保证串行，下一个 tick 再补）
#
# 守则（与 ci-gateway skill 同文）：
# - 禁止静默关 PR：任何 close 前必须先 comment 且评论成功
# - comment 失败则本轮放弃不 close
set -euo pipefail

# === 配置 ===
SCRIPT_DIR="${HOME}/.factory/webhook/scripts"
WEBHOOK_BASE="${HOME}/.factory/webhook"
LOG_DIR="${WEBHOOK_BASE}/logs"
LOCK_DIR="${WEBHOOK_BASE}/locks"
STATUS_DIR="${WEBHOOK_BASE}/status"
REPO="hdot123/infraro-core"
TEAM_KEY="INFRA"

# 死锁出口 stale 阈值（architecture §3.2）：
# status=completed + sessionId 非空 + exitCode=0 而 Linear 停留非终态超过此阈值 → 推进终态
# 对齐 45 分钟量级（与现行 5t 超时保护一致）
DEADLOCK_STALE_THRESHOLD_MIN=45

# 死锁出口幂等 sentinel（HTML 隐藏评论，UI 不可见）
DEADLOCK_EXIT_SENTINEL_PREFIX="<!-- deadlock-exit "

# Stale orphan fallback threshold (INFRA-310 / #676):
# 无 status 文件的孤儿 issue（早于 status 机制存在）连续被 E4 阻挡 N 次后，
# 判定为 pre-status-mechanism 孤儿，执行 Linear canceled + 证据评论。
# N=5 对应 ~2.5h（30min/tick × 5）
STALE_ORPHAN_FALLBACK_TICKS=5

# Red PR Sweeper threshold (F2, VAL-SWEEP-002)
# Data source: artifacts/red-pr-sweep/distribution.json (P90 = 506.7 min)
# Rounded up: ⌈506.7⌉ = 507 min (8.45 hours)
# Rationale: 90% of red PRs are resolved within this time. Only the slowest 10%
# (typically PRs with droid-review failures or multi-fail cascades) exceed it.
# See stats.md §6.1 for nearest-rank derivation.
SWEEP_RED_PR_THRESHOLD_MINUTES=507

# New commit debounce window (VAL-SWEEP-006)
# Skip PRs with commits in the last 30 minutes (someone may be fixing)
SWEEP_NEW_COMMIT_WINDOW_MINUTES=30

# DRY_RUN=1 guard (M1 hemostasis, VAL-M1-001):
# When set, both cancel paths (failed-session + stale-orphan) only log intent
# and skip all GraphQL issueUpdate(cancel) calls.
DRY_RUN="${DRY_RUN:-0}"

# VAL-LEDGER-001 查重键集合（顶层预初始化避免 SC2154；填充点见 4b/4c 节）
# GAP_A_TERMINAL_REFS=锚点验证过的终态 ref 注册表（4b 终态关闭循环收集）
# LINEAR_REFS=非终态卡的 Linear ref 集（主查询投影）
GAP_A_TERMINAL_REFS=""
LINEAR_REFS=""

mkdir -p "$LOG_DIR" 2>/dev/null

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="${LOG_DIR}/reconcile-${TIMESTAMP}.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# GAP-A 补偿批次上限（VAL-LEDGER-004 缺口 / mission gap-a-batch-cap）：
# 单次运行补偿创建 ≤ cap。默认 2 与 R1_DAILY_BUDGET 对齐（终值 owner 在切强制
# 检查点裁定）；env 可调，非法值回退默认（fail-safe）。孤儿扫描全候选枚举 +
# 限量消费：超限孤儿本轮不创建（deferred，非丢弃——无墓碑语义，下个 tick 键面
# 重新判定）。批次上限属切强制三前置之一（孤儿✔ + 通道分离✔ + 批次上限）。
GAP_A_BATCH_CAP="${GAP_A_BATCH_CAP:-2}"
# 两段校验：① 非负整数（非数字回退）；② 上界 100——纯位数校验会放行超 int
# 上限值，后续 [ -ge ] 比较对巨值返回 rc=2 被 if 视为 false → cap 静默失效
# （fail-open）。长度前置守卫保证第二段的数值比较只见 ≤3 位数字，巨值走回退。
case "$GAP_A_BATCH_CAP" in
    ''|*[!0-9]*)
        log "WARNING: GAP_A_BATCH_CAP='${GAP_A_BATCH_CAP}' invalid, falling back to default 2"
        GAP_A_BATCH_CAP=2
        ;;
    *)
        if [ "${#GAP_A_BATCH_CAP}" -gt 3 ] || [ "$GAP_A_BATCH_CAP" -gt 100 ]; then
            log "WARNING: GAP_A_BATCH_CAP='${GAP_A_BATCH_CAP}' out of range (max 100), falling back to default 2"
            GAP_A_BATCH_CAP=2
        fi
        ;;
esac

# 锚点提取失败留痕（INFRA-357）：stderr 带时间戳写入 anchor-extract.log，
# 供事后 grep 退化痕迹；不影响调用方的 fail-closed 语义（空锚点照常 skip/block）
log_anchor_extract_err() {
    local target="$1" err_text="$2"
    [ -z "$err_text" ] && return 0
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] anchor-extract ${target}: ${line}" >> "${LOG_DIR}/anchor-extract.log"
    done <<< "$err_text"
}

# Red PR Sweeper function (F2, VAL-SWEEP-003~014)
# Checks if an open PR should be closed due to prolonged CI failure
# Returns 0 if sweeper took action, 1 otherwise
sweep_red_pr() {
    local pr_number="$1"
    local linear_ref="$2"
    
    # Debounce 1: Check if l2d-INFRA-*.lock exists
    local lock_file="${LOCK_DIR}/l2d-${linear_ref}.lock"
    if [ -f "$lock_file" ]; then
        log "  [SWEEP] PR #${pr_number}: l2d lock exists, skip (debounce)"
        return 1
    fi
    
    # Check PR age (created_at from GitHub)
    local pr_created_at
    pr_created_at=$(gh pr view "$pr_number" --repo "$REPO" --json createdAt --jq '.createdAt' 2>/dev/null || echo "")
    if [ -z "$pr_created_at" ]; then
        log "  [SWEEP] PR #${pr_number}: failed to fetch created_at, skip"
        return 1
    fi
    
    local pr_age_minutes
    # || true: head -n 1 early exit can SIGPIPE(141) the upstream producer under
    # pipefail; swallow the pipeline error and let the case guard map garbage to 9999
    pr_age_minutes=$("${PYTHON_BIN:-/opt/homebrew/bin/python3}" -c "
from datetime import datetime, timezone
try:
    created = datetime.fromisoformat('${pr_created_at}'.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    age_min = int((now - created).total_seconds() / 60)
    print(age_min)
except:
    print(9999)
" 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
    # Guard against empty or non-numeric values (prevents integer expression expected)
    case "${pr_age_minutes}" in
        ''|*[!0-9]*) pr_age_minutes=9999;;
    esac
    
    # Check if PR age exceeds threshold
    if [ "$pr_age_minutes" -lt "$SWEEP_RED_PR_THRESHOLD_MINUTES" ]; then
        log "  [SWEEP] PR #${pr_number}: age ${pr_age_minutes}min < threshold ${SWEEP_RED_PR_THRESHOLD_MINUTES}min, skip"
        return 1
    fi
    
    # Debounce 2: Check if last commit is recent (< 30 min)
    # Fix: pushedAt doesn't exist, use commits[-1].committedDate instead
    # Defense: trim and take first line to prevent multi-line integer comparison error
    local last_commit_at
    last_commit_at=$(gh pr view "$pr_number" --repo "$REPO" --json commits --jq '.commits[-1].committedDate' 2>/dev/null || echo "")
    if [ -n "$last_commit_at" ]; then
        local commit_age_minutes
        # || true: head -n 1 early exit can SIGPIPE(141) the upstream producer under
        # pipefail; swallow the pipeline error and let the case guard map garbage to 9999
        commit_age_minutes=$("${PYTHON_BIN:-/opt/homebrew/bin/python3}" -c "
from datetime import datetime, timezone
try:
    pushed = datetime.fromisoformat('${last_commit_at}'.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    age_min = int((now - pushed).total_seconds() / 60)
    print(age_min)
except:
    print(9999)
" 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
        # Guard against empty or non-numeric values (prevents integer expression expected)
        case "${commit_age_minutes}" in
            ''|*[!0-9]*) commit_age_minutes=9999;;
        esac
        
        if [ "$commit_age_minutes" -lt "$SWEEP_NEW_COMMIT_WINDOW_MINUTES" ]; then
            log "  [SWEEP] PR #${pr_number}: last commit ${commit_age_minutes}min ago (< ${SWEEP_NEW_COMMIT_WINDOW_MINUTES}min), skip (debounce)"
            return 1
        fi
    fi
    
    # Check CI status - must have failed checks
    # gh pr checks exit-code semantics (gh help exit-codes): 0=all passed,
    # 1=some failed, 8=pending. A failing PR exits 1 while still printing
    # valid JSON on stdout — the old `|| echo "[]"` fallback appended "[]"
    # AFTER that JSON, corrupting it so has_failure always parsed as 0 and
    # the sweeper never fired on real red PRs (INFRA-534). Fall back to []
    # only when stdout is empty (genuine fetch failure).
    # Fix: Use valid fields name,state,completedAt (not name,status,conclusion which don't exist)
    local checks_json
    checks_json=$(gh pr checks "$pr_number" --repo "$REPO" --json name,state,completedAt 2>/dev/null || true)
    if [ -z "$checks_json" ]; then
        checks_json="[]"
    fi
    
    local has_failure
    has_failure=$(echo "$checks_json" | "${PYTHON_BIN:-/opt/homebrew/bin/python3}" -c "
import json, sys
try:
    checks = json.load(sys.stdin)
    # Check if any check has state=='FAILURE' (not conclusion, which doesn't exist)
    has_failure = any(c.get('state') == 'FAILURE' for c in checks)
    print('1' if has_failure else '0')
except:
    print('0')
" 2>/dev/null || echo "0")
    
    if [ "$has_failure" != "1" ]; then
        log "  [SWEEP] PR #${pr_number}: no FAILURE checks, skip"
        return 1
    fi
    
    # All conditions met: comment first, then close if comment succeeds
    log "  [SWEEP] PR #${pr_number}: red PR detected (age=${pr_age_minutes}min, has FAILURE checks)"
    
    # Prepare comment body
    local comment_body="Red PR Sweeper (F2): This PR has been open for ${pr_age_minutes} minutes with CI failures.
Reference: ${linear_ref}
Threshold: ${SWEEP_RED_PR_THRESHOLD_MINUTES} minutes (P90 of red PR survival time)
Last commit: ${last_commit_at:-unknown}
Action: Closing to trigger re-dispatch by scanner.
Note: This is an automated cleanup. The issue will be re-discovered by the scanner and a new PR may be created."
    
    if [ "${DRY_RUN:-0}" = "1" ]; then
        log "  [SWEEP] [DRY_RUN] Would comment on PR #${pr_number}"
        log "  [SWEEP] [DRY_RUN] Would close PR #${pr_number}"
        return 0
    fi
    
    # Step 1: Comment on PR
    if ! gh pr comment "$pr_number" --repo "$REPO" --body "$comment_body" >> "$LOG_FILE" 2>&1; then
        log "  [SWEEP] PR #${pr_number}: comment failed, skip close (preserve for next tick)"
        return 1
    fi
    
    log "  [SWEEP] PR #${pr_number}: comment succeeded"
    
    # Step 2: Close PR
    if ! gh pr close "$pr_number" --repo "$REPO" >> "$LOG_FILE" 2>&1; then
        log "  [SWEEP] PR #${pr_number}: close failed"
        return 1
    fi
    
    log "  [SWEEP] PR #${pr_number}: closed successfully"
    
    # Step 3: Cleanup pending-ci file if exists (VAL-SWEEP-014)
    local pending_file="${LOCK_DIR}/pending-ci-${pr_number}.json"
    if [ -f "$pending_file" ]; then
        rm -f "$pending_file"
        log "  [SWEEP] PR #${pr_number}: cleaned up pending-ci file"
    fi
    
    return 0
}

# === 1. 解析 LINEAR_API_KEY（复用 trigger-droid.sh 的 op-mcp 机制）===
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/op-mcp.sh"

# === 共用派发账本（VAL-LEDGER-002）：GAP-A 补偿创建写账本 ===
# 补偿创建是新的派发事实源：linear_uuid 为主键（跨团队变号不变）+ 源 GH issue 号。
# 写失败仅 WARN 降级（账本是审计面不是补偿闸门）。lib 缺失（部署漂移）只禁用本
# tick 账本写入，不中断 reconcile 主流程——set -e 下 source 失败会直接退出（实证：
# `source X || fallback` 的 fallback 分支不执行），必须先文件存在性再 source。
_LEDGER_LIB_FILE="${SCRIPT_DIR}/lib/dispatch-ledger.sh"
if [ -f "$_LEDGER_LIB_FILE" ]; then
    # shellcheck source=/dev/null
    source "$_LEDGER_LIB_FILE"
else
    log "WARN: dispatch ledger lib unavailable (sync drift?), ledger writes disabled this tick"
fi

LINEAR_API_KEY="${LINEAR_API_KEY:-}"
if [ -z "$LINEAR_API_KEY" ]; then
    LINEAR_API_KEY=$(op_get_field "$OP_VAULT_SEVER" "uxsq45aoumghdysj3r5fe2fvqm" "凭据" || true)
fi

if [ -z "$LINEAR_API_KEY" ]; then
    log "ERROR: LINEAR_API_KEY is empty — cannot query Linear, aborting"
    exit 1
fi
export LINEAR_API_KEY

# === 2. 解析 TEAM_ID from repositories.yml ===
TEAM_ID=$(TEAM_KEY="$TEAM_KEY" /opt/homebrew/bin/python3 -c "
import yaml, os, sys
team_key = os.environ['TEAM_KEY']
with open(os.path.expanduser('~/.factory/config/repositories.yml')) as f:
    cfg = yaml.safe_load(f)
for td in cfg.get('teams', {}).values():
    if td.get('teamKey', '').upper() == team_key.upper():
        print(td.get('linearTeamId', ''))
        sys.exit(0)
print('')
" 2>/dev/null || echo "")

if [ -z "$TEAM_ID" ]; then
    log "ERROR: TEAM_ID not found for ${TEAM_KEY}, aborting"
    exit 1
fi

# === 3. 检查 per-team 锁是否空闲 ===
TEAM_LOCK="${LOCK_DIR}/l2d-team-${TEAM_KEY}.lock"
if [ -f "$TEAM_LOCK" ]; then
    LOCK_CONTENT=$(cat "$TEAM_LOCK" 2>/dev/null || echo "")
    if [ -n "$LOCK_CONTENT" ]; then
        LOCK_PID=$(echo "$LOCK_CONTENT" | cut -d: -f2)
        if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
            log "Team ${TEAM_KEY} droid still running (PID ${LOCK_PID}), skip reconciliation"
            exit 0
        else
            log "Team lock exists but PID ${LOCK_PID} is dead, proceeding"
        fi
    fi
fi

# === 4. 查询 Linear 中所有 evolution-found 且非终态的 Issue ===
# 查询 triage/backlog/unstarted 以及 started (In Progress) 状态。
# started 状态由 5t 超时保护处理，避免与刚启动的 droid 竞争。
LINEAR_ISSUES=$(TEAM_ID="$TEAM_ID" LINEAR_API_KEY="$LINEAR_API_KEY" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
team_id = os.environ['TEAM_ID']
api_key = os.environ['LINEAR_API_KEY']
# VAL-LEDGER-001：live 注册表（LINEAR_REFS/LINEAR_LIVE_TITLES）数据源同查重键面，
# first:50 截断会使 >50 张非终态卡被误判孤儿补偿（幽灵卡回归），after 游标
# 分页与终态查询同款（页数上限 50 防 API 异常死循环）
query = '''query (\$after: String, \$first: Int!) {
    issues(
        filter: {
            team: { id: { eq: \"''' + team_id + '''\" } },
            labels: { name: { containsIgnoreCase: \"evolution-found\" } },
            state: { type: { in: [\"triage\", \"backlog\", \"unstarted\", \"started\"] } }
        },
        orderBy: createdAt,
        first: \$first,
        after: \$after
    ) {
        nodes {
            id
            identifier
            title
            state { name type }
            updatedAt
        }
        pageInfo { hasNextPage endCursor }
    }
}'''
rows = []
after = None
try:
    for _page in range(50):
        payload = {'query': query, 'variables': {'first': 50, 'after': after}}
        req = urllib.request.Request(
            'https://api.linear.app/graphql',
            data=json.dumps(payload).encode(),
            headers={'Authorization': api_key, 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode())
        conn = d.get('data', {}).get('issues', {}) or {}
        nodes = conn.get('nodes', []) or []
        for n in nodes:
            ref = n.get('identifier', '')
            title = n.get('title', '').replace('|', '-')
            uuid = n.get('id', '')
            state = n.get('state', {}).get('name', '')
            updated_at = n.get('updatedAt', '')
            rows.append(f'{uuid}|{ref}|{title}|{state}|{updated_at}')
        pi = conn.get('pageInfo', {}) or {}
        if not pi.get('hasNextPage'):
            break
        cursor = pi.get('endCursor')
        # 静默截断=部分数据被当全集（live 注册表缺项=误判孤儿补偿）：
        # hasNextPage=true 而游标无法前进（缺失/停滞），或 50 页上限耗尽，
        # 都必须显式失败（fail-closed），不得带残缺注册表继续
        if not cursor or cursor == after:
            print('ERROR: pagination cursor stalled with hasNextPage=true', file=sys.stderr)
            sys.exit(1)
        after = cursor
    else:
        print('ERROR: pagination page cap (50) exhausted with more pages pending', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    # 部分数据不可信（live 注册表不完整=误判孤儿），与旧查询同语义 fail-fast
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
print('\n'.join(rows), end='')
" 2>>"$LOG_FILE") || {
    log "ERROR: Linear API query failed, aborting"
    exit 1
}

ISSUE_COUNT=$(echo "$LINEAR_ISSUES" | grep -c '|' 2>/dev/null || true)
ISSUE_COUNT=${ISSUE_COUNT:-0}
ISSUE_COUNT=$(echo "$ISSUE_COUNT" | tr -d '[:space:]')
log "Found ${ISSUE_COUNT} non-terminal evolution-found issues in Linear"

# === 4b. 查询 Linear 中终态（Done/Cancelled）的 evolution-found Issue，关闭对应 GitHub Issue ===
# VAL-LEDGER-001：状态集扩为全部非取消即终态（duplicate 缺失时镜像永不收敛）；
# after 游标内部分页修复 first:50 截断（页数上限防 API 异常死循环）。
TERMINAL_ISSUES=$(TEAM_ID="$TEAM_ID" LINEAR_API_KEY="$LINEAR_API_KEY" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
team_id = os.environ['TEAM_ID']
api_key = os.environ['LINEAR_API_KEY']
query = '''query (\$after: String, \$first: Int!) {
    issues(
        filter: {
            team: { id: { eq: \"''' + team_id + '''\" } },
            labels: { name: { containsIgnoreCase: \"evolution-found\" } },
            state: { type: { in: [\"completed\", \"canceled\", \"duplicate\"] } }
        },
        orderBy: createdAt,
        first: \$first,
        after: \$after
    ) {
        nodes {
            identifier
            title
            state { name type }
        }
        pageInfo { hasNextPage endCursor }
    }
}'''
rows = []
after = None
try:
    for _page in range(50):
        payload = {'query': query, 'variables': {'first': 50, 'after': after}}
        req = urllib.request.Request(
            'https://api.linear.app/graphql',
            data=json.dumps(payload).encode(),
            headers={'Authorization': api_key, 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode())
        conn = d.get('data', {}).get('issues', {}) or {}
        nodes = conn.get('nodes', []) or []
        for n in nodes:
            ref = n.get('identifier', '')
            title = n.get('title', '').replace('|', '-')
            state = n.get('state', {}).get('name', '')
            rows.append(f'{ref}|{title}|{state}')
        pi = conn.get('pageInfo', {}) or {}
        if not pi.get('hasNextPage'):
            break
        cursor = pi.get('endCursor')
        # 静默截断=部分数据被当全集（终态注册表缺项=幽灵卡回归）：
        # hasNextPage=true 而游标停滞/缺失，或页数上限耗尽，都必须显式失败，
        # rc 传播给 bash 侧 TERMINAL_FAILED 标记（下游 fail-closed 消费）
        if not cursor or cursor == after:
            print('ERROR: terminal pagination cursor stalled with hasNextPage=true', file=sys.stderr)
            sys.exit(1)
        after = cursor
    else:
        print('ERROR: terminal pagination page cap (50) exhausted with more pages pending', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    # 部分数据不可信（终态注册表不完整=幽灵卡回归）：丢弃部分页 + 显式失败，
    # rc 传播给 bash 侧 TERMINAL_FAILED 标记（下游 fail-closed 消费）
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
print('\n'.join(rows), end='')
" 2>>"$LOG_FILE") || TERMINAL_QUERY_RC=$?
if [ "${TERMINAL_QUERY_RC:-0}" -ne 0 ]; then
    # VAL-LEDGER-001：终态注册表构建失败时 GAP-A 键面不完整（fail-open 风险），
    # 标记后由 GAP-A 侧整体跳过孤儿判定（fail-closed；关闭镜像循环照常执行）
    TERMINAL_QUERY_FAILED=1
    log "WARNING: terminal-state query failed (rc=${TERMINAL_QUERY_RC}) — GAP-A orphan judgement will be skipped (fail-closed)"
else
    TERMINAL_QUERY_FAILED=0
fi

TERMINAL_COUNT=$(echo "$TERMINAL_ISSUES" | grep -c '|' 2>/dev/null || true)
TERMINAL_COUNT=${TERMINAL_COUNT:-0}
TERMINAL_COUNT=$(echo "$TERMINAL_COUNT" | tr -d '[:space:]')
log "Found ${TERMINAL_COUNT} terminal-state evolution-found issues in Linear"

if [ "$TERMINAL_COUNT" -gt 0 ]; then
    # VAL-LEDGER-001：锚点验证过的终态 ref 注册表（GAP-A 查重键数据源）
    GAP_A_TERMINAL_REFS=""
    while IFS='|' read -r T_REF T_TITLE T_STATE; do
        [ -z "$T_REF" ] && continue
        log "Checking terminal ${T_REF}: ${T_TITLE} (state: ${T_STATE})"

        # 查找对应的 open GitHub Issue（带 label 双闸）
        GH_ISSUE_JSON=$(gh issue list --repo "$REPO" --label evolution-found --state open --search "${T_REF}" --json number --limit 1 2>/dev/null || echo "[]")
        GH_NUMBER=$(echo "$GH_ISSUE_JSON" | /opt/homebrew/bin/python3 -c "import json,sys; data=json.load(sys.stdin); print(data[0]['number'] if data else '')" 2>/dev/null || echo "")

        if [ -n "$GH_NUMBER" ]; then
            # 锚点一致性校验（issue-flow.md §9.4）：提取评论中的 linear-linkback，必须 == T_REF
            # 失败留痕 anchor-extract.log（INFRA-357），fail-closed 语义不变
            ANCHOR_ERR="$(mktemp)"
            ANCHOR=$(/opt/homebrew/bin/python3 "$SCRIPT_DIR/extract_anchor.py" issue "$GH_NUMBER" "$REPO" 2>"$ANCHOR_ERR" || echo "")
            log_anchor_extract_err "issue#${GH_NUMBER}" "$(cat "$ANCHOR_ERR")"
            rm -f "$ANCHOR_ERR"
            if [ "$ANCHOR" = "$T_REF" ]; then
                # VAL-LEDGER-001：锚点验证过的终态 ref 注册表（GAP-A 查重键数据源）；
                # 先于 close 收集——close 失败也不影响「Linear 已有终态记录」事实
                GAP_A_TERMINAL_REFS="${GAP_A_TERMINAL_REFS} ${T_REF}"
                log "  ${T_REF}: anchor consistent, closing GitHub Issue #${GH_NUMBER} (Linear state: ${T_STATE})"
                gh issue close "$GH_NUMBER" --repo "$REPO" \
                    --comment "对应的 Linear Issue ${T_REF} 已处于终态（${T_STATE}），本 GitHub Issue 由 compensation-layer 清理关闭。" \
                    2>>"$LOG_FILE" || log "  WARNING: failed to close GitHub Issue #${GH_NUMBER}"
            elif [ -z "$ANCHOR" ]; then
                log "  ${T_REF}: WARNING no anchor in GitHub Issue #${GH_NUMBER} — skip close (fail-closed, drift record)"
                # 漂移守望记录（issue-flow.md §9.4）
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRIFT: ${T_REF} GitHub Issue #${GH_NUMBER} missing anchor" >> "${LOG_DIR}/anchor-drift.log"
            else
                log "  ${T_REF}: WARNING anchor mismatch (expected ${T_REF}, got ${ANCHOR}) in GitHub Issue #${GH_NUMBER} — skip close (fail-closed)"
                # 漂移守望记录
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRIFT: ${T_REF} GitHub Issue #${GH_NUMBER} anchor mismatch (got ${ANCHOR})" >> "${LOG_DIR}/anchor-drift.log"
            fi
        else
            log "  ${T_REF}: no open GitHub Issue found, skip"
        fi
    done <<< "$TERMINAL_ISSUES"
fi

if [ -n "$LINEAR_ISSUES" ]; then
    LINEAR_REFS=$(echo "$LINEAR_ISSUES" | awk -F'|' 'NF>=2 {print $2}')
fi
# === GAP-A (INFRA-174): GitHub→Linear 反向对账 — 检测 Linear 同步失败 ===
# 当 Linear native GitHub 集成同步失败（API 限流、集成故障）时，evolution scanner
# 创建的 GitHub Issue 在 Linear 中无对应记录，Linear webhook 永不触发、droid 永不处理。
# 本节通过反向对账（GitHub→Linear）检测孤立 Issue 并补偿创建 Linear Issue。
#
# 注意：必须在下面的 ISSUE_COUNT==0 提前退出检查之前执行 —— 孤立 Issue 意味着
# Linear 无记录，ISSUE_COUNT 可能为 0，否则会跳过本节。
GAP_A_THRESHOLD_MIN=30

GH_EVOLUTION_ISSUES=$(gh issue list --repo "$REPO" --label evolution-found --state open \
    --json number,title,createdAt --limit 50 2>>"$LOG_FILE" || echo "[]")

GH_EVOLUTION_COUNT=$(echo "$GH_EVOLUTION_ISSUES" | /opt/homebrew/bin/python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")
GH_EVOLUTION_COUNT=$(echo "$GH_EVOLUTION_COUNT" | tr -d '[:space:]')

if [ "$GH_EVOLUTION_COUNT" -gt 0 ]; then
    if [ "$TERMINAL_QUERY_FAILED" -ne 0 ]; then
        # VAL-LEDGER-001 fail-closed：终态注册表构建失败 → 键面不完整，
        # 孤儿判定（④ 兜底补偿）整体跳过，下个 tick 键面恢复再判。
        # 幽灵卡工厂正是「注册表缺项时锚点在的镜像被误判孤儿」的产物。
        log "GAP-A: terminal registry incomplete (query failed) — skip orphan judgement entirely (fail-closed, retry next tick)"
    else
    log "GAP-A: checking ${GH_EVOLUTION_COUNT} open evolution-found GitHub issues for Linear sync"

    # VAL-LEDGER-001：标题集合（仅作「锚点缺失时」的非补偿信号；
    # 查重正键=锚点（GH issue 号回链），标题永不作为肯定键）
    LINEAR_TERMINAL_TITLES_TMP=$(mktemp 2>/dev/null || echo "/tmp/gap-a-linear-terminal-titles.$$")
    echo "$TERMINAL_ISSUES" | awk -F'|' 'NF>=2 {print $2}' > "$LINEAR_TERMINAL_TITLES_TMP" 2>/dev/null || true
    LINEAR_LIVE_TITLES_TMP=$(mktemp 2>/dev/null || echo "/tmp/gap-a-linear-live-titles.$$")
    echo "$LINEAR_ISSUES" | awk -F'|' 'NF>=3 {print $3}' > "$LINEAR_LIVE_TITLES_TMP" 2>/dev/null || true

    # 枚举全部孤儿候选（批次上限消费前提）：锚点（linear-linkback 的 INFRA-xxx，=GH issue 号回链）
    # 不在终态注册表、也不在非终态 ref 集，且 age > 阈值
    GAP_A_ORPHAN=$(LINEAR_TERMINAL_TITLES_TMP="$LINEAR_TERMINAL_TITLES_TMP" \
        LINEAR_LIVE_TITLES_TMP="$LINEAR_LIVE_TITLES_TMP" REPO="$REPO" \
        GAP_A_TERMINAL_REFS="$GAP_A_TERMINAL_REFS" LINEAR_REFS="$LINEAR_REFS" \
        GAP_A_THRESHOLD_MIN="$GAP_A_THRESHOLD_MIN" \
        SCRIPT_DIR="$SCRIPT_DIR" \
        /opt/homebrew/bin/python3 -c "
import json, os, subprocess, sys
from datetime import datetime, timezone

# 从 stdin 读取 GitHub issues JSON
try:
    issues = json.load(sys.stdin)
except Exception:
    issues = []

threshold = int(os.environ.get('GAP_A_THRESHOLD_MIN', '30'))
now = datetime.now(timezone.utc)

repo = os.environ.get('REPO', '')
titles_path = os.environ.get('LINEAR_TERMINAL_TITLES_TMP', '')
terminal_titles = set()
try:
    with open(titles_path) as f:
        terminal_titles = {line.strip() for line in f if line.strip()}
except Exception:
    pass
live_titles_path = os.environ.get('LINEAR_LIVE_TITLES_TMP', '')
live_titles = set()
try:
    with open(live_titles_path) as f:
        live_titles = {line.strip() for line in f if line.strip()}
except Exception:
    pass

# VAL-LEDGER-001：查重正键=锚点（GH issue 号回链，经 extract_anchor 等值验证）
terminal_refs = set(os.environ.get('GAP_A_TERMINAL_REFS', '').split())
live_refs = set(os.environ.get('LINEAR_REFS', '').split())
script_dir = os.environ.get('SCRIPT_DIR', '')

def get_anchor(number):
    # rc=0+空输出=真无锚点；rc≠0/异常=提取失败（gh 故障/超时），与「无锚点」
    # 必须区分：提取失败返回 None → SKIP_ANCHOR_UNKNOWN（fail-closed，不补偿）
    try:
        r = subprocess.run(
            [os.path.join(script_dir, 'extract_anchor.py'), 'issue', str(number), repo],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return None
        return (r.stdout or '').strip()
    except Exception:
        return None

for issue in issues:
    title = issue.get('title', '')
    number = issue.get('number')
    created = issue.get('createdAt', '')
    if number is None or not title:
        continue
    try:
        ca = datetime.fromisoformat(created.replace('Z', '+00:00'))
        age_min = (now - ca).total_seconds() / 60
    except Exception:
        continue
    if age_min <= threshold:
        continue

    anchor = get_anchor(number)
    # ⓪ 提取失败（gh 故障/超时）≠ 无锚点：fail-closed 跳过本候选，
    # 绝不让提取失败流入 ④（无键=孤儿）补偿路径
    if anchor is None:
        print(f'SKIP_ANCHOR_UNKNOWN\\t{number}\\tanchor extraction failed (fail-closed)')
        continue
    # ① 锚点 ∈ 终态注册表 → 已有终态记录，绝不补偿（幽灵卡工厂修复点）
    if anchor and anchor in terminal_refs:
        print(f'SKIP_TERMINAL\\t{number}\\tanchored to {anchor} (terminal registry)')
        continue
    # ② 锚点 ∈ 非终态 ref 集 → live 双胞胎存在，不重补偿
    if anchor and anchor in live_refs:
        print(f'SKIP_LIVE\\t{number}\\tanchored to {anchor} (live)')
        continue
    # ②b 锚点存在但不在任何注册表（live 集分页截断/4b search miss/注册表
    # best-effort 缺项）→ 键面未知：fail-closed skip。旧 has_linkback 子串
    # 检查在此场景保守跳过；锚点键化后必须显式恢复同语义，否则注册表任何
    # 缺项都会把已锚定镜像重判孤儿 → 幽灵卡工厂回归
    if anchor and anchor not in terminal_refs and anchor not in live_refs:
        print(f'SKIP_ANCHOR_UNKNOWN\\t{number}\\tanchor {anchor} not in terminal/live registries (fail-closed)')
        continue
    # ③ 锚点缺失（无键）→ 标题降级为非补偿信号（终态标题命中即 skip）；
    #    标题永不作为补偿的肯定键（标题碰撞=旧键的根缺陷）
    if not anchor and title in terminal_titles:
        print(f'SKIP_TERMINAL_TITLE\\t{number}')
        continue
    if not anchor and title in live_titles:
        print(f'SKIP_LIVE_TITLE\\t{number}')
        continue
    # ④ 无终态键、无 live 键 → 孤儿，输出 number<TAB>title（全候选枚举：
    # 批次上限是「枚举全集后的限量消费」，首行即停会让 cap 只对第一个可见
    # 孤儿生效，且 SKIP_* 行遮蔽后续孤儿=补偿饥饿旧语义回潮）
    print(f'{number}\\t{title}')
" <<< "$GH_EVOLUTION_ISSUES" 2>>"$LOG_FILE" || echo "")

    rm -f "$LINEAR_TERMINAL_TITLES_TMP" "$LINEAR_LIVE_TITLES_TMP" 2>/dev/null || true

    # VAL-LEDGER-001：python 一次扫描输出多行（SKIP_* 若干行 + 全部孤儿行），
    # 全候选枚举（批次上限的前置：先枚举全集再限量消费）。bash 侧消费规则：
    # 孤儿判定只认非 SKIP 行（逐行过滤，head -1 会把排在 SKIP 后面的真孤儿
    # 整行吞掉——SKIP 候选长期滞留时孤儿每 tick 被丢弃、补偿饥饿）；
    # SKIP_* 行=键面命中，逐条留痕。
    GAP_A_SKIP_LINES=$(echo "$GAP_A_ORPHAN" | grep "^SKIP_" || true)
    GAP_A_ORPHANS=$(echo "$GAP_A_ORPHAN" | grep -v "^SKIP_" || true)

    if [ -n "$GAP_A_SKIP_LINES" ]; then
        while IFS= read -r SKIP_LINE; do
            SKIP_NUM=$(echo "$SKIP_LINE" | cut -f2)
            SKIP_WHY=$(echo "$SKIP_LINE" | cut -f3)
            log "GAP-A: GitHub Issue #${SKIP_NUM} ${SKIP_WHY:-skip} — skip orphan judgement (dedup key hit)"
        done <<< "$GAP_A_SKIP_LINES"
    fi

    # VAL-LEDGER-001 防线 2（bash 侧复核，POSIX 兼容词等值匹配，逐行）：
    # python ④ 判定只对无锚点候选输出孤儿行，因此幸存孤儿若在 bash 侧提取
    # 出非空锚点 = python/bash 键面判定矛盾（环境传递故障/脚本漂移）。
    # 注册表命中 → 等价 SKIP：该行必须移出补偿集（只留痕不移除 = 已锚定镜像
    # 照常补偿 → 幽灵卡工厂回归，#281 双保险语义）；锚点未知 → 该行 fail-closed
    # 剔除并留痕。两种剔除均单行过滤逐行重建（BSD grep 对 -vxF 多行 pattern
    # 参数不保证按行切分，GNU-only 后置过滤在生产 macOS 上静默失配 = fail-open）。
    if [ -n "$GAP_A_ORPHANS" ]; then
        GAP_A_ORPHANS_REBUILT=""
        while IFS= read -r CAND_LINE; do
            [ -z "$CAND_LINE" ] && continue
            CAND_NUM=$(echo "$CAND_LINE" | cut -f1)
            CAND_ANCHOR=$(/opt/homebrew/bin/python3 "$SCRIPT_DIR/extract_anchor.py" issue "$CAND_NUM" "$REPO" 2>/dev/null || echo "")
            if [ -n "$CAND_ANCHOR" ]; then
                # shellcheck disable=SC2086  # 注册表为空格分隔的 ref 词列表，词分割即遍历语义
                if printf '%s\n' $GAP_A_TERMINAL_REFS | grep -qx "$CAND_ANCHOR" || \
                   echo "$LINEAR_REFS" | grep -qw "$CAND_ANCHOR"; then
                    log "GAP-A: GitHub Issue #${CAND_NUM} anchored to ${CAND_ANCHOR} — bash-side dedup key hit, skip compensation"
                    continue
                fi
                log "GAP-A: CONTRADICTION — orphan #${CAND_NUM} has anchor ${CAND_ANCHOR} unknown to registries (python/bash key-face disagreement), exclude this orphan (fail-closed)"
                continue
            fi
            GAP_A_ORPHANS_REBUILT="${GAP_A_ORPHANS_REBUILT}${CAND_LINE}
"
        done <<< "$GAP_A_ORPHANS"
        GAP_A_ORPHANS=$(printf '%s' "$GAP_A_ORPHANS_REBUILT")
    fi

    # 批次上限计数（VAL-LEDGER-004）：全候选枚举后的限量消费
    GAP_A_TOTAL_ORPHANS=0
    if [ -n "$GAP_A_ORPHANS" ]; then
        GAP_A_TOTAL_ORPHANS=$(printf '%s\n' "$GAP_A_ORPHANS" | grep -c . || true)
    fi
    GAP_A_COMPENSATED=0
    GAP_A_DEFERRED=0

    if [ "$GAP_A_TOTAL_ORPHANS" -gt 0 ]; then
        while IFS= read -r O_LINE; do
            [ -z "$O_LINE" ] && continue
            O_NUMBER=$(echo "$O_LINE" | cut -f1)
            O_TITLE=$(echo "$O_LINE" | cut -f2-)
            # 超限即停创建：剩余孤儿逐条 deferred 留痕（非丢弃——无墓碑语义，
            # 卡保持 open，下个 tick 键面重新判定）
            if [ "$GAP_A_COMPENSATED" -ge "$GAP_A_BATCH_CAP" ]; then
                GAP_A_DEFERRED=$((GAP_A_DEFERRED + 1))
                log "GAP-A: batch cap ${GAP_A_BATCH_CAP} reached — GitHub Issue #${O_NUMBER} deferred to next tick (batch summary at end of run)"
                continue
            fi
            log "GAP-A: orphan detected — GitHub Issue #${O_NUMBER} has no Linear sync, compensating"

        # 补偿创建 Linear Issue（解析 evolution-found 标签 → issueCreate → 回写评论）
        GAP_A_RESULT=$(TEAM_ID="$TEAM_ID" LINEAR_API_KEY="$LINEAR_API_KEY" \
            O_NUMBER="$O_NUMBER" O_TITLE="$O_TITLE" REPO="$REPO" \
            /opt/homebrew/bin/python3 -c "
import json, os, sys, urllib.request

team_id = os.environ['TEAM_ID']
api_key = os.environ['LINEAR_API_KEY']
o_number = os.environ['O_NUMBER']
o_title = os.environ['O_TITLE']
repo = os.environ['REPO']
gh_url = f'https://github.com/{repo}/issues/{o_number}'

def gql(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps(payload).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

# 1. 解析 evolution-found 标签 ID
label_query = '''query (\$tid: String!) {
    team(id: \$tid) {
        labels(filter: { name: { eq: \"evolution-found\" } }) {
            nodes { id name }
        }
    }
}'''
label_data = gql(label_query, {'tid': team_id})
nodes = label_data.get('data', {}).get('team', {}).get('labels', {}).get('nodes', [])
label_id = nodes[0]['id'] if nodes else None

# 2. 创建 Linear Issue
# VAL-LEDGER-001：desc 显式携带源 GH issue 号（锚点回链的 Linear 侧可见面）
o_number = os.environ.get('O_NUMBER', '')
desc = (
    f'## Compensation 创建（GAP-A / INFRA-174）\\n\\n'
    f'Linear native GitHub 集成未能自动同步此 Issue，由 reconcile-evolution.sh 反向对账补偿创建。\\n\\n'
    f'**GitHub Issue**: {gh_url}\\n'
    f'**GitHub Issue #**: {o_number}\\n'
    f'**Title**: {o_title}\\n\\n'
    f'请参考 GitHub Issue 获取完整 finding 详情。'
)
create_mut = '''mutation (\$tid: String!, \$title: String!, \$desc: String!, \$labels: [String!]) {
    issueCreate(input: {
        teamId: \$tid,
        title: \$title,
        description: \$desc,
        labelIds: \$labels
    }) {
        issue { id identifier url }
    }
}'''
create_vars = {'tid': team_id, 'title': o_title, 'desc': desc, 'labels': [label_id] if label_id else []}
create_data = gql(create_mut, create_vars)
issue_node = create_data.get('data', {}).get('issueCreate', {}).get('issue', {})
if not issue_node:
    errs = create_data.get('errors', [])
    print('ERROR: ' + json.dumps(errs, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)

linear_uuid = issue_node['id']
linear_ref = issue_node['identifier']

# 3. 回写评论关联 GitHub Issue
comment_mut = '''mutation (\$iid: String!, \$body: String!) {
    commentCreate(input: { issueId: \$iid, body: \$body }) {
        comment { id }
    }
}'''
comment_body = f'GitHub Issue: {gh_url}（补偿创建，Linear 集成同步失败）'
gql(comment_mut, {'iid': linear_uuid, 'body': comment_body})

# VAL-LEDGER-001：输出带源 GH issue 号（驱动锚点评论回写与日志关联）
print(f'{linear_ref}|{linear_uuid}|{o_number}')
" 2>>"$LOG_FILE" || echo "")

        if [ -n "$GAP_A_RESULT" ]; then
            G_LINEAR_REF=$(echo "$GAP_A_RESULT" | cut -d'|' -f1)
            G_O_NUMBER=$(echo "$GAP_A_RESULT" | cut -d'|' -f3)
            # 两字段形态（|{linear_ref}|{linear_uuid}）兼容：f3 为空时回退本次孤儿号
            G_O_NUMBER="${G_O_NUMBER:-$O_NUMBER}"
            log "GAP-A: created Linear Issue ${GAP_A_RESULT} for GitHub #${G_O_NUMBER} (next tick will trigger droid)"

            # VAL-LEDGER-001：向 GitHub 镜像回写锚点评论（<!-- linear-linkback 新ref -->，
            # 键自愈——下个 tick 起本卡可被锚点查重命中；并发已写场景幂等跳过）
            if [ -n "$G_O_NUMBER" ] && [ -n "$G_LINEAR_REF" ]; then
                O_ANCHOR=$(/opt/homebrew/bin/python3 "$SCRIPT_DIR/extract_anchor.py" issue "$G_O_NUMBER" "$REPO" 2>/dev/null || echo "")
                if [ -n "$O_ANCHOR" ]; then
                    log "GAP-A: anchor already present (#${G_O_NUMBER}: ${O_ANCHOR}), skip comment write-back (idempotent)"
                else
                    ANCHOR_FILE=$(mktemp 2>/dev/null || echo "/tmp/gap-a-anchor-comment.$$")
                    printf '<!-- linear-linkback %s -->\nGAP-A 补偿创建锚点（源 GitHub Issue #%s，VAL-LEDGER-001 查重键）\n' "$G_LINEAR_REF" "$G_O_NUMBER" > "$ANCHOR_FILE"
                    if gh issue comment "$G_O_NUMBER" --repo "$REPO" --body-file "$ANCHOR_FILE" 2>>"$LOG_FILE"; then
                        log "GAP-A: anchor comment written to GitHub #${G_O_NUMBER} (linkback ${G_LINEAR_REF})"
                    else
                        log "GAP-A: WARNING — anchor comment write-back failed for GitHub #${G_O_NUMBER}"
                    fi
                    rm -f "$ANCHOR_FILE" 2>/dev/null || true
                fi
            fi

            # === 共用派发账本（VAL-LEDGER-002）：GAP-A 补偿创建写账本 ===
            # 补偿创建是新的派发事实源：linear_uuid 主键（跨团队变号不变）+ 源 GH issue 号。
            # kind=gap-a-compensation 区分来源（去重判定只认 source=trigger-droid 的
            # 成功记录，补偿记录永不构成派发去重命中）；事件名 compensation_created。
            # rc!=0 均不中断补偿流程（set -e 面：if/elif 吞 rc；rc=2 WARN 降级——
            # 账本是审计面非补偿闸门）。
            G_LINEAR_UUID=$(echo "$GAP_A_RESULT" | cut -d'|' -f2)
            if ledger_claim "$G_LINEAR_UUID" "reconcile-gap-a" "$G_LINEAR_REF" "${O_TITLE:0:200}" "$TEAM_KEY" "gap-a-compensation" "$G_O_NUMBER"; then
                ledger_append "$G_LINEAR_UUID" "$G_LINEAR_REF" "compensation_created" "ghIssue=${G_O_NUMBER}"
            elif [ $? -eq 2 ]; then
                log "GAP-A: WARN — dispatch ledger unavailable, compensation record not persisted (uuid=${G_LINEAR_UUID} gh=#${G_O_NUMBER})"
            fi
            # VAL-LEDGER-004：批次计数（在补偿动作完成后递增，进度日志随创建留痕）
            GAP_A_COMPENSATED=$((GAP_A_COMPENSATED + 1))
            log "GAP-A: batch progress ${GAP_A_COMPENSATED}/${GAP_A_BATCH_CAP} compensated this run"
        else
            log "GAP-A: WARNING — Linear issue creation failed for GitHub #${O_NUMBER}"
        fi
        done <<< "$GAP_A_ORPHANS"
        log "GAP-A: batch summary — cap=${GAP_A_BATCH_CAP} compensated=${GAP_A_COMPENSATED} deferred=${GAP_A_DEFERRED} total_orphans=${GAP_A_TOTAL_ORPHANS}"
    else
        log "GAP-A: no orphaned GitHub issues detected"
        log "GAP-A: batch summary — cap=${GAP_A_BATCH_CAP} compensated=0 deferred=0 total_orphans=0"
    fi
    fi
else
    log "GAP-A: no open evolution-found GitHub issues to check"
fi

if [ "$ISSUE_COUNT" -eq 0 ]; then
    log "Nothing to reconcile"
    exit 0
fi

# === 5. 对每个 Linear Issue 检查是否已有 PR 或正在处理 ===
TRIGGERED=0

while IFS='|' read -r ISSUE_UUID LINEAR_REF ISSUE_TITLE ISSUE_STATE ISSUE_UPDATED; do
    [ -z "$LINEAR_REF" ] && continue

    log "Checking ${LINEAR_REF}: ${ISSUE_TITLE} (state: ${ISSUE_STATE})"

    # 5t. 对 In Progress (started) 状态的 Issue 应用超时保护，避免与刚启动的 droid 竞争
    if echo "$ISSUE_STATE" | grep -q "进行"; then
        AGE_MIN=$(/opt/homebrew/bin/python3 -c "
from datetime import datetime, timezone
try:
    updated = datetime.fromisoformat('${ISSUE_UPDATED}'.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    age_min = int((now - updated).total_seconds() / 60)
    print(age_min)
except:
    print(9999)
" 2>/dev/null || echo "9999")

        STALE_THRESHOLD_MIN=45
        if [ "$AGE_MIN" -lt "$STALE_THRESHOLD_MIN" ]; then
            log "  ${LINEAR_REF}: In Progress but updated ${AGE_MIN}min ago (< ${STALE_THRESHOLD_MIN}min threshold), skip (timeout guard)"
            continue
        fi
        log "  ${LINEAR_REF}: In Progress and stale (${AGE_MIN}min > ${STALE_THRESHOLD_MIN}min threshold), checking for retrigger"
    fi

    # 5a. 检查是否有 open PR 引用该 Linear ref
    PR_FOUND=$(gh pr list --repo "$REPO" --search "${LINEAR_REF}" --state open --limit 5 --json number 2>/dev/null | /opt/homebrew/bin/python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")

    if [ "$PR_FOUND" -gt 0 ]; then
        log "  ${LINEAR_REF}: PR exists, skip"
        
        # === Red PR Sweeper (F2, VAL-SWEEP-002~014) ===
        # Check each open PR for this Linear ref to see if it's a "red PR"
        # (open + CI failure + age > threshold). If so, comment and close.
        log "  ${LINEAR_REF}: Running Red PR Sweeper check..."
        
        # Get list of open PR numbers
        PR_NUMBERS=$(gh pr list --repo "$REPO" --search "${LINEAR_REF}" --state open --limit 5 --json number --jq '.[].number' 2>/dev/null || echo "")
        
        for pr_number in $PR_NUMBERS; do
            # Call sweeper function; guarded so a "no action / debounce"
            # return code (1) does not abort the whole reconcile loop
            # under `set -e` (INFRA-534). Only a real exit (crash) propagates.
            if ! sweep_red_pr "$pr_number" "$LINEAR_REF"; then
                log "  ${LINEAR_REF}: Red PR #${pr_number} check completed (no action or debounce)"
            else
                log "  ${LINEAR_REF}: Red PR #${pr_number} was closed by sweeper"
            fi
        done
        
        continue
    fi

    # 5a2. 检查 GitHub Issue 是否已关闭（若已关闭则跳过 droid 触发）
    GH_OPEN_JSON=$(gh issue list --repo "$REPO" --search "${LINEAR_REF}" --state open --json number --limit 1 2>/dev/null || echo "[]")
    GH_OPEN_COUNT=$(echo "$GH_OPEN_JSON" | /opt/homebrew/bin/python3 -c "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")

    if [ "$GH_OPEN_COUNT" -eq 0 ]; then
        log "  ${LINEAR_REF}: GitHub Issue already closed (droid already processed)"

        # Terminal absorption: move Linear issue to terminal state
        # When GitHub mirror is closed, Linear issue should also be in terminal state
        # to prevent repeated empty retrigger attempts on subsequent ticks

        # Idempotency check: skip if already in terminal state
        # VAL-LEDGER-001：cancel 墓碑检查（第三处 cancel 点补缺——若 fallback
        # 路径已 cancel 过本卡（哨兵在），不再重复执行 terminal absorption）
        TOMBSTONE_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
        if [ -f "$TOMBSTONE_FILE" ]; then
            log "  ${LINEAR_REF}: tombstone found (already canceled by fallback), skip absorption (idempotent)"
            continue
        fi
        CURRENT_STATE=$(/opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = '$ISSUE_UUID'

if not api_key or not issue_uuid:
    print('unknown')
    sys.exit(0)

query = '{ issue(id: \"' + issue_uuid + '\") { state { type } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': query}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        state_type = d.get('data', {}).get('issue', {}).get('state', {}).get('type', 'unknown')
        print(state_type)
except Exception:
    print('unknown')
" 2>>"$LOG_FILE" || echo "unknown")

        if [ "$CURRENT_STATE" = "completed" ] || [ "$CURRENT_STATE" = "canceled" ]; then
            log "  ${LINEAR_REF}: already in terminal state (${CURRENT_STATE}), skip absorption"
            continue
        fi

        # Move to terminal state (reuse deadlock exit pattern)
        # Use "canceled" state since no actual work was done (GitHub mirror already closed)
        # M3 (NB-1): DRY_RUN guard - only log, don't execute Linear changes
        if [ "${DRY_RUN:-0}" = "1" ]; then
            log "  ${LINEAR_REF}: [DRY_RUN] would execute terminal absorption (GitHub mirror closed, move to canceled)"
            log "  ${LINEAR_REF}: [DRY_RUN] tombstone will be written after absorption succeeds"
            continue
        fi
        ABSORB_RESULT=$(ISSUE_UUID="$ISSUE_UUID" \
            LINEAR_REF="$LINEAR_REF" \
            TEAM_ID="$TEAM_ID" \
            /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = os.environ['ISSUE_UUID']
linear_ref = os.environ['LINEAR_REF']
team_id = os.environ['TEAM_ID']

def gql(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps(payload).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

# 1. Query team states to find canceled state ID
team_query = '''query(\$teamId: String!) {
    team(id: \$teamId) {
        states {
            nodes { id name type }
        }
    }
}'''
team_data = gql(team_query, {'teamId': team_id})
team_node = team_data.get('data', {}).get('team')
if not team_node:
    print(f'ERROR: team not found: {team_id}')
    sys.exit(1)

canceled_state_id = None
for state in team_node.get('states', {}).get('nodes', []):
    if state.get('type') == 'canceled':
        canceled_state_id = state['id']
        break

if not canceled_state_id:
    print(f'ERROR: no canceled state found for team {team_id}')
    sys.exit(1)

# 2. Move issue to canceled state
move_mut = '''mutation (\$issueId: String!, \$stateId: String!) {
    issueUpdate(id: \$issueId, input: { stateId: \$stateId }) {
        success
    }
}'''
move_result = gql(move_mut, {'issueId': issue_uuid, 'stateId': canceled_state_id})

# Defensive null checks (fail-closed)
data = move_result.get('data') if move_result else None
if data is None:
    errors = move_result.get('errors', []) if move_result else []
    print(f'ERROR: GraphQL returned no data, errors: {errors}')
    sys.exit(1)

issue_update = data.get('issueUpdate')
if issue_update is None:
    errors = move_result.get('errors', [])
    print(f'ERROR: issueUpdate returned null, GraphQL errors: {errors}')
    sys.exit(1)

if not issue_update.get('success'):
    print('ERROR: issueUpdate.success is false')
    sys.exit(1)

# 3. Add evidence comment
comment_body = f'''<!-- terminal-absorption {linear_ref} -->
终态吸收：GitHub 镜像已关闭（由 droid 或其他路径完成），Linear issue 直接吸收至终态。
- **INFRA ref**: {linear_ref}
- **依据**: GitHub mirror closed (no open issue found)
- **动作**: Linear state → canceled（terminal absorption）
- **幂等**: 后续 tick 不再重复处理'''

comment_mut = '''mutation (\$issueId: String!, \$body: String!) {
    commentCreate(input: { issueId: \$issueId, body: \$body }) {
        comment { id }
    }
}'''
gql(comment_mut, {'issueId': issue_uuid, 'body': comment_body})

print('OK')
" 2>>"$LOG_FILE" || echo "FAIL")

        if [ "$ABSORB_RESULT" = "OK" ]; then
            log "  ${LINEAR_REF}: terminal absorption completed (GitHub mirror closed, moved to canceled)"
            # VAL-LEDGER-001：写 cancel 墓碑（与 failed-session/stale-orphan 同语义，
            # 三处 cancel 点全覆盖）+ 清理 fallback 计数器（墓碑已落，计数器失效）
            touch "$TOMBSTONE_FILE"
            STALE_ORPHAN_COUNT_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.count"
            rm -f "$STALE_ORPHAN_COUNT_FILE"
        else
            log "  ${LINEAR_REF}: terminal absorption failed (${ABSORB_RESULT}), will retry next tick"
        fi

        continue
    fi

    # 5b. 检查 per-issue 锁是否存在且 PID 存活
    ISSUE_LOCK="${LOCK_DIR}/l2d-${LINEAR_REF}.lock"
    if [ -f "$ISSUE_LOCK" ]; then
        ISSUE_LOCK_CONTENT=$(cat "$ISSUE_LOCK" 2>/dev/null || echo "")
        if [ -n "$ISSUE_LOCK_CONTENT" ]; then
            ISSUE_LOCK_PID=$(echo "$ISSUE_LOCK_CONTENT" | cut -d: -f2)
            if [ -n "$ISSUE_LOCK_PID" ] && kill -0 "$ISSUE_LOCK_PID" 2>/dev/null; then
                log "  ${LINEAR_REF}: per-issue lock held (PID ${ISSUE_LOCK_PID}), skip"
                continue
            else
                log "  ${LINEAR_REF}: per-issue lock stale (PID ${ISSUE_LOCK_PID} dead), marking for retrigger"
            fi
        fi
    fi

    # 5c. 检查状态文件是否正在运行
    STATUS_FILE="${STATUS_DIR}/${LINEAR_REF}.json"
    if [ -f "$STATUS_FILE" ]; then
        STATUS=$(/opt/homebrew/bin/python3 -c "
import json
try:
    d = json.load(open('$STATUS_FILE'))
    print(d.get('status', ''))
except:
    print('')
" 2>/dev/null || echo "")

        if [ "$STATUS" = "running" ]; then
            STATUS_PID=$(/opt/homebrew/bin/python3 -c "
import json
try:
    d = json.load(open('$STATUS_FILE'))
    print(d.get('pid', ''))
except:
    print('')
" 2>/dev/null || echo "")

            if [ -n "$STATUS_PID" ] && kill -0 "$STATUS_PID" 2>/dev/null; then
                log "  ${LINEAR_REF}: droid running (PID ${STATUS_PID}), skip"
                continue
            else
                log "  ${LINEAR_REF}: stale running status (PID dead), marking for retrigger"
            fi
        elif [ "$STATUS" = "completed" ]; then
            # === 死锁出口（architecture §3.2，VAL-DLK-001/002/004/005）===
            # 条件：status=completed + sessionId 非空 + exitCode=0 + Linear 非终态超 stale 阈值
            # 动作：推进 Linear 终态 + 三要素证据评论 + 幂等 sentinel
            DEADLOCK_EXIT_DONE=$(/opt/homebrew/bin/python3 -c "
import json
try:
    d = json.load(open('$STATUS_FILE'))
    sid = d.get('sessionId')
    ec = d.get('exitCode')
    if sid and str(sid).lower() not in ('none', 'null', '') and ec == 0:
        print('yes')
    else:
        print('no')
except:
    print('no')
" 2>/dev/null || echo "no")

            if [ "$DEADLOCK_EXIT_DONE" = "yes" ]; then
                # 检查 Linear updatedAt 是否超过 stale 阈值
                LINEAR_AGE_MIN=$(/opt/homebrew/bin/python3 -c "
from datetime import datetime, timezone
try:
    updated = datetime.fromisoformat('${ISSUE_UPDATED}'.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    age_min = int((now - updated).total_seconds() / 60)
    print(age_min)
except:
    # Defensive default: garbage/unparseable dates should trigger stale check
    print(9999)
" 2>/dev/null || echo "9999")

                if [ "$LINEAR_AGE_MIN" -ge "$DEADLOCK_STALE_THRESHOLD_MIN" ]; then
                    # 检查幂等 sentinel（是否已执行过出口）
                    # LINEAR_API_KEY 已在脚本顶部 export，子进程自动继承
                    SENTINEL_EXISTS=$(/opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = '$ISSUE_UUID'
sentinel_prefix = '$DEADLOCK_EXIT_SENTINEL_PREFIX'
if not api_key or not issue_uuid:
    print('no')
    sys.exit(0)
query = '{ issue(id: \"' + issue_uuid + '\") { comments { nodes { body } } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': query}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        nodes = d.get('data', {}).get('issue', {}).get('comments', {}).get('nodes', [])
        for n in nodes:
            body = n.get('body', '')
            if sentinel_prefix in body:
                print('yes')
                sys.exit(0)
        print('no')
except Exception:
    print('no')
" 2>>"$LOG_FILE" || echo "no")

                    if [ "$SENTINEL_EXISTS" = "yes" ]; then
                        log "  ${LINEAR_REF}: deadlock exit already executed (sentinel found), skip (VAL-DLK-005 idempotent)"
                        continue
                    fi

                    # 执行死锁出口：推进 Linear 终态 + 三要素证据评论
                    # LINEAR_API_KEY 已在脚本顶部 export，子进程自动继承
                    # M3 (NB-1): DRY_RUN guard - only log, don't execute Linear changes
                    if [ "${DRY_RUN:-0}" = "1" ]; then
                        log "  ${LINEAR_REF}: [DRY_RUN] would execute deadlock exit (push to completed, stale=${LINEAR_AGE_MIN}min)"
                        continue
                    fi
                    DEADLOCK_RESULT=$(ISSUE_UUID="$ISSUE_UUID" \
                        LINEAR_REF="$LINEAR_REF" STATUS_FILE="$STATUS_FILE" \
                        TEAM_ID="$TEAM_ID" \
                        DEADLOCK_EXIT_SENTINEL_PREFIX="$DEADLOCK_EXIT_SENTINEL_PREFIX" \
                        /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = os.environ['ISSUE_UUID']
linear_ref = os.environ['LINEAR_REF']
status_file = os.environ['STATUS_FILE']
team_id = os.environ['TEAM_ID']
sentinel_prefix = os.environ['DEADLOCK_EXIT_SENTINEL_PREFIX']

def gql(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps(payload).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

# 1. 查询指定 team 的 completed state（关键：必须用 TEAM_ID，不能 teams(first:1)）
team_query = '''query(\$teamId: String!) {
    team(id: \$teamId) {
        states {
            nodes { id name type }
        }
    }
}'''
team_data = gql(team_query, {'teamId': team_id})
team_node = team_data.get('data', {}).get('team')
if not team_node:
    print(f'ERROR: team not found: {team_id}')
    sys.exit(1)

completed_state_id = None
for state in team_node.get('states', {}).get('nodes', []):
    if state.get('type') == 'completed':
        completed_state_id = state['id']
        break

if not completed_state_id:
    print(f'ERROR: no completed state found for team {team_id}')
    sys.exit(1)

# 2. 读取 status 文件证据（防御性处理：文件可能损坏/为空/为 null）
try:
    with open(status_file) as f:
        status_data = json.load(f)
    if status_data is None:
        status_data = {}
    session_id = status_data.get('sessionId', '')
    exit_code = status_data.get('exitCode', '')
except Exception as e:
    print(f'ERROR: failed to read status file: {e}')
    sys.exit(1)

# 3. 推进 Linear 到 completed
move_mut = '''mutation (\$issueId: String!, \$stateId: String!) {
    issueUpdate(id: \$issueId, input: { stateId: \$stateId }) {
        success
    }
}'''
move_result = gql(move_mut, {'issueId': issue_uuid, 'stateId': completed_state_id})

# 防御性 null 检查（fail-closed：证据不全=不推进）
data = move_result.get('data') if move_result else None
if data is None:
    errors = move_result.get('errors', []) if move_result else []
    print(f'ERROR: GraphQL returned no data, errors: {errors}')
    sys.exit(1)

issue_update = data.get('issueUpdate')
if issue_update is None:
    errors = move_result.get('errors', [])
    print(f'ERROR: issueUpdate returned null, GraphQL errors: {errors}')
    sys.exit(1)

if not issue_update.get('success'):
    print('ERROR: issueUpdate.success is false')
    sys.exit(1)

# 4. 写三要素证据评论（含幂等 sentinel）
comment_body = f'''{sentinel_prefix}{linear_ref} sessionId={session_id} exitCode=0 -->
死锁出口：session 已完成（sessionId={session_id}，exitCode={exit_code}），Linear 停留非终态超 stale 阈值，由 reconcile 推进终态。
- **INFRA ref**: {linear_ref}
- **Status 依据**: status=completed, sessionId={session_id}, exitCode={exit_code}
- **动作**: Linear state → completed（reconcile deadlock exit）'''

comment_mut = '''mutation (\$issueId: String!, \$body: String!) {
    commentCreate(input: { issueId: \$issueId, body: \$body }) {
        comment { id }
    }
}'''
gql(comment_mut, {'issueId': issue_uuid, 'body': comment_body})

print('OK')
" 2>>"$LOG_FILE" || echo "FAIL")

                    # Extract sessionId for logging (Issue #4 fix)
                    SESSION_ID_FOR_LOG=$(python3 -c "import json; print(json.load(open('$STATUS_FILE')).get('sessionId', 'unknown'))" 2>/dev/null || echo "unknown")

                    if [ "$DEADLOCK_RESULT" = "OK" ]; then
                        log "  ${LINEAR_REF}: DEADLOCK EXIT executed — Linear pushed to completed (session=${SESSION_ID_FOR_LOG:-unknown}, exitCode=0, stale=${LINEAR_AGE_MIN}min)"
                        # 记录到 drift log 供取证
                        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DEADLOCK_EXIT: ${LINEAR_REF} uuid=${ISSUE_UUID} stale=${LINEAR_AGE_MIN}min" >> "${LOG_DIR}/anchor-drift.log"
                    else
                        log "  ${LINEAR_REF}: WARNING deadlock exit failed (${DEADLOCK_RESULT}), will retry next tick"
                    fi
                    continue
                else
                    log "  ${LINEAR_REF}: completed but Linear not yet stale (${LINEAR_AGE_MIN}min < ${DEADLOCK_STALE_THRESHOLD_MIN}min threshold), skip"
                    continue
                fi
            else
                log "  ${LINEAR_REF}: already completed (no valid session/exitCode), skip"
                continue
            fi
        elif [ "$STATUS" = "failed" ]; then
            # === status=failed 出口（INFRA-371 死锁止血，E4 复发防护）===
            # 条件：status=failed（session 执行失败，exitCode!=0）
            # 问题：status 文件存在 → stale-orphan 路径（需文件缺失）不触发
            #       镜像 OPEN + status 存在 → retrigger 双验证通过 → 无限循环
            # 解决：将 RETRIGGER_OK 置 0，复用 stale-orphan counter 机制
            #       连续 N tick 后执行 Linear canceled + 证据评论（幂等）
            log "  ${LINEAR_REF}: status=failed (session failed, exitCode!=0), blocking retrigger (E4 fix)"
            RETRIGGER_OK=0

            # === 基础设施故障分流 (VAL-M1-002) ===
            # exitCode=127/126 → 不取消、标记 infra_failed、计数冻结
            # 插入点：log blocking 之后、STALE_ORPHAN_COUNT 递增之前
            STATUS_EXIT_CODE=$(/opt/homebrew/bin/python3 -c "
import json
try:
    d = json.load(open('$STATUS_FILE'))
    print(d.get('exitCode', ''))
except:
    print('')
" 2>/dev/null || echo "")

            if [ "$STATUS_EXIT_CODE" = "127" ] || [ "$STATUS_EXIT_CODE" = "126" ]; then
                log "  ${LINEAR_REF}: INFRA FAILURE DETECTED (exitCode=${STATUS_EXIT_CODE}) — freezing counter, marking infra_failed"
                
                # 写 infra_failed 标记（不递增计数）
                INFRA_FAILED_MARKER="${LOCK_DIR}/infra-failed-${LINEAR_REF}.marker"
                echo "exitCode=${STATUS_EXIT_CODE}" > "$INFRA_FAILED_MARKER"
                
                # PostHog 事件（通过 lib/posthog.sh 统一实现）
                if [ "$DRY_RUN" = "1" ]; then
                    log "  ${LINEAR_REF}: [DRY_RUN] would send PostHog event ci_infra_failure_blocked_cancel (exitCode=${STATUS_EXIT_CODE})"
                else
                    POSTHOG_EVENT_NAME="ci_infra_failure_blocked_cancel"
                    POSTHOG_DISTINCT_ID="reconcile-evolution"
                    # shellcheck source=/dev/null
                    source "${SCRIPT_DIR}/lib/posthog.sh"
                    send_posthog_event "infra_failure_${STATUS_EXIT_CODE}" "$LINEAR_REF" "${STATUS_FILE}" "exitCode=${STATUS_EXIT_CODE}"
                fi
                
                # 跳过计数器递增，直接 continue
                continue
            fi

            # 复用 stale-orphan counter 机制（与 status 文件缺失路径同构）
            STALE_ORPHAN_COUNT_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.count"
            if [ -f "$STALE_ORPHAN_COUNT_FILE" ]; then
                STALE_ORPHAN_COUNT=$(cat "$STALE_ORPHAN_COUNT_FILE" 2>/dev/null || echo "0")
                STALE_ORPHAN_COUNT=$((STALE_ORPHAN_COUNT + 1))
            else
                STALE_ORPHAN_COUNT=1
            fi
            echo "$STALE_ORPHAN_COUNT" > "$STALE_ORPHAN_COUNT_FILE"
            log "  ${LINEAR_REF}: failed-session counter=${STALE_ORPHAN_COUNT}/${STALE_ORPHAN_FALLBACK_TICKS}"

            if [ "$STALE_ORPHAN_COUNT" -ge "$STALE_ORPHAN_FALLBACK_TICKS" ]; then
                log "  ${LINEAR_REF}: FAILED SESSION FALLBACK — threshold reached, executing Linear canceled"

                # 幂等检查：是否已执行过 canceled
                STALE_ORPHAN_SENTINEL_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
                if [ -f "$STALE_ORPHAN_SENTINEL_FILE" ]; then
                    log "  ${LINEAR_REF}: failed session fallback already executed (sentinel found), skip (idempotent)"
                    continue
                fi

                # DRY_RUN guard (VAL-M1-001): skip actual GraphQL call
                if [ "$DRY_RUN" = "1" ]; then
                    log "  ${LINEAR_REF}: [DRY_RUN] would execute Linear canceled (failed-session, count=${STALE_ORPHAN_COUNT})"
                    continue
                fi

                # 执行 Linear canceled + 证据评论（复用 stale-orphan fallback 逻辑）
                STALE_ORPHAN_RESULT=$(ISSUE_UUID="$ISSUE_UUID" \
                    LINEAR_REF="$LINEAR_REF" \
                    STALE_ORPHAN_COUNT="$STALE_ORPHAN_COUNT" \
                    STALE_ORPHAN_FALLBACK_TICKS="$STALE_ORPHAN_FALLBACK_TICKS" \
                    TEAM_ID="$TEAM_ID" \
                    /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = os.environ['ISSUE_UUID']
linear_ref = os.environ['LINEAR_REF']
stale_count = os.environ['STALE_ORPHAN_COUNT']
fallback_ticks = os.environ['STALE_ORPHAN_FALLBACK_TICKS']

def gql(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps(payload).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

# 1. 查询 team 的 canceled state
team_id = os.environ['TEAM_ID']
team_query = '''query(\$teamId: String!) {
    team(id: \$teamId) {
        states {
            nodes { id name type }
        }
    }
}'''
team_data = gql(team_query, {'teamId': team_id})
team_node = team_data.get('data', {}).get('team')
if not team_node:
    print(f'ERROR: team not found: {team_id}')
    sys.exit(1)

canceled_state_id = None
for state in team_node.get('states', {}).get('nodes', []):
    if state.get('type') == 'canceled':
        canceled_state_id = state['id']
        break

if not canceled_state_id:
    print(f'ERROR: no canceled state found for team {team_id}')
    sys.exit(1)

# 2. 推进 Linear 到 canceled
move_mut = '''mutation (\$issueId: String!, \$stateId: String!) {
    issueUpdate(id: \$issueId, input: { stateId: \$stateId }) {
        success
    }
}'''
move_result = gql(move_mut, {'issueId': issue_uuid, 'stateId': canceled_state_id})

# 防御性 null 检查
data = move_result.get('data') if move_result else None
if data is None:
    errors = move_result.get('errors', []) if move_result else []
    print(f'ERROR: GraphQL returned no data, errors: {errors}')
    sys.exit(1)

issue_update = data.get('issueUpdate')
if issue_update is None:
    errors = move_result.get('errors', [])
    print(f'ERROR: issueUpdate returned null, GraphQL errors: {errors}')
    sys.exit(1)

if not issue_update.get('success'):
    print('ERROR: issueUpdate.success is false')
    sys.exit(1)

# 3. 写证据评论
comment_body = f'''<!-- stale-orphan-fallback {linear_ref} count={stale_count} -->
Stale orphan fallback：status=failed（session 执行失败），连续 {stale_count} tick 被 E4 阻挡，判定为无法自愈的遗留 issue。
- **INFRA ref**: {linear_ref}
- **E4 阻挡次数**: {stale_count}（阈值={fallback_ticks}）
- **Status 文件**: status=failed, exitCode!=0（session 失败）
- **动作**: Linear state → canceled（failed session fallback）
- **GitHub 镜像**: 由 scanner auto_close_resolved() 下一轮关闭（Linear→GitHub 自动同步）'''

comment_mut = '''mutation (\$issueId: String!, \$body: String!) {
    commentCreate(input: { issueId: \$issueId, body: \$body }) {
        comment { id }
    }
}'''
gql(comment_mut, {'issueId': issue_uuid, 'body': comment_body})

print('OK')
" 2>>"$LOG_FILE" || echo "FAIL")

                if [ "$STALE_ORPHAN_RESULT" = "OK" ]; then
                    log "  ${LINEAR_REF}: FAILED SESSION FALLBACK executed — Linear canceled (count=${STALE_ORPHAN_COUNT})"
                    # 写幂等 sentinel
                    touch "$STALE_ORPHAN_SENTINEL_FILE"
                    # 记录到 drift log
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAILED_SESSION_CANCELED: ${LINEAR_REF} uuid=${ISSUE_UUID} count=${STALE_ORPHAN_COUNT}" >> "${LOG_DIR}/anchor-drift.log"
                    # 清理计数器
                    rm -f "$STALE_ORPHAN_COUNT_FILE"
                else
                    log "  ${LINEAR_REF}: WARNING failed session fallback failed (${STALE_ORPHAN_RESULT}), will retry next tick"
                fi
                continue
            fi
            continue  # status=failed: always skip section 5d (do not retrigger)
        fi
    fi

    # 5d. 补触发前置双验证（architecture §3.3，VAL-DRF-005）
    # 前提：镜像 issue 仍 OPEN 且 finding 仍存在（status 文件存在）
    # 不满足 → 不派发，转关闭评估或记录
    RETRIGGER_OK=1

    # 验证 1：镜像 issue 仍 OPEN
    RETRIGGER_GH_CHECK=$(gh issue list --repo "$REPO" --label evolution-found --state open \
        --search "${LINEAR_REF}" --json number --limit 1 2>/dev/null || echo "[]")
    RETRIGGER_GH_COUNT=$(echo "$RETRIGGER_GH_CHECK" | /opt/homebrew/bin/python3 -c \
        "import json,sys; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "0")
    if [ "$RETRIGGER_GH_COUNT" -eq 0 ]; then
        log "  ${LINEAR_REF}: retrigger BLOCKED — no open GitHub mirror issue (VAL-DRF-005)"
        RETRIGGER_OK=0
    fi

    # 验证 2：status 文件存在（finding 仍被追踪）
    if [ "$RETRIGGER_OK" -eq 1 ] && [ ! -f "${STATUS_DIR}/${LINEAR_REF}.json" ]; then
        log "  ${LINEAR_REF}: retrigger BLOCKED — no status file (E4 形态，VAL-DRF-005)"
        # 转关闭评估：issue open + finding 消失（无 status 文件）→ 记录漂移
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DRIFT: ${LINEAR_REF} retrigger blocked — status file missing (E4)" >> "${LOG_DIR}/anchor-drift.log"
        RETRIGGER_OK=0
        
        # === 基础设施故障豁免 (m1-urgent-stale-orphan-guard) ===
        # 若 infra-failed marker 存在，说明此 issue 是基础设施故障（exitCode=127/126）
        # 归档后路由进 E4 的。此时必须跳过计数器递增，防止误取消。
        # 与 status=failed 分支的 infra-failed 守卫（:877-:906）同语义。
        INFRA_FAILED_MARKER="${LOCK_DIR}/infra-failed-${LINEAR_REF}.marker"
        if [ -f "$INFRA_FAILED_MARKER" ]; then
            log "  ${LINEAR_REF}: INFRA-FAILED MARKER FOUND — skipping stale-orphan counter (frozen, will not cancel)"
            # 清零残留计数器（marker 存在后计数器失效，删除防继续误判）
            STALE_ORPHAN_COUNT_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.count"
            if [ -f "$STALE_ORPHAN_COUNT_FILE" ]; then
                rm -f "$STALE_ORPHAN_COUNT_FILE"
                log "  ${LINEAR_REF}: removed stale count file (marker guard active)"
            fi
            continue
        fi
        
        # Stale orphan fallback (INFRA-310 / #676):
        # 连续 N tick 被 E4 阻挡且无 status 文件 → 判定为 pre-status-mechanism 孤儿
        # 执行 Linear canceled + 证据评论，不触发新 session（不违反 E4）
        STALE_ORPHAN_COUNT_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.count"
        if [ -f "$STALE_ORPHAN_COUNT_FILE" ]; then
            STALE_ORPHAN_COUNT=$(cat "$STALE_ORPHAN_COUNT_FILE" 2>/dev/null || echo "0")
            STALE_ORPHAN_COUNT=$((STALE_ORPHAN_COUNT + 1))
        else
            STALE_ORPHAN_COUNT=1
        fi
        echo "$STALE_ORPHAN_COUNT" > "$STALE_ORPHAN_COUNT_FILE"
        log "  ${LINEAR_REF}: stale orphan counter=${STALE_ORPHAN_COUNT}/${STALE_ORPHAN_FALLBACK_TICKS}"
        
        if [ "$STALE_ORPHAN_COUNT" -ge "$STALE_ORPHAN_FALLBACK_TICKS" ]; then
            log "  ${LINEAR_REF}: STALE ORPHAN FALLBACK — threshold reached, executing Linear canceled"
            
            # 幂等检查：是否已执行过 canceled
            STALE_ORPHAN_SENTINEL_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
            if [ -f "$STALE_ORPHAN_SENTINEL_FILE" ]; then
                log "  ${LINEAR_REF}: stale orphan fallback already executed (sentinel found), skip (idempotent)"
                continue
            fi

            # DRY_RUN guard (VAL-M1-001): skip actual GraphQL call
            if [ "$DRY_RUN" = "1" ]; then
                log "  ${LINEAR_REF}: [DRY_RUN] would execute Linear canceled (stale-orphan, count=${STALE_ORPHAN_COUNT})"
                continue
            fi

            # 执行 Linear canceled + 证据评论
            STALE_ORPHAN_RESULT=$(ISSUE_UUID="$ISSUE_UUID" \
                LINEAR_REF="$LINEAR_REF" \
                STALE_ORPHAN_COUNT="$STALE_ORPHAN_COUNT" \
                STALE_ORPHAN_FALLBACK_TICKS="$STALE_ORPHAN_FALLBACK_TICKS" \
                TEAM_ID="$TEAM_ID" \
                /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
api_key = os.environ['LINEAR_API_KEY']
issue_uuid = os.environ['ISSUE_UUID']
linear_ref = os.environ['LINEAR_REF']
stale_count = os.environ['STALE_ORPHAN_COUNT']
fallback_ticks = os.environ['STALE_ORPHAN_FALLBACK_TICKS']

def gql(query, variables=None):
    payload = {'query': query}
    if variables:
        payload['variables'] = variables
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps(payload).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

# 1. 查询 team 的 canceled state
team_id = os.environ['TEAM_ID']
team_query = '''query(\$teamId: String!) {
    team(id: \$teamId) {
        states {
            nodes { id name type }
        }
    }
}'''
team_data = gql(team_query, {'teamId': team_id})
team_node = team_data.get('data', {}).get('team')
if not team_node:
    print(f'ERROR: team not found: {team_id}')
    sys.exit(1)

canceled_state_id = None
for state in team_node.get('states', {}).get('nodes', []):
    if state.get('type') == 'canceled':
        canceled_state_id = state['id']
        break

if not canceled_state_id:
    print(f'ERROR: no canceled state found for team {team_id}')
    sys.exit(1)

# 2. 推进 Linear 到 canceled
move_mut = '''mutation (\$issueId: String!, \$stateId: String!) {
    issueUpdate(id: \$issueId, input: { stateId: \$stateId }) {
        success
    }
}'''
move_result = gql(move_mut, {'issueId': issue_uuid, 'stateId': canceled_state_id})

# 防御性 null 检查
data = move_result.get('data') if move_result else None
if data is None:
    errors = move_result.get('errors', []) if move_result else []
    print(f'ERROR: GraphQL returned no data, errors: {errors}')
    sys.exit(1)

issue_update = data.get('issueUpdate')
if issue_update is None:
    errors = move_result.get('errors', [])
    print(f'ERROR: issueUpdate returned null, GraphQL errors: {errors}')
    sys.exit(1)

if not issue_update.get('success'):
    print('ERROR: issueUpdate.success is false')
    sys.exit(1)

# 3. 写证据评论
comment_body = f'''<!-- stale-orphan-fallback {linear_ref} count={stale_count} -->
Stale orphan fallback：无 status 文件（pre-status-mechanism 孤儿），连续 {stale_count} tick 被 E4 阻挡，判定为无法自愈的遗留 issue。
- **INFRA ref**: {linear_ref}
- **E4 阻挡次数**: {stale_count}（阈值={fallback_ticks}）
- **Status 文件**: 不存在（早于 status 机制或已消失）
- **动作**: Linear state → canceled（stale orphan fallback）
- **GitHub 镜像**: 由 scanner auto_close_resolved() 下一轮关闭（Linear→GitHub 自动同步）'''

comment_mut = '''mutation (\$issueId: String!, \$body: String!) {
    commentCreate(input: { issueId: \$issueId, body: \$body }) {
        comment { id }
    }
}'''
gql(comment_mut, {'issueId': issue_uuid, 'body': comment_body})

print('OK')
" 2>>"$LOG_FILE" || echo "FAIL")
            
            if [ "$STALE_ORPHAN_RESULT" = "OK" ]; then
                log "  ${LINEAR_REF}: STALE ORPHAN FALLBACK executed — Linear canceled (count=${STALE_ORPHAN_COUNT})"
                # 写幂等 sentinel
                touch "$STALE_ORPHAN_SENTINEL_FILE"
                # 记录到 drift log
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] STALE_ORPHAN_CANCELED: ${LINEAR_REF} uuid=${ISSUE_UUID} count=${STALE_ORPHAN_COUNT}" >> "${LOG_DIR}/anchor-drift.log"
                # 清理计数器
                rm -f "$STALE_ORPHAN_COUNT_FILE"
            else
                log "  ${LINEAR_REF}: WARNING stale orphan fallback failed (${STALE_ORPHAN_RESULT}), will retry next tick"
            fi
            continue
        fi
    fi

    if [ "$RETRIGGER_OK" -eq 0 ]; then
        continue
    fi

    log "  ${LINEAR_REF}: orphaned, triggering reconciliation"
    bash "${SCRIPT_DIR}/trigger-droid.sh" \
        "create" "Issue" "$LINEAR_REF" "$ISSUE_UUID" "$TEAM_KEY" "$ISSUE_TITLE" \
        >> "$LOG_FILE" 2>&1 || true

    TRIGGERED=1
    log "  Triggered ${LINEAR_REF}, stopping for this tick (serial execution)"
    break

done <<< "$LINEAR_ISSUES"

log "Reconciliation complete: triggered=${TRIGGERED}"
