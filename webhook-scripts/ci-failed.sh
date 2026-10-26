#!/usr/bin/env bash
# shellcheck disable=SC2034
# CI failed webhook handler
# In production, this script is routed via hooks.json (line 216) when ISSUE_ID is not set.
# The Linear issue reference is parameterized via ISSUE_ID env var; when unset, it falls back
# to legacy hardcoded INFRA-5. UseISSUE_ID when calling to avoid the legacy fallback.
# For CI failures, the primary route should be through the main droid-task pipeline via Linear gateway.
set -uo pipefail

LOG_DIR="${LOG_DIR:-${HOME}/.factory/webhook/logs}"
LOG_FILE="$LOG_DIR/ci-failed-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG_FILE"
}

# === PostHog 事件上报 (lib/posthog.sh 统一实现) ===
POSTHOG_EVENT_NAME="ci_failed_mcp_failure"
POSTHOG_DISTINCT_ID="ci-webhook"
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/posthog.sh"

log "CI failed hook triggered: pipeline=$CI_PIPELINE_ID project=$CI_PROJECT branch=$CI_BRANCH sha=$CI_SHA"

# Write comment to Linear issue about CI failure (triggers Droid via Linear webhook)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/lib/op-mcp.sh"
LINEAR_API_KEY=$(op_get_field "$OP_VAULT_SEVER" "uxsq45aoumghdysj3r5fe2fvqm" "凭据" || true)

if [ -z "$LINEAR_API_KEY" ]; then
    log "ERROR: Linear API key not found"
    send_posthog_event "mcp_key_retrieval_failed" "${CI_PIPELINE_ID:-unknown}" "auth" "MCP: op_get_field returned empty"
    exit 1
fi

# Get issue ID from Linear - parameterized via environment variable, with legacy fallback
# IN Momentum: Use ISSUE_ID env var from caller; otherwise use legacy hardcoded fallback
ISSUE_ID="${ISSUE_ID:-}"
if [ -z "$ISSUE_ID" ]; then
    # Legacy fallback for backward compatibility (legacy event: memory-core MR #41)
    ISSUE_ID="f023acdf-71d9-4121-8edf-a9ed8c7c05f7"
    log "WARNING: ISSUE_ID not provided, using legacy fallback (INFRA-5)"
fi

log "Using Linear issue ID: $ISSUE_ID"

# === 派发账本接线（VAL-BUD-002，observe-first）===
# 本脚本派发形态 = Linear comment → Linear webhook → trigger-droid（链式头）。
# 计量+记账落 comment 写入之前（本通道派发事实在此产生）。守卫式 source：
# lib 缺失（sync 漂移窗口）只禁用本次计量记账，禁绝派发即违规。
# budget rc 恒 0（超限/不可达只 WARN+遥测）；claim rc=2（账本不可用）fail-open 继续派发。
_LEDGER_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/dispatch-ledger.sh"
if [ -f "$_LEDGER_LIB" ]; then
    # shellcheck disable=SC1090,SC1091  # 沙盒/部署路径动态解析，非跟随面
    source "$_LEDGER_LIB"
else
    log "WARN: dispatch ledger lib unavailable (sync drift?), budget/ledger metering disabled this invocation"
fi
# claim 键 = 每次调用铸造独立 uuid4（与其他四成员同范式），ref 位保持 ISSUE_ID：
# 本通道派发事实是「写了 comment」，不是「本机执行了这张卡」；卡 UUID 是
# trigger-droid 的去重键空间（账本 first-writer-wins，borrow 先记即永久废掉
# 该卡 dedup 保护），且稳定键会让按记录计数的预算每卡终生只计 1 次。
# lib 未 source 时 PYTHON_BIN 可能未定义：默认回退 python3，铸造失败再退时间戳。
DISPATCH_UUID=$("${PYTHON_BIN:-python3}" -c 'import uuid; print(uuid.uuid4())' 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
if declare -F ledger_budget_check >/dev/null 2>&1; then
    ledger_budget_check "ci-failed" "ci-failed"
fi
if declare -F ledger_claim >/dev/null 2>&1; then
    ledger_claim "$DISPATCH_UUID" "ci-failed" "$ISSUE_ID" "ci failed comment dispatch" "" "ci-failed"
    _ledger_rc=$?
    ledger_append "$DISPATCH_UUID" "$ISSUE_ID" "dispatch_attempted" "source=ci-failed kind=ci-failed pipeline=${CI_PIPELINE_ID:-unknown}"
    if [ "$_ledger_rc" -eq 2 ]; then
        log "WARN: dispatch ledger unavailable, fail-open continue (issue=${ISSUE_ID})"
    fi
fi

# Write comment to Linear (this triggers the Linear webhook -> trigger-droid.sh -> Droid)
# Note: COMMENT_BODY is intentionally left generic; callers should provide via env var
COMMENT_BODY="${COMMENT_BODY:-CI 流水线失败，请修复。Pipeline: $CI_PIPELINE_ID, Branch: $CI_BRANCH}"

curl -s -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"mutation { commentCreate(input: { issueId: \\\"$ISSUE_ID\\\", body: \\\"$COMMENT_BODY\\\" }) { success } }\"}" >> "$LOG_FILE" 2>&1

log "Comment posted to Linear issue via webhook"

log "CI failed hook completed"
