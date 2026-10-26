#!/bin/bash
# linear-queue.sh — Linear GraphQL helper library for per-team serialization
# Source this file from trigger-droid.sh
#
# Provides:
#   linear_queue_init(TEAM_KEY, ISSUE_UUID, LINEAR_API_KEY)
#   linear_move_to_in_progress(ISSUE_UUID)
# linear_query_next_queued()
#   演化卡中立（owner 第零条裁定）：无 delegate = 跳过 pick（不按标签优选）；
#   查询恒排除 evolution-found 标签。输出第 5 字段 = 该卡标签 JSON，
#   供 trigger-droid.sh 链式触发守卫识别演化卡。
#   linear_get_issue_team_key(ISSUE_UUID)
#
# Global variables set by linear_queue_init:
#   TEAM_ID              — Linear team UUID (from repositories.yml)
#   AGENT_UUID           — Factory Agent Linear user UUID (from issue delegate)
#   IN_PROGRESS_STATE_ID — Team "started" workflow state UUID
#
# All GraphQL calls use /opt/homebrew/bin/python3 + urllib with socket timeout.
# Functions tolerate API failures (return empty/error, never hang).
#
# SECURITY: All webhook-derived values and secrets are passed via os.environ,
# never interpolated directly into Python string literals (heredoc-safety).

# === Guard against double-sourcing ===
if [ -n "${_LINEAR_QUEUE_SH_LOADED:-}" ]; then
    # SC2317 误报：source-in-subshell 时 return 可达（同 lib/evolution-channel.sh 先例）
    # shellcheck disable=SC2317
    return 0 2>/dev/null || true
fi
_LINEAR_QUEUE_SH_LOADED=1

# === Global variables (populated by linear_queue_init) ===
TEAM_ID=""
AGENT_UUID=""
IN_PROGRESS_STATE_ID=""

# 演化排除标签（linear_query_next_queued 差集查询用；与
# lib/evolution-channel.sh EVOLUTION_CHANNEL_LABEL 同步的双保险）
EVOLUTION_EXCLUDE_LABEL="${EVOLUTION_EXCLUDE_LABEL:-evolution-found}"

# === Internal: LOG_FILE fallback ===
# If LOG_FILE is not set by the caller, default to stderr
_LQ_LOG="${LOG_FILE:-/dev/stderr}"

