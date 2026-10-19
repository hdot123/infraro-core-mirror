#!/bin/bash
# shellcheck disable=SC2034
# ci-timeout-watchdog.sh - TTL watchdog for pending CI notifications
# Scans ~/.factory/webhook/locks/pending-ci-*.json
# Phase A: If created_at > 30 minutes without injected_at, sends PostHog event and deletes file
# Phase B: If injected_at > 45 minutes and PR not merged, spawns fallback and deletes file
# Idempotent: safe to run multiple times

set -uo pipefail

# Dynamic Python binary detection (macOS: /opt/homebrew/bin/python3, Linux: python3)
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || echo /opt/homebrew/bin/python3)}"

# === Configuration ===
LOCKS_DIR="${LOCKS_DIR:-$HOME/.factory/webhook/locks}"
TTL_SECONDS=1800          # 30 minutes (Phase A)
INJECTION_TTL_SECONDS=2700 # 45 minutes (Phase B)

# === PostHog 事件上报 (lib/posthog.sh 统一实现) ===
POSTHOG_EVENT_NAME="ci_webhook_failure"
POSTHOG_DISTINCT_ID="ci-webhook"
# NF-4（终批 #5，2026-10-15）：PostHog key 迁出 launchd plist 明文——
# plist EnvironmentVariables 中的 POSTHOG_API_KEY 字面量改为脚本内解析：
# env 优先（测试/显式注入面），空则 op-mcp 兜底（trigger-ci-droid.sh
# FACTORY_TOKEN 先例 :744-756）。key 仍缺 → posthog.sh 自跳过上报（只降级
# 上报面，扫描/派发主流程不依赖）。vault/item 定位常量：
#   vault = OP_VAULT_SEVER（op-mcp.sh 定义）
#   item  = 1Password「PostHog / Project API Key / memory-core」
#           (euar3izxbu7grsje3r7kuuxqza) 字段 credential——已按 sha256
#           指纹与原 plist 值比对确认同源，零换 key。
POSTHOG_API_KEY="${POSTHOG_API_KEY:-}"
if [ -z "$POSTHOG_API_KEY" ] && [ -z "${POSTHOG_DRY_RUN:-}" ]; then
  _WATCHDOG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "${_WATCHDOG_SCRIPT_DIR}/lib/op-mcp.sh" ]; then
    # shellcheck disable=SC1090,SC1091  # 部署路径动态解析，非跟随面
    # shellcheck source=lib/op-mcp.sh
    source "${_WATCHDOG_SCRIPT_DIR}/lib/op-mcp.sh"
    POSTHOG_API_KEY=$(op_get_field "$OP_VAULT_SEVER" "euar3izxbu7grsje3r7kuuxqza" "credential" || true)
  fi
  if [ -z "$POSTHOG_API_KEY" ]; then
    echo "[watchdog] POSTHOG_API_KEY unavailable (env empty, op-mcp fallback failed) — PostHog reporting skipped this run"
  fi
fi
# VAL-INJ-009: 回执落盘——LOG_FILE 赋值先于 source lib/posthog.sh
# 确保 send_posthog_event 的 curl 回执写入真实日志文件，非 /dev/null
LOG_DIR="${LOG_DIR:-${HOME}/.factory/webhook/logs}"
mkdir -p "$LOG_DIR" 2>/dev/null
LOG_FILE="${LOG_DIR}/ci-timeout-watchdog-posthog.log"
# lib/evolution-guard.sh 的 _evolution_guard_alert 依赖 caller 提供 log()
# （lib 契约同 trigger-ci-droid.sh:257；缺失时阳性拦截报 log: command not
# found，EVOLUTION-GUARD 日志行丢失，macOS 还会被 /usr/bin/log 吞掉）
# SC2317: shellcheck ≤0.10.0 无法追踪 sourced lib 的间接调用，把未直接
# 调用的 log() 函数体判为 unreachable（0.11.0 已修复该误报，CI 仍固定 0.10.0）
# shellcheck disable=SC2329,SC2317  # 经 source 的 lib 间接调用
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/posthog.sh"
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/p0a-guard.sh"
# shellcheck source=/dev/null
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/evolution-guard.sh"

# Cross-platform mtime (macOS stat -f %m / GNU stat -c %Y)
# Conditional assignment avoids GNU stdout leak into $() capture
# (GNU stat treats -f as --file-system, leaking fs-listing stdout)
_portable_mtime() {
  local _f="$1" _ts
  if ! _ts=$(stat -f %m "$_f" 2>/dev/null); then
    _ts=$(stat -c %Y "$_f" 2>/dev/null || echo 0)
  fi
  printf '%s' "$_ts"
}

