#!/bin/bash
# shellcheck disable=SC2034
# lib/evolution-guard.sh — 路径 G 演化守卫（owner 2026-10-04 裁定 a，VAL-GATE-020）
#
# spawn_fallback / trigger-error-droid 自动修复开枪前的演化标记检查：
#
#   阳性（pending-ci context 或事件上下文含 r1-evolution 标记）
#     → halt_evolution：跳过执行 + 日志/PostHog 告警（不自动修复）。
#       修补唯一入口=人工重打 r1-cleared 过 admitDispatch 闸门。
#   阴性 / 文件缺失 / JSON 损坏 / 字段非字符串（不可判定）
#     → proceed：照常执行。owner 普通 CI 自愈零改动，阳性识别才拦截。
#
# fail-open 语义（与 mac-channel-separation 的 fail-closed 方向相反）：
# 通道分离是「演化卡到达 Mac=不变量破坏」的封条；本守卫只拦阳性证据充分的
# 自动修复执行，证据不足一律放行——杜绝不可判定输入误伤普通自愈。
#
# R1 派发契约：R1 派发的 PR 注册路由时必须带 --context r1-evolution
# （write-pending-ci.sh 落盘 pending-ci-{PR}.json context 字段）。
#
# 调用约定：
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/evolution-guard.sh"
#
#   EVOLUTION_GUARD_CONTEXT="..."          # 可选：事件上下文（error-droid 面）
#   _evolution_guard_run "$PENDING_CI_FILE"
#   [ "$EVOLUTION_GUARD_RESULT" = "halt_evolution" ] && _evolution_guard_alert ... && exit 0
#
# 判定语义：事件上下文与文件 context 任一阳性即 halt（OR；round-2 P2 修复，
# 原 env 绝对优先在 env 阴性 + 文件阳性时 fail-open）。
#
# 结果（全局变量）：EVOLUTION_GUARD_RESULT ∈ halt_evolution | proceed

# Guard against double-sourcing
if [ -n "${_EVOLUTION_GUARD_SH_LOADED:-}" ]; then
    # shellcheck disable=SC2317
    return 0 2>/dev/null || true
fi
_EVOLUTION_GUARD_SH_LOADED=1

EVOLUTION_GUARD_MARKER="${EVOLUTION_GUARD_MARKER:-r1-evolution}"

# PostHog 告警依赖（posthog.sh 自带 double-source 守卫，重复 source 安全；
# 调用方已 source 时为 no-op）。调用方需先行设置 POSTHOG_EVENT_NAME /
# POSTHOG_DISTINCT_ID（lib/posthog.sh 约定）。
SCRIPT_DIR_EVO_GUARD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR_EVO_GUARD}/lib/posthog.sh"

# _evolution_guard_run <pending_ci_file>
# 读 context 判定。事件上下文（EVOLUTION_GUARD_CONTEXT）与文件 context
# 任一阳性即 halt（OR 语义，round-2 P2 修复：原 env 优先短路在
# 「env 阴性 + 文件阳性」组合下 fail-open，process-global env 泄漏会静默
# 关掉文件面检测）；两者皆不可判定 → proceed。

_evolution_guard_run() {
    local pending_file="${1:-}"
    EVOLUTION_GUARD_RESULT="proceed"

    local env_positive=0
    local file_positive=0

    local env_context=""
    if [ -n "${EVOLUTION_GUARD_CONTEXT:-}" ]; then
        env_context="$EVOLUTION_GUARD_CONTEXT"
    fi
    local normalized_env
    normalized_env=$(printf '%s' "$env_context" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    local marker
    marker=$(printf '%s' "$EVOLUTION_GUARD_MARKER" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    if [ -n "$normalized_env" ] && printf '%s' "$normalized_env" | grep -qF -- "$marker"; then
        env_positive=1
    fi

    if [ "$env_positive" -eq 0 ] && [ -n "$pending_file" ] && [ -f "$pending_file" ]; then
        local file_context
        file_context=$("${PYTHON_BIN:-python3}" -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
    value = data.get('context')
    print(value if isinstance(value, str) else '')
except Exception:
    print('')
" "$pending_file" 2>/dev/null) || file_context=""
        local normalized_file
        normalized_file=$(printf '%s' "$file_context" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
        if [ -n "$normalized_file" ] && printf '%s' "$normalized_file" | grep -qF -- "$marker"; then
            file_positive=1
        fi
    fi

    if [ "$env_positive" -eq 1 ] || [ "$file_positive" -eq 1 ]; then
        EVOLUTION_GUARD_RESULT="halt_evolution"
    fi
}

# _evolution_guard_alert <event_type> <identifier>
# 告警出口：日志行（含不自动修复与修补唯一入口措辞）+ PostHog 独立事件。
# 使用 send_posthog_event_named（独立事件名，区别于调用方默认 ci_webhook_failure），
# r1 告警事件不配 Alert destination（owner 侧约束，防 error-gateway 回环）。
_evolution_guard_alert() {
    local event_type="${1:-evolution_guard_blocked}"
    local identifier="${2:-unknown}"

    # log 由调用方提供（trigger-ci-droid.sh tee 双写；测试面 stub 为 stdout echo）
    log "EVOLUTION-GUARD: ${event_type} id=${identifier} — R1 派发的演化修复上下文，跳过自动执行（不自动修复；修补唯一入口=人工重打 r1-cleared 过闸）"
    send_posthog_event_named "evolution_guard_blocked" \
        "$event_type" \
        "$identifier" \
        "evolution_guard" \
        "marker=${EVOLUTION_GUARD_MARKER} halt_reason=${event_type}"
}
