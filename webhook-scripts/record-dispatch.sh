#!/bin/bash
# shellcheck disable=SC2317
# record-dispatch.sh — droid-task 接收闸记账脚本（VAL-BUD-004，a′ 收门 observe-first）
#
# 用法: record-dispatch.sh <source> <ref> [kind] [title]
#   source 派发面标识（如 droid-task）；ref 卡号（如 INFRA-xxxx）
#   kind   派发种类（linear-gateway / error-gateway，缺省 linear-gateway）
#   title  卡片标题（可选，进记录 title 面便于对账）
#
# 内部：source lib/dispatch-ledger.sh → ledger_budget_check（超预算 WARN+遥测，
# rc 恒 0）→ ledger_claim + ledger_append 记账。
#
# 语义（用户裁定 2026-10-10「第一步规范不掐死」）：本脚本只做计量与记账，
# **不产生任何拒发路径**——所有失败形态（缺参/lib 缺失/账本不可用/claim 撞车）
# 都只 WARN 后 exit 0；调用方（droid-task.yml 预算步骤）再以 || true 兜底双保险。
# 与 Mac 侧接线（trigger-release 等）同款守卫式 source：lib 缺失（sync 漂移
# 窗口）只禁用本次计量记账，禁绝派发即违规。
#
# UUID 生成：脚本进程内 python3 uuid4（账本库 PYTHON_BIN 同源解析）；生成失败
# 回退时间戳形态键（uuid 键要求仅由去重消费方依赖，droid-task 侧无跨通道去重）。
#
# 约定：WEBHOOK_BASE / LEDGER_BASE_TS / POSTHOG_* 由调用方环境继承（工作流
# 步骤按需注入；未注入时 WEBHOOK_BASE 回落 ${HOME}/.factory/webhook 生产落点，
# 与 dispatch-ledger.sh 既有语义一致）。

set -u

SOURCE="${1:-}"
REF="${2:-}"
KIND="${3:-linear-gateway}"
TITLE="${4:-}"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$SOURCE" ] || [ -z "$REF" ]; then
    printf '[RECORD-DISPATCH] WARN usage: record-dispatch.sh <source> <ref> [kind] [title]\n' >&2
    exit 0
fi

_LEDGER_LIB="${_SCRIPT_DIR}/lib/dispatch-ledger.sh"
if [ -f "$_LEDGER_LIB" ]; then
    # shellcheck disable=SC1090,SC1091  # 沙盒/部署路径动态解析，非跟随面
    source "$_LEDGER_LIB"
else
    printf '[RECORD-DISPATCH] WARN: dispatch ledger lib unavailable (sync drift?), budget/ledger metering disabled this invocation\n' >&2
    exit 0
fi

# 预算计量（rc 恒 0：超限/不可达只 WARN + budget_* 遥测，永远放行）
if declare -F ledger_budget_check >/dev/null 2>&1; then
    ledger_budget_check "$SOURCE" "$KIND"
fi

# 记账（原子 no-clobber；rc=2 账本不可用 fail-open）
DISPATCH_UUID=$("${PYTHON_BIN}" -c 'import uuid; print(uuid.uuid4())' 2>/dev/null) \
    || DISPATCH_UUID="dt-$(date -u +%Y%m%dT%H%M%SZ)-$$"
if declare -F ledger_claim >/dev/null 2>&1; then
    ledger_claim "$DISPATCH_UUID" "$SOURCE" "$REF" "${TITLE:0:200}" "" "$KIND"
    _ledger_rc=$?
    ledger_append "$DISPATCH_UUID" "$REF" "dispatch_attempted" "source=${SOURCE} kind=${KIND} face=receiver"
    if [ "$_ledger_rc" -eq 2 ]; then
        printf '[RECORD-DISPATCH] WARN: dispatch ledger unavailable, fail-open continue (ref=%s)\n' "$REF" >&2
    fi
fi

exit 0