# === 演化守卫告警去重（alert-once）===
# 阳性 halt 保留 pending 文件（防重扫绕过），但 5min 重扫会无限重复
# evolution_guard_blocked 事件——首警后写 evolution_alerted_at 标记，
# 后续重扫静默跳过（文件仍保留，bypass 防御语义不变）。
_evolution_alert_once() {
  local pending_file="$1"
  local event_type="$2"
  local pr_number="$3"
  local alerted_at
  alerted_at=$($PYTHON_BIN -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(json.load(f).get('evolution_alerted_at', ''))
except Exception:
    print('')
" "$pending_file" 2>/dev/null)
  if [ -n "$alerted_at" ]; then
    echo "PR #$pr_number evolution guard already alerted at $alerted_at, skipping duplicate alert"
    return 0
  fi
  _evolution_guard_alert "$event_type" "$pr_number"
  # 回写标记（尽力而为；失败不阻断——下一轮重扫最多多一次告警）
  $PYTHON_BIN -c "
import json, sys
from datetime import datetime, timezone
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    data['evolution_alerted_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    with open(sys.argv[1], 'w') as f:
        json.dump(data, f)
except Exception:
    pass
" "$pending_file" 2>/dev/null
}

# === Spawn fallback for lost messages ===
spawn_fallback() {
  local pr_num="$1"
  local ci_status="$2"
  local repo_path="$3"
  local lock_file="$LOCKS_DIR/ci-fallback-${pr_num}.lock"

  # 派发账本接线（VAL-BUD-002，observe-first）：计量+记账在 spawn 判定之前
  # （ECHO_DROID 干跑也计量——预算面统计的是「watchdog 想派发」这个事实）。
  # 守卫式 source：lib 缺失（sync 漂移窗口）只禁用本次计量记账，禁绝派发即违规。
  # budget rc 恒 0（超限/不可达只 WARN+遥测）；claim rc=2（账本不可用）fail-open 继续派发。
  local _ledger_lib
  _ledger_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/dispatch-ledger.sh"
  if [ -f "$_ledger_lib" ]; then
    # shellcheck disable=SC1090,SC1091  # 沙盒/部署路径动态解析，非跟随面
    source "$_ledger_lib"
  else
    echo "WARN: dispatch ledger lib unavailable (sync drift?), budget/ledger metering disabled this invocation"
  fi
  DISPATCH_UUID=$($PYTHON_BIN -c 'import uuid; print(uuid.uuid4())' 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
  if declare -F ledger_budget_check >/dev/null 2>&1; then
    ledger_budget_check "ci-fallback" "ci-fallback"
  fi
  if declare -F ledger_claim >/dev/null 2>&1; then
    ledger_claim "$DISPATCH_UUID" "ci-fallback" "INFRA-${pr_num}" "ci fallback PR #${pr_num}" "" "ci-fallback"
    local _ledger_rc=$?
    ledger_append "$DISPATCH_UUID" "INFRA-${pr_num}" "dispatch_attempted" "source=ci-fallback kind=ci-fallback status=${ci_status}"
    if [ "$_ledger_rc" -eq 2 ]; then
      echo "WARN: dispatch ledger unavailable, fail-open continue (pr=${pr_num})"
    fi
  fi

  # Create fallback lock to prevent duplicate spawns (idempotent)
  # Only spawn if lock doesn't exist or is older than 1 hour
  if [ -f "$lock_file" ]; then
    local lock_age=$(( $(date +%s) - $(_portable_mtime "$lock_file") ))
    if [ "$lock_age" -lt 3600 ]; then
      echo "Fallback lock exists and is $lock_age seconds old, skipping duplicate spawn"
      return 0
    fi
    echo "Stale fallback lock ($lock_age seconds old), removing"
    rm -f "$lock_file"
  fi
  date +%Y%m%d-%H%M%S > "$lock_file"
  echo "Created fallback lock: $lock_file"

  echo "Spawning fallback session for PR #$pr_num (CI status: $ci_status)"

  # Determine if we need to spawn a droid session for branch cleanup
  # This is triggered when the main CI injection was lost
  local droid_bin="${DROID_BIN:-$(command -v droid || echo /usr/local/bin/droid)}"
  local prompt="CI webhook injection lost for PR #$pr_num (status: $ci_status). Repository: $repo_path. Please verify PR merge status and perform branch cleanup if needed: check if PR #$pr_num is merged, if so delete the remote branch and update local main branch. If not merged, investigate why."
  local tag="ci-fallback-$pr_num"

  # Dry-run guard: prevent real droid sessions during testing
  if [ "${ECHO_DROID:-0}" = "1" ]; then
    echo "[ECHO_DROID] Would run: $droid_bin exec --auto high --tag '$tag' '$prompt'"
    echo "Fallback session dry-run complete (ECHO_DROID=1)"
    return 0
  fi

  echo "Executing: $droid_bin exec --auto high --tag '$tag' '$prompt'"

  # Spawn new session in background (nohup preserves execution after webhook returns)
  nohup "$droid_bin" exec --auto high --tag "$tag" "$prompt" > /dev/null 2>&1 &
  local spawn_pid=$!

  # Brief delay + kill -0 probe to detect immediate spawn failure
  # (nohup ... & returns 0 immediately; $? is always 0 — previous dead code)
  sleep 2
  if ! kill -0 "$spawn_pid" 2>/dev/null; then
    # Process already exited — collect exit status if available
    wait "$spawn_pid" 2>/dev/null
    local spawn_exit=$?
    echo "ERROR: Failed to spawn fallback session (PID=$spawn_pid, exit=$spawn_exit)"
    send_posthog_event "ci_fallback_spawn_failed" "$pr_num" "fallback_spawn" "exit=$spawn_exit,pid=$spawn_pid"
    return 1
  fi

  echo "Fallback session spawned successfully (PID=$spawn_pid)"
}

# === Create locks directory if missing ===
if [ ! -d "$LOCKS_DIR" ]; then
  mkdir -p "$LOCKS_DIR"
  exit 0
fi

# === Scan pending-ci-*.json files ===
CURRENT_TIME=$(date +%s)

for pending_file in "$LOCKS_DIR"/pending-ci-*.json; do
  # Skip if no files match the pattern
  [ -f "$pending_file" ] || continue
  
  # Parse JSON to extract fields
  PR_NUMBER=$($PYTHON_BIN -c "
import json
try:
    with open('$pending_file') as f:
        print(json.load(f).get('pr_number', ''))
except:
    print('')
" 2>/dev/null)
  
  CREATED_AT=$($PYTHON_BIN -c "
import json
try:
    with open('$pending_file') as f:
        print(json.load(f).get('created_at', ''))
except:
    print('')
" 2>/dev/null)
  
  INJECTED_AT=$($PYTHON_BIN -c "
import json
try:
    with open('$pending_file') as f:
        print(json.load(f).get('injected_at', ''))
except:
    print('')
" 2>/dev/null)
  
  CWD=$($PYTHON_BIN -c "
import json
try:
    with open('$pending_file') as f:
        print(json.load(f).get('cwd', ''))
except:
    print('')
" 2>/dev/null)
  
  SOURCE=$($PYTHON_BIN -c "
import json
try:
    with open('$pending_file') as f:
        print(json.load(f).get('source', ''))
except:
    print('')
" 2>/dev/null)
  
  # Skip if parsing failed or fields are empty
  if [ -z "$PR_NUMBER" ] || [ -z "$CREATED_AT" ]; then
    continue
  fi
  
  # Calculate created_at age in seconds
  CREATED_EPOCH=$($PYTHON_BIN -c "
from datetime import datetime, timezone
import sys
try:
    created_at = '$CREATED_AT'
    # Parse ISO 8601 timestamp
    if created_at.endswith('Z'):
        created_at = created_at[:-1] + '+00:00'
    created_time = datetime.fromisoformat(created_at)
    print(int(created_time.timestamp()))
except Exception as e:
    print('0', file=sys.stderr)
    print('0')
" 2>/dev/null)
  
  # Skip if timestamp parsing failed
  if [ "$CREATED_EPOCH" = "0" ]; then
    continue
  fi
  
  CREATED_AGE_SECONDS=$((CURRENT_TIME - CREATED_EPOCH))
  
  # === Phase B: Check if injected but not consumed ===
  if [ -n "$INJECTED_AT" ]; then
    INJECTED_EPOCH=$($PYTHON_BIN -c "
from datetime import datetime, timezone
import sys
try:
    injected_at = '$INJECTED_AT'
    if injected_at.endswith('Z'):
        injected_at = injected_at[:-1] + '+00:00'
    injected_time = datetime.fromisoformat(injected_at)
    print(int(injected_time.timestamp()))
except Exception as e:
    print('0', file=sys.stderr)
    print('0')
" 2>/dev/null)
    
    if [ "$INJECTED_EPOCH" = "0" ]; then
      # VAL-INJ-008: 不可恢复样本单次告警 + 终态处理（重命名 .malformed，防重复扫描）
      # 不再每 5 分钟重复告警（基线 :188-190 裸 continue 缺陷）
      echo "Warning: Could not parse injected_at timestamp for PR #$PR_NUMBER — marking as malformed (terminal)"
      send_posthog_event "ci_malformed_timestamp" "$PR_NUMBER" "watchdog_phase_b" "unrecoverable_format"
      mv "$pending_file" "${pending_file}.malformed"
      continue
    fi
    
    INJECTED_AGE_SECONDS=$((CURRENT_TIME - INJECTED_EPOCH))
    
    # Check if injected message was not consumed within 45 minutes
    if [ "$INJECTED_AGE_SECONDS" -gt "$INJECTION_TTL_SECONDS" ]; then
      INJECTED_AGE_MINUTES=$((INJECTED_AGE_SECONDS / 60))
      
      echo "Phase B: PR #$PR_NUMBER injected $INJECTED_AGE_MINUTES minutes ago but not consumed"
      
      # M3 (VAL-M3-001): Derive owner/repo from pending file's cwd for gh pr view -R
      # Without -R, gh pr view fails in non-git directories (e.g., plist WorkingDirectory)
      PR_REPO=""
      if [ -n "$CWD" ] && [ -d "$CWD" ]; then
        PR_REPO=$(git -C "$CWD" remote get-url origin 2>/dev/null \
          | /usr/bin/sed -E 's#^https?://[^/]+/##;s#^git@[^:]+:##;s#\.git$##' || true)
      fi

      # Check if PR is already merged (with -R when repo is derivable)
      if [ -n "$PR_REPO" ]; then
        PR_MERGED=$($PYTHON_BIN -c "
import subprocess, sys
try:
    result = subprocess.run(['gh', 'pr', 'view', '$PR_NUMBER', '-R', '$PR_REPO', '--json', 'state'],
                          capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        import json
        data = json.loads(result.stdout)
        print(data.get('state', 'UNKNOWN'))
    else:
        print('UNKNOWN')
except Exception as e:
    print(f'Error checking PR: {e}', file=sys.stderr)
    print('UNKNOWN')
" 2>/dev/null)
      else
        echo "Phase B: WARNING cannot derive owner/repo from cwd='$CWD', marking UNKNOWN"
        PR_MERGED="UNKNOWN"
      fi

      if [ "$PR_MERGED" = "MERGED" ]; then
        # PR is merged, message was consumed or branch cleanup will handle it
        echo "PR #$PR_NUMBER is already merged, cleaning up pending-ci file"
        rm -f "$pending_file"
      elif [ "$PR_MERGED" = "UNKNOWN" ]; then
        # M3 (VAL-M3-001): UNKNOWN = query failed — alert only, do NOT spawn
        # Spawning on UNKNOWN caused false fallback sessions when gh couldn't query
        echo "Phase B: PR #$PR_NUMBER query returned UNKNOWN (gh pr view failed), alerting only — no spawn"
        send_posthog_event "ci_pr_status_unknown" "$PR_NUMBER" "watchdog_phase_b" "gh_pr_view_failed,repo=${PR_REPO:-none}"
      else
        # P0-A 源感知守卫：开枪前三查（runner 静默 / sentinel 让路 / autofix 降级）
        # SOURCE 已在循环开头解析，直接使用
        _p0a_guard_run "$PR_NUMBER" "$SOURCE" "$PR_REPO"
        if [ "$P0A_GUARD_RESULT" = "silent_runner" ]; then
          echo "Phase B: PR #$PR_NUMBER skipped (runner source, silent)"
          rm -f "$pending_file"
        elif [ "$P0A_GUARD_RESULT" = "yield_sentinel" ]; then
          echo "Phase B: PR #$PR_NUMBER skipped (autofix sentinel found, yielding)"
          rm -f "$pending_file"
        elif [ "$P0A_GUARD_RESULT" = "alert_only" ]; then
          echo "Phase B: PR #$PR_NUMBER degraded to warning (autofix active, not spawning)"
          rm -f "$pending_file"
        else
          # 路径 G 演化守卫（INFRA-1302 / VAL-GATE-020 同款，watchdog 派发面）：
          # context 阳性含 r1-evolution → 不开枪（不自动修复，修补唯一入口=
          # 人工重打 r1-cleared 过闸）；阴性/文件缺失/损坏 → 照常执行。
          # 阳性保留 pending 文件：watchdog 每 5min 重扫，删除会让下一轮
          # 重走 Phase B 无守卫判定（与 trigger-ci expired 路径 P1 修复同因）。
          _evolution_guard_run "$pending_file"
          if [ "$EVOLUTION_GUARD_RESULT" = "halt_evolution" ]; then
            echo "Phase B: PR #$PR_NUMBER r1-evolution context, halting (EVOLUTION-GUARD)"
            _evolution_alert_once "$pending_file" "watchdog_phase_b_evolution_blocked" "$PR_NUMBER"
          else
            # OPEN or other non-merged state — injection was lost, spawn fallback
            echo "PR #$PR_NUMBER not merged (state: $PR_MERGED), spawning fallback"
            send_posthog_event "ci_injection_lost" "$PR_NUMBER" "watchdog_phase_b" "injected=${INJECTED_AGE_MINUTES}min, not consumed"
            spawn_fallback "$PR_NUMBER" "injection_lost" "$CWD"
            rm -f "$pending_file"
          fi
        fi
      fi
    fi
    
    # Skip Phase A if injected_at exists
    continue
  fi
  
  # === Phase A: Check if created but not injected ===
  if [ "$CREATED_AGE_SECONDS" -gt "$TTL_SECONDS" ]; then
    AGE_MINUTES=$((CREATED_AGE_SECONDS / 60))
    
    # F1: Skip scanner source — no fallback, silent cleanup only (VAL-REG-011)
    if [ "$SOURCE" = "scanner" ]; then
      echo "Phase A: PR #$PR_NUMBER is scanner source, silent cleanup only (no fallback)"
      rm -f "$pending_file"
      continue
    fi
    
    # P0-A 源感知守卫：开枪前三查（runner 静默 / sentinel 让路 / autofix 降级）
    # 调用 lib/p0a-guard.sh 的 _p0a_guard_run，结果写入全局变量 P0A_GUARD_RESULT
    _p0a_guard_run "$PR_NUMBER" "$SOURCE" ""
    case "$P0A_GUARD_RESULT" in
      silent_runner)
        echo "Phase A: PR #$PR_NUMBER skipped (source=runner, silent cleanup)"
        rm -f "$pending_file"
        continue
        ;;
      yield_sentinel)
        echo "Phase A: PR #$PR_NUMBER skipped (autofix sentinel found, yielding to autofix)"
        rm -f "$pending_file"
        continue
        ;;
      alert_only)
        echo "Phase A: PR #$PR_NUMBER degraded to warning (AUTOFIX_AUTO_ENABLED=true, not spawning fallback)"
        send_posthog_event "p0a_autofix_degraded" "$PR_NUMBER" "watchdog_phase_a" "autofix_active,source=${SOURCE:-unknown}"
        rm -f "$pending_file"
        continue
        ;;
      proceed|*)
        # 继续原有逻辑：发送 PostHog 事件并触发 fallback
        ;;
    esac
    
    # 路径 G 演化守卫（INFRA-1302 / VAL-GATE-020 同款，watchdog 派发面）：
    # context 阳性含 r1-evolution → 不开枪、保留 pending 文件（防 5min 重扫
    # 绕过守卫，与 Phase B/trigger-ci expired 路径同因）；阴性/不可判定放行。
    _evolution_guard_run "$pending_file"
    if [ "$EVOLUTION_GUARD_RESULT" = "halt_evolution" ]; then
      echo "Phase A: PR #$PR_NUMBER r1-evolution context, halting (EVOLUTION-GUARD)"
      _evolution_alert_once "$pending_file" "watchdog_phase_a_evolution_blocked" "$PR_NUMBER"
      continue
    fi

    # Send PostHog timeout event
    send_posthog_event "ci_notification_timeout" "$PR_NUMBER" "watchdog_phase_a" "age=${AGE_MINUTES}min, not injected"
    
    # Spawn fallback for unprocessed CI
    echo "Phase A: PR #$PR_NUMBER pending for $AGE_MINUTES minutes, spawning fallback"
    spawn_fallback "$PR_NUMBER" "injection_timeout" "$CWD"
    
    # Delete stale file (idempotent)
    rm -f "$pending_file"
  fi
done

exit 0
