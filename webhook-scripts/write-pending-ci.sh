#!/usr/bin/env bash
# shellcheck disable=SC2034
# write-pending-ci.sh - Write pending-ci-{PR_NUMBER}.json for CI webhook routing
# Usage: ~/.factory/webhook/scripts/write-pending-ci.sh [--source session|scanner] [--context <意图>] [--session-id <id>] <PR_NUMBER>
#
# M2 session_id tiered capture: schema {pr_number, cwd, created_at, session_id}
# - --session-id <id> explicit param wins
# - No flag: process-ancestry auto-detection was probed (2026-10-02) and is
#   structurally infeasible — the droid process is a SHARED daemon (one pid
#   serves many sessions), no pid→session registry exists anywhere, and the
#   process holds no session-anchor env/fd. So no explicit flag ⇒ write
#   explicit null + PostHog ci_session_id_unresolved. NEVER guess by
#   sessions-dir mtime (that heuristic was the Bug#002 misroute root cause).
# - trigger-ci-droid.sh consumes: id → probe that session only; null →
#   verify-not-rebind semantics (rt-verify-no-rebind)
#
# Backward compat: old pending files with session_id still work (trigger reads both)
#
# Env-overridable constants (for testing without /tmp copy + sed):
#   LOCKS_DIR     - directory for pending-ci files (default: ~/.factory/webhook/locks)
#   WEBHOOK_BASE  - base webhook directory (default: ~/.factory/webhook)
#   SESSIONS_INDEX - sessions-index.json path (default: ~/.factory/sessions-index.json)

set -euo pipefail

# === Env-overridable constants (M5: testability) ===
WEBHOOK_BASE="${WEBHOOK_BASE:-${HOME}/.factory/webhook}"
LOCKS_DIR="${LOCKS_DIR:-${WEBHOOK_BASE}/locks}"
SESSIONS_INDEX="${SESSIONS_INDEX:-${HOME}/.factory/sessions-index.json}"

# === 日志配置（VAL-INJ-010: LOG_FILE 赋值先于任何 send_posthog_event）===
LOG_DIR="${WEBHOOK_BASE}/logs"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="${LOG_DIR}/write-pending-ci-${TIMESTAMP}.log"
mkdir -p "$LOG_DIR" 2>/dev/null

# === PostHog 事件上报 (lib/posthog.sh 统一实现) ===
POSTHOG_EVENT_NAME="ci_webhook_failure"
POSTHOG_DISTINCT_ID="ci-webhook"
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/posthog.sh"

# Dynamic Python binary detection
PYTHON_BIN=${PYTHON_BIN:-$(command -v python3)}

# === F1: 选项解析框架 (--source session|scanner, --context <意图>, --session-id <id>) ===
SOURCE=""
CONTEXT=""
SESSION_ID_ARG=""
SOURCE_PROVIDED=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE="${2:-}"
      SOURCE_PROVIDED=true
      shift 2
      ;;
    --context)
      CONTEXT="${2:-}"
      shift 2
      ;;
    --session-id)
      SESSION_ID_ARG="${2:-}"
      shift 2
      ;;
    -*)
      echo "ERROR: Unknown option $1" >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

PR_NUMBER="${1:?Usage: write-pending-ci.sh [--source session|scanner] [--context <意图>] [--session-id <id>] <PR_NUMBER>}"

OUTPUT_FILE="$LOCKS_DIR/pending-ci-${PR_NUMBER}.json"

# source 合法性校验（fail-fast 先于写入，VAL-REG-004）
# 空字符串视为非法（VAL-REG-004: --source "" 必须 fail-fast）
# R1-P0A: 扩展 runner 值（runner 来源静默通过 watchdog）
if [[ "$SOURCE_PROVIDED" == "true" ]]; then
  case "$SOURCE" in
    session|scanner|runner) ;;
    *)
      echo "ERROR: Invalid source '$SOURCE' (must be 'session', 'scanner', or 'runner')" >&2
      exit 1
      ;;
  esac
fi

