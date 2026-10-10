#!/bin/bash
# shellcheck disable=SC2034
# lib/dispatch-ledger.sh — 共用派发账本（VAL-LEDGER-002，MS4 账本与退役）
#
# 语义（mission shared-ledger / architecture §6 M5）：
# - 键 = Linear UUID（跨团队移动/编号变更不变；INFRA-xxx 只是展示面，不作键）
# - append-only：一卡一派发记录。record 文件创建后不改写；重复 claim = 去重命中
#   （原子 no-clobber：tmp 写入后 ln 硬链接落位，ln 失败=记录已存在）
# - 宿主 = ${WEBHOOK_BASE}/status/ 文件族目录扩展（trigger-droid.sh STATUS_DIR 先例）：
#   status/ledger/<uuid>.json 为记录本体，status/ledger/by-ref/<INFRA-ref>.json 为
#   symlink 视图（人查/对账入口；卡跨团队变号时重链，record 本体永不改写）
# - status/ledger/events.jsonl 追加事件流（claim/ref_moved 留痕，影子周对账数据源）
#
# 消费方：trigger-droid.sh（派发前 claim 去重）、reconcile-evolution.sh
# （GAP-A 补偿创建写账本）。与 R1 派发共用同一账本语义（一卡一派发记录）。
#
# 失败语义 fail-open（与 R1 Worker 闸门 fail-closed 方向相反）：账本是去重与
# 审计面、不是预算闸门；账本不可用只降级（WARN），不瘫痪既有派发链路——
# flock/状态机等既有防线仍在。record 创建失败区分两种返回：
#   rc=1 去重命中（记录已存在，调用方必须拒绝派发）
#   rc=2 账本不可用（调用方自行决定 fail-open 放行并告警）
#
# 约定：本库依赖调用方提供 log()（缺失时静默降级）；WEBHOOK_BASE 与调用方同源。
# shellcheck disable=SC2034

# Guard against double-sourcing
if [ -n "${_DISPATCH_LEDGER_SH_LOADED:-}" ]; then
    # shellcheck disable=SC2317
    return 0 2>/dev/null || true
fi
_DISPATCH_LEDGER_SH_LOADED=1

LEDGER_WEBHOOK_BASE="${WEBHOOK_BASE:-${HOME}/.factory/webhook}"
LEDGER_DIR="${LEDGER_WEBHOOK_BASE}/status/ledger"
LEDGER_EVENTS_FILE="${LEDGER_DIR}/events.jsonl"

LEDGER_SCHEMA_VERSION="dispatch-ledger/1"

# Python binary 动态解析（lib/evolution-channel.sh 先例）：本库只用 stdlib。
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || echo /opt/homebrew/bin/python3)}"

_ledger_log() {
    # 调用方无 log() 时静默（库可独立被影子脚本/测试 source）
    if declare -F log >/dev/null 2>&1; then
        log "$@"
    fi
}

# ============================================================================
# _ledger_init — 确保账本目录就绪（status 目录 + ledger 子目录 + by-ref 视图）
# ============================================================================
_ledger_init() {
    mkdir -p "${LEDGER_DIR}/by-ref" 2>/dev/null || return 1
    return 0
}

# ============================================================================
# _ledger_key_of <uuid> — UUID → 文件安全键（非 [A-Za-z0-9._-] 折叠为下划线）
# ============================================================================
_ledger_key_of() {
    local l_uuid="${1:-}"
    [ -n "$l_uuid" ] || return 1
    LEDGER_KEY_RAW="$l_uuid" "${PYTHON_BIN}" -c 'import os,re;print(re.sub(r"[^A-Za-z0-9._-]","_",os.environ["LEDGER_KEY_RAW"].strip()))' 2>/dev/null
}

# ============================================================================
# ledger_record_path <uuid> — 打印记录文件路径（存在与否由调用方判定）
# ============================================================================
ledger_record_path() {
    local l_key
    l_key=$(_ledger_key_of "$1") || return 1
    printf '%s\n' "${LEDGER_DIR}/${l_key}.json"
}

# ============================================================================
# ledger_get <uuid> — 读记录 JSON（stdout）；无记录/不可用返回 rc=1 且空输出
# ============================================================================
ledger_get() {
    local g_path
    g_path=$(ledger_record_path "$1") || return 1
    if [ -f "$g_path" ]; then
        cat "$g_path"
        return 0
    fi
    return 1
}

