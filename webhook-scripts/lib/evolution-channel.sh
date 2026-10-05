#!/usr/bin/env bash
# lib/evolution-channel.sh — 演化卡通道识别库（Mac 通道分离，owner 第零条裁定 2026-10-04）
#
# 通道分离语义：演化卡不属于 Mac 自动处理域。识别为演化卡 → REJECTED 日志 +
# PostHog mac_evolution_rejected → 退出（零 droid exec 启动）。不是 skip 过滤
# （黑名单思维），是该类卡整体退出 Mac 自动执行域；唯一通道=闸门化 R1。
# 人工/对话触发入口保留（resume + 显式 session id 走会话预算，不经自动路径）。
#
# 识别 = title "[evolution]" 前缀（scanner 建卡恒带此前缀，创建时稳定信号，
#        解 GitHub→Linear 先建后贴 label 竞态）或归一化 labels（trim+大小写折叠）。
# 本库是防御纵深第二道；第一道在 Worker 侧（路径 D 演化事件零转发 Mac）。
#
# Caller setup（source 前可选）：
#   EVOLUTION_CHANNEL_LABEL — 演化标签名（默认 evolution-found）
#   POSTHOG_EVENT_NAME / POSTHOG_DISTINCT_ID / POSTHOG_DRY_RUN — lib/posthog.sh 约定
#   LOG_FILE — 日志目的地
#
# 共享测试向量：tests/evolution_channel_vectors.json（Worker 侧
# channels/test-vectors/ 同名副本，sha256 校验双仓同步）。
# shellcheck disable=SC2034,SC2154

# Guard against double-sourcing
# shellcheck disable=SC2317  # return 后的 || true 仅为 source-in-subshell 兜底
if [ -n "${_EVOLUTION_CHANNEL_SH_LOADED:-}" ]; then
    return 0 2>/dev/null || true
fi
_EVOLUTION_CHANNEL_SH_LOADED=1

SCRIPT_DIR_EVO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR_EVO}/lib/posthog.sh"

# Python binary 动态解析（posthog.sh 先例）：本库只用 stdlib（json/sys），
# 任意 python3 均可；macOS 生产环境回退 /opt/homebrew/bin/python3。
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || echo /opt/homebrew/bin/python3)}"

EVOLUTION_CHANNEL_LABEL="${EVOLUTION_CHANNEL_LABEL:-evolution-found}"

# ============================================================================
# evolution_normalize_label <raw>
# 归一化单个标签名：修剪首尾空白/换行/制表符 + ASCII 大小写折叠。
# ============================================================================
evolution_normalize_label() {
    local raw="${1:-}"
    printf '%s' "$raw" | "${PYTHON_BIN}" -c "
import sys
raw = sys.stdin.read()
print(raw.strip().casefold())
"
}

# ============================================================================
# evolution_is_evolution_card <title> <labels_json>
# 演化卡识别（归一化）。labels_json 必须是 JSON 字符串（null / 数组 / 其他）。
# 结果（调用方读取）：
#   EVOLUTION_DECISION ∈ evolution | normal
#   EVOLUTION_REASON    ∈ title_prefix | normalized_labels | ""
#   返回 0=演化卡（拒绝路径）1=非演化卡
# 判定顺序：title 前缀优先归因；非数组 labels 折叠为空后仅剩 title 信号。
# ============================================================================
evolution_is_evolution_card() {
    local title="${1:-}"
    local labels_json="${2:-null}"
    unset EVOLUTION_DECISION EVOLUTION_REASON

    local verdict
    verdict=$(TITLE_JSON="$(printf '%s' "$title" | "${PYTHON_BIN}" -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
    LABELS_JSON="$labels_json" LABEL="$EVOLUTION_CHANNEL_LABEL" "${PYTHON_BIN}" -c "
import json, os, sys

title = json.loads(os.environ['TITLE_JSON'])
label = os.environ['LABEL'].strip().casefold()

try:
    labels = json.loads(os.environ['LABELS_JSON'])
except (json.JSONDecodeError, TypeError):
    labels = None
if not isinstance(labels, list):
    labels = []

normalized = set()
for item in labels:
    if isinstance(item, str):
        normalized.add(item.strip().casefold())

if title.strip().casefold().startswith('[evolution]'):
    print('evolution title_prefix')
elif label in normalized:
    print('evolution normalized_labels')
else:
    print('normal -')
" 2>/dev/null)

    case "$verdict" in
        evolution*)
            EVOLUTION_DECISION="evolution"
            EVOLUTION_REASON="${verdict#evolution }"
            return 0
            ;;
        normal*)
            EVOLUTION_DECISION="normal"
            EVOLUTION_REASON=""
            return 1
            ;;
        *)
            # 解析器异常：fail-closed 由守卫层处理（此处按非演化放行，守卫检查 rc+decision）
            EVOLUTION_DECISION=""
            EVOLUTION_REASON=""
            return 1
            ;;
    esac
}

# ============================================================================
# evolution_reject_or_pass
# 通道分离守卫：演化卡 → REJECTED 日志 + PostHog mac_evolution_rejected → exit 0。
#
# 输入（调用方环境）：ISSUE_TITLE（回填后）、LINEAR_LABELS（payload labels JSON，可缺省）
# 前置豁免：ACTION=resume 且显式 RESUME_SESSION_ID（人工恢复痕迹）→ 放行。
# 幂等出口：exit 0（webhook 端 2xx，不触发重投递）。
# fail-closed：解析器崩溃/输出异常 → 按演化卡拒绝（封条宁可误拒不可漏放）。
# ============================================================================
evolution_reject_or_pass() {
    # 人工恢复豁免：显式 session id = owner 会话内明确动作（人工痕迹），
    # 位于演化拒绝之前——演化卡可人工恢复，但永不自动执行。
    if [ "$ACTION" = "resume" ] && [ -n "${RESUME_SESSION_ID:-}" ]; then
        return 0
    fi

    # 默认 fail-closed：识别器任何异常路径都按演化卡拒绝。
    local _rejected=1
    if evolution_is_evolution_card "${ISSUE_TITLE:-}" "${LINEAR_LABELS:-null}"; then
        _rejected=1
    elif [ "${EVOLUTION_DECISION:-}" = "normal" ]; then
        _rejected=0
    fi

    if [ "$_rejected" = "1" ]; then
        log "REJECTED: evolution card reached Mac (routed to R1 expected); dropped, not queued — 排队等待 R1 通道 ref=${ISSUE_REF:-unknown} uuid=${ISSUE_UUID:-unknown} signal=${EVOLUTION_REASON:-identifier_parse_failure}"
        send_posthog_event_named "mac_evolution_rejected" \
            "evolution_card_rejected" \
            "${ISSUE_REF:-unknown}" \
            "channel_separation" \
            "signal=${EVOLUTION_REASON:-identifier_parse_failure} uuid=${ISSUE_UUID:-unknown}"
        exit 0
    fi
    return 0
}