# --session-id sanity guard: it is consumed downstream as a Sessions API URL
# segment, so whitespace can only produce a garbage route — fail fast here.
if [[ "$SESSION_ID_ARG" =~ [[:space:]] ]]; then
  echo "ERROR: Invalid --session-id '$SESSION_ID_ARG' (must not contain whitespace)" >&2
  exit 1
fi

# PR_NUMBER must be a positive integer (mirror trigger-ci-droid.sh consumer guard)
if [[ ! "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Invalid PR_NUMBER='$PR_NUMBER' (must match ^[1-9][0-9]*$)"
  send_posthog_event "ci_invalid_pr_number" "${PR_NUMBER:-empty}" "validation" "format_error"
  exit 1
fi

# Create locks directory if it doesn't exist
mkdir -p "$LOCKS_DIR"

# Detect project root for cwd field
if ! PROJECT_CWD=$(git rev-parse --show-toplevel 2>/dev/null); then
  echo "ERROR: Not in a git repository. Run from a git project." >&2
  exit 1
fi

# Generate ISO 8601 timestamp (UTC)
CREATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# === M2 F5: session_id tiered capture ===
# Tier 1: --session-id explicit param. Tier 2 (ancestry auto-detection) was
# probed and is structurally infeasible (shared droid daemon, no pid→session
# registry, no session-anchor env/fd) ⇒ fall straight through to Tier 3:
# write explicit null + PostHog ci_session_id_unresolved. NEVER guess by
# sessions-dir mtime — that heuristic is the Bug#002 misroute root cause.
# Webhook semantics: fall-open, exit 0 (telemetry is best-effort, never fatal).
if [[ -n "$SESSION_ID_ARG" ]]; then
  SESSION_ID="$SESSION_ID_ARG"
else
  SESSION_ID=""
  SESSION_UNRESOLVED=true
fi

# === F1: Schema — M5 base fields + optional source/context + session_id (explicit string or null) ===
TMP_FILE="${OUTPUT_FILE}.tmp.$$"
"$PYTHON_BIN" -c "
import json, sys
data = {
    'pr_number': sys.argv[1],
    'cwd': sys.argv[2],
    'created_at': sys.argv[3],
    'session_id': sys.argv[4] if sys.argv[4] else None
}
# 仅当 --source 显式传入时写入（VAL-REG-002：旧式调用无 source 键）
if sys.argv[5]:  # SOURCE
    data['source'] = sys.argv[5]
if sys.argv[6]:  # CONTEXT
    data['context'] = sys.argv[6]
with open(sys.argv[7], 'w') as f:
    json.dump(data, f)
" "$PR_NUMBER" "$PROJECT_CWD" "$CREATED_AT" "$SESSION_ID" "$SOURCE" "$CONTEXT" "$TMP_FILE"

# Verify tmp file is valid JSON before mv
if ! "$PYTHON_BIN" -c "import json; json.load(open('$TMP_FILE'))" 2>/dev/null; then
  echo "ERROR: Generated pending file is not valid JSON. Aborting." >&2
  rm -f "$TMP_FILE"
  exit 1
fi

mv -f "$TMP_FILE" "$OUTPUT_FILE"

# M2 F5 Tier 3: session_id unresolved → PostHog alert (after successful write,
# best-effort; POSTHOG_DRY_RUN prints instead of sending)
if [[ "${SESSION_UNRESOLVED:-false}" == "true" ]]; then
  send_posthog_event "ci_session_id_unresolved" "$PR_NUMBER" "session_capture" "no --session-id; ancestry probe structurally infeasible (shared daemon); wrote null"
fi

if [[ -n "$SOURCE" ]]; then
  echo "pending-ci-${PR_NUMBER}.json written (atomic, M5+F1 schema): pr_number=$PR_NUMBER, source=$SOURCE, created_at=$CREATED_AT, cwd=$PROJECT_CWD, session_id=${SESSION_ID:-null}"
else
  echo "pending-ci-${PR_NUMBER}.json written (atomic, M2 schema): pr_number=$PR_NUMBER, created_at=$CREATED_AT, cwd=$PROJECT_CWD, session_id=${SESSION_ID:-null}"
fi