# ============================================================================
# ledger_ref_of <uuid> — 打印记录内 linearRef；无记录返回 rc=1（非管道形态，
# 管道会吞 rc——调用方以 rc 判定记录存在性）
# ============================================================================
ledger_ref_of() {
    local r_path
    r_path=$(ledger_record_path "$1") || return 1
    [ -f "$r_path" ] || return 1
    LEDGER_READER_PATH="$r_path" "${PYTHON_BIN}" -c 'import json, os
try:
    print(json.load(open(os.environ["LEDGER_READER_PATH"])).get("linearRef") or "")
except Exception:
    print("")' 2>/dev/null
    return 0
}

# ============================================================================
# ledger_link_ref <uuid> <ref> — by-ref symlink 视图维护（best-effort，永不致命）
# 视图幂等：已指向同一记录时不动。目标 record 永不改写（append-only）。
# ============================================================================
ledger_link_ref() {
    local l_uuid="$1" l_ref="$2"
    [ -n "$l_uuid" ] && [ -n "$l_ref" ] || return 0
    local l_key l_target l_path
    l_key=$(_ledger_key_of "$l_uuid") || return 0
    # ref 只进文件名：防御性折叠路径分隔符（调用面 ref 已过 ^[A-Z]+-[0-9]+$ 门）
    l_ref="${l_ref//\//_}"
    l_target="${LEDGER_DIR}/${l_key}.json"
    l_path="${LEDGER_DIR}/by-ref/${l_ref}.json"
    [ -f "$l_target" ] || return 0
    if [ "$(readlink "$l_path" 2>/dev/null)" = "$l_target" ]; then
        return 0
    fi
    ln -sf "$l_target" "$l_path" 2>/dev/null || true
}

# ============================================================================
# ledger_append <uuid> <ref> <event> [detail] — 事件流追加（append-only 留痕）
# 单行 printf（O_APPEND 原子面）；detail/ref 中引号反斜杠折叠（本库自产的
# k=v 串与 ^[A-Z]+-[0-9]+$ ref 本就不含；防御外部注入面）。
# 失败不影响主流程（留痕是观测面，不是判定面）。事件语义归调用方：
# trigger-droid 记 dispatch_claimed，GAP-A 记 compensation_created。
# ============================================================================
ledger_append() {
    local a_uuid="$1" a_ref="${2:-}" a_event="$3" a_detail="${4:-}"
    _ledger_init || return 0
    local a_ts="${LEDGER_BASE_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
    a_detail="${a_detail//\\/}"
    a_detail="${a_detail//\"/\'}"
    a_ref="${a_ref//\\/}"
    a_ref="${a_ref//\"/\'}"
    printf '{"ts":"%s","uuid":"%s","ref":"%s","event":"%s","detail":"%s"}\n' \
        "$a_ts" "$a_uuid" "$a_ref" "$a_event" "$a_detail" >> "${LEDGER_EVENTS_FILE}" 2>/dev/null || true
}