# ============================================================================
# linear_queue_init(TEAM_KEY, ISSUE_UUID, LINEAR_API_KEY)
#
# Discover:
#   1. TEAM_ID from repositories.yml via TEAM_KEY lookup
#   2. AGENT_UUID from issue(id: ISSUE_UUID).delegate.id
#   3. IN_PROGRESS_STATE_ID from team(id: TEAM_ID).states where type="started"
#
# Returns: 0 on success, 1 on failure
# ============================================================================
linear_queue_init() {
    local team_key="$1"
    local issue_uuid="$2"
    local api_key="$3"

    # Validate inputs
    if [ -z "$team_key" ] || [ -z "$issue_uuid" ] || [ -z "$api_key" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: missing required parameter (team_key=${team_key:-<empty>}, issue_uuid=${issue_uuid:-<empty>}, api_key=${api_key:+<set>})" >> "$_LQ_LOG"
        return 1
    fi

    # === Step 1: Look up TEAM_ID from repositories.yml ===
    TEAM_ID=$(TEAM_KEY="$team_key" REPO_CONFIG="${REPO_CONFIG:-${HOME}/.factory/config/repositories.yml}" /opt/homebrew/bin/python3 -c "
import yaml, os, sys
team_key = os.environ['TEAM_KEY']
try:
    with open(os.environ['REPO_CONFIG']) as f:
        cfg = yaml.safe_load(f)
    for td in cfg.get('teams', {}).values():
        if td.get('teamKey', '').upper() == team_key.upper():
            print(td.get('linearTeamId', ''))
            sys.exit(0)
    print('')
except Exception as e:
    print('', file=sys.stderr)
    sys.exit(1)
" 2>>"$_LQ_LOG") || {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: failed to read repositories.yml for team_key=$team_key" >> "$_LQ_LOG"
        return 1
    }

    if [ -z "$TEAM_ID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: TEAM_ID not found for team_key=$team_key" >> "$_LQ_LOG"
        return 1
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: TEAM_ID=$TEAM_ID (team=$team_key)" >> "$_LQ_LOG"

    # === Step 2: Query issue delegate.id → AGENT_UUID ===
    AGENT_UUID=$(ISSUE_UUID="$issue_uuid" LINEAR_API_KEY="$api_key" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
issue_uuid = os.environ['ISSUE_UUID']
api_key = os.environ['LINEAR_API_KEY']
query = '{ issue(id: \"' + issue_uuid + '\") { delegate { id } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': query}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        delegate = d.get('data', {}).get('issue', {}).get('delegate', {})
        print(delegate.get('id', '') if delegate else '')
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>>"$_LQ_LOG") || {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: failed to query delegate for issue=$issue_uuid" >> "$_LQ_LOG"
        return 1
    }

    if [ -z "$AGENT_UUID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: AGENT_UUID not found for issue=$issue_uuid (issue may not be delegated — continuing without delegate)" >> "$_LQ_LOG"
        # Do NOT return 1 here — AGENT_UUID is optional for basic queue init
        # Continue to Step 3 to resolve IN_PROGRESS_STATE_ID
        # Downstream consumers (linear_move_to_in_progress, chain_trigger_next)
        # either don't need AGENT_UUID or already handle null gracefully
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: AGENT_UUID=$AGENT_UUID" >> "$_LQ_LOG"
    fi

    # === Step 3: Query team states → find type="started" → IN_PROGRESS_STATE_ID ===
    IN_PROGRESS_STATE_ID=$(TEAM_ID="$TEAM_ID" LINEAR_API_KEY="$api_key" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
team_id = os.environ['TEAM_ID']
api_key = os.environ['LINEAR_API_KEY']
query = '{ team(id: \"' + team_id + '\") { states { nodes { id name type } } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': query}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        states = d.get('data', {}).get('team', {}).get('states', {}).get('nodes', [])
        for s in states:
            if s.get('type') == 'started':
                print(s['id'])
                sys.exit(0)
        print('')
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
" 2>>"$_LQ_LOG") || {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: failed to query team states for team=$TEAM_ID" >> "$_LQ_LOG"
        return 1
    }

    if [ -z "$IN_PROGRESS_STATE_ID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: IN_PROGRESS_STATE_ID not found for team=$TEAM_ID (no state with type=started)" >> "$_LQ_LOG"
        return 1
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: IN_PROGRESS_STATE_ID=$IN_PROGRESS_STATE_ID" >> "$_LQ_LOG"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_queue_init: all variables resolved successfully" >> "$_LQ_LOG"
    return 0
}

# ============================================================================
# linear_move_to_in_progress(ISSUE_UUID)
#
# Mutation: issueUpdate(id: ISSUE_UUID, input: { stateId: IN_PROGRESS_STATE_ID })
# Requires: IN_PROGRESS_STATE_ID set by linear_queue_init
#
# Returns: 0 on success, 1 on failure
# ============================================================================
linear_move_to_in_progress() {
    local issue_uuid="$1"

    if [ -z "$issue_uuid" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_move_to_in_progress: issue_uuid is empty" >> "$_LQ_LOG"
        return 1
    fi

    if [ -z "$IN_PROGRESS_STATE_ID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_move_to_in_progress: IN_PROGRESS_STATE_ID not set (call linear_queue_init first)" >> "$_LQ_LOG"
        return 1
    fi

    local api_key="${LINEAR_API_KEY:-}"
    if [ -z "$api_key" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_move_to_in_progress: LINEAR_API_KEY not set" >> "$_LQ_LOG"
        return 1
    fi

    ISSUE_UUID="$issue_uuid" IN_PROGRESS_STATE_ID="$IN_PROGRESS_STATE_ID" LINEAR_API_KEY="$api_key" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
issue_uuid = os.environ['ISSUE_UUID']
state_id = os.environ['IN_PROGRESS_STATE_ID']
api_key = os.environ['LINEAR_API_KEY']
mutation = 'mutation { issueUpdate(id: \"' + issue_uuid + '\", input: { stateId: \"' + state_id + '\" }) { success issue { id state { name type } } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': mutation}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        success = d.get('data', {}).get('issueUpdate', {}).get('success', False)
        if success:
            state = d.get('data', {}).get('issueUpdate', {}).get('issue', {}).get('state', {})
            print('OK: moved to ' + state.get('name', 'unknown') + ' (type=' + state.get('type', 'unknown') + ')')
        else:
            errors = d.get('errors', [])
            print('FAIL: ' + json.dumps(errors), file=sys.stderr)
            sys.exit(1)
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    sys.exit(1)
" >> "$_LQ_LOG" 2>&1 || {
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_move_to_in_progress: API call failed for issue=$issue_uuid" >> "$_LQ_LOG"
        return 1
    }

    return 0
}

# ============================================================================
# linear_query_next_queued([skip_uuid...])
#
# Query same-team issues delegated to Factory Agent with state.type='unstarted',
# returns earliest by createdAt.
#
# 可选参数：本轮已拒绝卡的 UUID 列表（id nin 排除）——链式触发环拒绝演化卡时
# Linear 卡片状态未变，下一轮查询会再返回队首同一张；调用方传 skip 列表让查询
# 跳过已拒绝卡，链式环才能继续处理后续正常卡（不依赖 repick-guard break）。
#
# Output to stdout: UUID|REF|TITLE|TEAM_KEY|LABELS_JSON (pipe-delimited;
# LABELS_JSON = 该卡标签名数组的 JSON，供链式触发守卫识别演化卡；旧 4 字段
# 消费方按 read -r a b c d 兼容，第 5 字段被丢弃)。
# Empty stdout if no queued issues found.
# Requires: TEAM_ID, AGENT_UUID set by linear_queue_init
#
# 演化卡中立（Mac 通道分离不变量，owner 第零条裁定 2026-10-04）：
#   - AGENT_UUID 为空 = 跳过 pick（不回退标签查询——旧实现对空 delegate 按
#     evolution-found 标签优选演化卡，是演化卡绕过通道分离的铸卡源）；
#   - 查询恒排除演化标签：Linear 无 labels none 反向过滤（实证 400：
#     "Field \"none\" is not defined by type IssueLabelCollectionFilter"，
#     IssueFilter 也无 not），用双查询 A/B 差集——A=主查询取候选批，
#     B=labels some eqIgnoreCase 命中演化标签集，client 侧从 A 剔除 B 的 id。
#     eqIgnoreCase 单标量（StringComparator 无 inIgnoreCase，实证 400）。
# ============================================================================
linear_query_next_queued() {
    if [ -z "$TEAM_ID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_query_next_queued: TEAM_ID not set" >> "$_LQ_LOG"
        return 0
    fi
    if [ -z "$AGENT_UUID" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_query_next_queued: AGENT_UUID empty — skipping pick (evolution-neutral: no label fallback)" >> "$_LQ_LOG"
        return 0
    fi

    local api_key="${LINEAR_API_KEY:-}"
    if [ -z "$api_key" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_query_next_queued: LINEAR_API_KEY not set" >> "$_LQ_LOG"
        return 0
    fi

    # 本轮已拒绝卡 skip 列表（空白分隔 UUID；非空才拼 nin 过滤）
    local skip_uuids="$*"

    local result
    result=$(TEAM_ID="$TEAM_ID" AGENT_UUID="$AGENT_UUID" LINEAR_API_KEY="$api_key" \
        SKIP_UUIDS="$skip_uuids" EVOLUTION_EXCLUDE_LABEL="$EVOLUTION_EXCLUDE_LABEL" \
        /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
team_id = os.environ['TEAM_ID']
agent_uuid = os.environ['AGENT_UUID']
api_key = os.environ['LINEAR_API_KEY']
skip_uuids = os.environ.get('SKIP_UUIDS', '').split()
evo_label = os.environ.get('EVOLUTION_EXCLUDE_LABEL') or 'evolution-found'

def gql(filter_body, first):
    query = '''{
        issues(
            filter: {
                team: { id: { eq: \"%s\" } },
                delegate: { id: { eq: \"%s\" } },
                state: { type: { in: [\"triage\", \"backlog\", \"unstarted\"] } }%s
            },
            orderBy: createdAt,
            first: %d
        ) {
            nodes { id identifier title team { key } labels { nodes { name } } }
        }
    }''' % (team_id, agent_uuid, filter_body, first)
    req = urllib.request.Request(
        'https://api.linear.app/graphql',
        data=json.dumps({'query': query}).encode(),
        headers={'Authorization': api_key, 'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

try:
    nin = ''
    if skip_uuids:
        nin = ', id: { nin: [%s] }' % ', '.join('\"' + u + '\"' for u in skip_uuids)
    # A：候选批（delegate 过滤 + 本轮已拒绝 nin 排除）
    d = gql(nin, 50)
    if d.get('errors'):
        print('GraphQL errors: ' + json.dumps(d['errors'])[:300], file=sys.stderr)
        sys.exit(0)
    nodes = d.get('data', {}).get('issues', {}).get('nodes', [])
    # B：演化标签命中集（some eqIgnoreCase 单标量；A/B 差集 = 无演化标签）
    d_b = gql(', labels: { some: { name: { eqIgnoreCase: \"%s\" } } }' % evo_label, 50)
    if not d_b.get('errors'):
        evo_ids = {n.get('id') for n in d_b.get('data', {}).get('issues', {}).get('nodes', [])}
        nodes = [n for n in nodes if n.get('id') not in evo_ids]
    else:
        # B 查询失败不阻断 A 结果（title 信号守卫仍在链式面兜底识别）
        print('WARN: evolution-set query failed: ' + json.dumps(d_b.get('errors'))[:200], file=sys.stderr)
    if nodes:
        n = nodes[0]
        issue_id = n.get('id', '')
        identifier = n.get('identifier', '')
        title = n.get('title', '').replace('|', '-')
        team_key = n.get('team', {}).get('key', '')
        labels_json = json.dumps(
            [l.get('name', '') for l in n.get('labels', {}).get('nodes', [])]
        )
        print(f'{issue_id}|{identifier}|{title}|{team_key}|{labels_json}')
    # else: no output (empty stdout)
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    # Return empty on failure (do not hang)
" 2>>"$_LQ_LOG") || true

    # Output result to stdout (may be empty)
    echo "$result"
}

# ============================================================================
# linear_get_issue_team_key(ISSUE_UUID)
#
# Query issue(id).team.key for chain-trigger routing.
# Output to stdout: team key string (e.g., "INFRA")
# Empty stdout if not found.
# ============================================================================
linear_get_issue_team_key() {
    local issue_uuid="$1"

    if [ -z "$issue_uuid" ]; then
        return 0
    fi

    local api_key="${LINEAR_API_KEY:-}"
    if [ -z "$api_key" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] linear_get_issue_team_key: LINEAR_API_KEY not set" >> "$_LQ_LOG"
        return 0
    fi

    local result
    result=$(ISSUE_UUID="$issue_uuid" LINEAR_API_KEY="$api_key" /opt/homebrew/bin/python3 -c "
import json, urllib.request, os, sys, socket
socket.setdefaulttimeout(15)
issue_uuid = os.environ['ISSUE_UUID']
api_key = os.environ['LINEAR_API_KEY']
query = '{ issue(id: \"' + issue_uuid + '\") { team { key } } }'
req = urllib.request.Request(
    'https://api.linear.app/graphql',
    data=json.dumps({'query': query}).encode(),
    headers={'Authorization': api_key, 'Content-Type': 'application/json'},
    method='POST'
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read().decode())
        team_key = d.get('data', {}).get('issue', {}).get('team', {}).get('key', '')
        print(team_key)
except Exception as e:
    print('ERROR: ' + str(e), file=sys.stderr)
    # Return empty on failure
" 2>>"$_LQ_LOG") || true

    echo "$result"
}