# ============================================================================
# ledger_dispatch_done <uuid> <ref> — 本通道已成功完成的派发判定
# 去重语义 = 拦「成功后的重投递/重放」，不拦「失败后的合法重触发」：
#   记录存在 且 source=trigger-droid 且 status 文件 completed+exitCode=0 → rc=0（去重命中）
#   其余（gap-a 补偿记录 / failed 或 stale running / status 缺失/归档 / 无记录）
#     → rc=1（放行——重试走既有 flock+状态机，账本不成为重试闸门）
# status 文件 = ${WEBHOOK_BASE}/status/<ref>.json（trigger-droid STATUS_DIR 同源）
# ============================================================================
ledger_dispatch_done() {
    local d_uuid="$1" d_ref="${2:-}"
    local d_path
    d_path=$(ledger_record_path "$d_uuid") || return 1
    [ -f "$d_path" ] || return 1
    local d_src
    d_src=$(LEDGER_READER_PATH="$d_path" "${PYTHON_BIN}" -c 'import json, os
try:
    print(json.load(open(os.environ["LEDGER_READER_PATH"])).get("source") or "")
except Exception:
    print("")' 2>/dev/null)
    [ "$d_src" = "trigger-droid" ] || return 1
    [ -n "$d_ref" ] || return 1
    local d_status_file="${LEDGER_WEBHOOK_BASE}/status/${d_ref}.json"
    [ -f "$d_status_file" ] || return 1
    LEDGER_STATUS_PATH="$d_status_file" "${PYTHON_BIN}" -c 'import json, os
try:
    d = json.load(open(os.environ["LEDGER_STATUS_PATH"]))
    print("yes" if d.get("status") == "completed" and d.get("exitCode") == 0 else "no")
except Exception:
    print("no")' 2>/dev/null | grep -q yes
}

# ============================================================================
# ledger_claim <uuid> <source> <ref> [title] [team] [kind] [gh_issue]
# 原子 no-clobber 创建派发记录（首次事实落账；ref/视图维护见上）：
#   rc=0 记录创建成功（本卡首条账本事实，调用方继续）
#   rc=1 记录已存在（首写胜出；本卡已有账本事实——是否构成去重命中由
#        ledger_dispatch_done 按来源+终态判定，claim 本身不裁决派发去重）
#        —— 且 ref 与记录内 linearRef 不同时重链 by-ref 视图 + ref_moved 事件
#   rc=2 账本不可用（空 UUID / 目录不可建 / 写入失败；调用方决定 fail-open）
# ============================================================================
ledger_claim() {
    local p_uuid="$1" p_source="$2" p_ref="${3:-}" p_title="${4:-}" p_team="${5:-}"
    local p_kind="${6:-linear-gateway}" p_gh="${7:-}"

    [ -n "$p_uuid" ] || return 2
    _ledger_init || return 2

    local p_key
    p_key=$(_ledger_key_of "$p_uuid") || return 2
    [ -n "$p_key" ] || return 2

    local p_file="${LEDGER_DIR}/${p_key}.json"
    if [ -f "$p_file" ]; then
        # 记录已存在（append-only）：跨团队变号时维护 by-ref 视图（record 本体不动）
        local p_prev_ref
        p_prev_ref=$(ledger_ref_of "$p_uuid")
        if [ -n "$p_ref" ] && [ -n "$p_prev_ref" ] && [ "$p_prev_ref" != "$p_ref" ]; then
            ledger_link_ref "$p_uuid" "$p_ref"
            ledger_append "$p_uuid" "$p_ref" "ref_moved" "previous=${p_prev_ref}"
            _ledger_log "LEDGER: ref moved ${p_prev_ref} -> ${p_ref} (uuid=${p_uuid} key unchanged)"
        fi
        return 1
    fi

    # 写 tmp（内容完整成形）→ ln 硬链接落位（同目录、同卷；no-clobber 原子语义）
    local p_tmp="${LEDGER_DIR}/.${p_key}.$$.tmp"
    LEDGER_UUID="$p_uuid" LEDGER_REF="$p_ref" LEDGER_KIND="$p_kind" \
    LEDGER_SOURCE="$p_source" LEDGER_GH="$p_gh" LEDGER_TITLE="$p_title" \
    LEDGER_TEAM="$p_team" LEDGER_BASE_TS="${LEDGER_BASE_TS:-}" LEDGER_SCHEMA_VERSION="$LEDGER_SCHEMA_VERSION" \
    "${PYTHON_BIN}" -c 'import json, datetime, os
gh = os.environ.get("LEDGER_GH", "").strip()
ts = os.environ.get("LEDGER_BASE_TS") or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({
    "version": 1,
    "schema": os.environ["LEDGER_SCHEMA_VERSION"],
    "linearUuid": os.environ["LEDGER_UUID"],
    "linearRef": os.environ.get("LEDGER_REF", "") or None,
    "kind": os.environ.get("LEDGER_KIND", "") or "linear-gateway",
    "source": os.environ["LEDGER_SOURCE"],
    "ghIssue": int(gh) if gh.isdigit() else None,
    "title": os.environ.get("LEDGER_TITLE", ""),
    "team": os.environ.get("LEDGER_TEAM", ""),
    "claimedAt": ts,
}, ensure_ascii=False))' > "$p_tmp" 2>/dev/null || {
        rm -f "$p_tmp" 2>/dev/null || true
        return 2
    }

    if ln "$p_tmp" "$p_file" 2>/dev/null; then
        rm -f "$p_tmp" 2>/dev/null || true
        ledger_link_ref "$p_uuid" "$p_ref"
        _ledger_log "LEDGER: dispatch record created uuid=${p_uuid} ref=${p_ref:-unknown} source=${p_source} (one-card-one-record, VAL-LEDGER-002)"
        return 0
    fi

    # ln 失败：区分「并发写者先落位」（记录已存在）与「文件系统异常」（不可用）
    rm -f "$p_tmp" 2>/dev/null || true
    if [ -f "$p_file" ]; then
        return 1
    fi
    return 2
}
