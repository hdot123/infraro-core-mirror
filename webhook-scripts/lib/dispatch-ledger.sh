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

# budget 遥测依赖 lib/posthog.sh（自带 double-source 守卫，重复 source 安全；
# 沙盒/独立消费方未定义 POSTHOG_EVENT_NAME 时发送侧自跳过，不影响 rc）。
LEDGER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=posthog.sh
if [ -f "${LEDGER_LIB_DIR}/posthog.sh" ]; then
    # shellcheck disable=SC1091
    source "${LEDGER_LIB_DIR}/posthog.sh"
fi

# budget 遥测事件专用事件名/主体（send_posthog_event_named 每次调用临时覆写，
# 不污染调用方的 POSTHOG_EVENT_NAME 约定）
LEDGER_BUDGET_POSTHOG_EVENT="r1_gate_event"
LEDGER_BUDGET_POSTHOG_ID="dispatch-ledger"

# 日预算缺省上限（DISPATCH_DAILY_BUDGET_<SOURCE> 覆盖 > DEFAULT > 50）
LEDGER_BUDGET_DEFAULT_LIMIT=50

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

# budget 告警面：WARN 是契约产物（超限/不可用必须可观测），不依赖调用方 log()
# ——直接落 stderr；调用方有 log() 时同步进日志文件（生产面留痕）。
_ledger_budget_warn() {
    printf '[LEDGER] %s\n' "$*" >&2
    _ledger_log "$*"
}

# ============================================================================
# ledger_budget_check <source> [kind] — 当日派发预算计量（a′ 收门 observe-first）
# 语义（用户裁定 2026-10-10「第一步规范不掐死」）：计量 + 超预算遥测告警 +
# **永远放行**。本函数任何派发形态都不产生拒发返回码——rc=1 仅用于用法错误
# （缺 source 参数），与派发裁决无关。
#   计数基准 = status/ledger/*.json 记录本体（source 精确匹配 + claimedAt
#   UTC 自然日前缀；给 kind 时再按 kind 精确匹配）。自然日窗口跨日自动重置。
#   上限 = DISPATCH_DAILY_BUDGET_<SOURCE>（大写、- 转 _）>
#          DISPATCH_DAILY_BUDGET_DEFAULT > LEDGER_BUDGET_DEFAULT_LIMIT(50)
#   超预算 → stderr WARN + budget_over_limit 遥测（PostHog 经 lib/posthog.sh，
#             尊重 POSTHOG_DRY_RUN=1；events.jsonl 审计留痕，DRY_RUN 下静默）
#   配置非数字 / 计数探针失败 / 账本目录不可用 → budget_unavailable + WARN，
#             fail-open 放行（与账本哲学一致：账本是观测面不是闸门）
# 返回：rc=0 放行（唯一派发路径返回值）；rc=1 用法错误（缺参数）
# ============================================================================
ledger_budget_check() {
    local b_source="${1:-}" b_kind="${2:-}"
    if [ -z "$b_source" ]; then
        _ledger_log "BUDGET: usage: ledger_budget_check <source> [kind]"
        return 1
    fi

    # 上限解析：SOURCE 覆盖 > DEFAULT > 内置缺省；非数字视同配置不可用
    local b_limit_var
    b_limit_var="DISPATCH_DAILY_BUDGET_$(printf '%s' "$b_source" | tr '[:lower:]-' '[:upper:]_')"
    local b_limit_raw="${!b_limit_var:-${DISPATCH_DAILY_BUDGET_DEFAULT:-}}"
    if [ -z "$b_limit_raw" ]; then
        b_limit_raw="$LEDGER_BUDGET_DEFAULT_LIMIT"
    fi
    case "$b_limit_raw" in
        ''|*[!0-9]*)
            _ledger_budget_warn "BUDGET: WARN budget_unavailable source=${b_source} reason=invalid_limit_config value=${b_limit_raw}"
            _ledger_budget_telemetry "budget_unavailable" "source=${b_source} reason=invalid_limit_config"
            return 0
            ;;
    esac

    # 计数：ledgers 目录缺席时先尝试就绪（懒创建语义与 ledger_claim 一致）；
    # 创建失败（宿主不可写）= 基建不可用 → budget_unavailable fail-open。
    # 懒创建成功=窗口内计数为 0（空窗），照常走计量留痕（budget_ok），不可静默。
    local b_today b_count
    if [ ! -d "$LEDGER_DIR" ]; then
        _ledger_init || {
            _ledger_budget_warn "BUDGET: WARN budget_unavailable source=${b_source} reason=ledger_dir_unavailable"
            _ledger_budget_telemetry "budget_unavailable" "source=${b_source} reason=ledger_dir_unavailable"
            return 0
        }
        b_count=0
    else
        # 窗口日期与 ledger_claim/ledger_append 共用 LEDGER_BASE_TS 时间旅行钩子：
        # 测试侧钉死窗口与播种日期一致，消除与 runner 实际日期的跨午夜竞态。
        b_today="${LEDGER_BASE_TS:-}"
        b_today="${b_today%%T*}"
        if [ -z "$b_today" ]; then
            b_today="$(date -u +%Y-%m-%d 2>/dev/null)" || {
                _ledger_budget_warn "BUDGET: WARN budget_unavailable source=${b_source} reason=clock_unavailable"
                _ledger_budget_telemetry "budget_unavailable" "source=${b_source} reason=clock_unavailable"
                return 0
            }
        fi
        b_count=$(BUDGET_COUNT_SOURCE="$b_source" BUDGET_COUNT_KIND="$b_kind" BUDGET_COUNT_TODAY="$b_today" \
            BUDGET_COUNT_DIR="$LEDGER_DIR" "${PYTHON_BIN}" -c 'import json, os
d = os.environ["BUDGET_COUNT_DIR"]
src = os.environ["BUDGET_COUNT_SOURCE"]
kind = os.environ["BUDGET_COUNT_KIND"]
today = os.environ["BUDGET_COUNT_TODAY"]
n = 0
try:
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name)) as f:
                rec = json.load(f)
        except Exception:
            continue
        if rec.get("source") != src:
            continue
        if kind and rec.get("kind") != kind:
            continue
        if str(rec.get("claimedAt") or "").startswith(today):
            n += 1
except OSError:
    pass
print(n)' 2>/dev/null) || true
    fi
    if ! [ "${b_count:-}" -ge 0 ] 2>/dev/null; then
        _ledger_budget_warn "BUDGET: WARN budget_unavailable source=${b_source} reason=count_probe_failed"
        _ledger_budget_telemetry "budget_unavailable" "source=${b_source} reason=count_probe_failed"
        return 0
    fi

    if [ "$b_count" -ge "$b_limit_raw" ]; then
        _ledger_budget_warn "BUDGET: WARN budget_over_limit source=${b_source} kind=${b_kind:-*} count=${b_count} limit=${b_limit_raw} (observe-only, dispatch proceeds)"
        _ledger_budget_telemetry "budget_over_limit" \
            "source=${b_source} kind=${b_kind:-*} count=${b_count} limit=${b_limit_raw}"
    else
        # 窗口内正常计量也留痕（VAL-BUD-002 对账面）：与超限/unavailable 同构
        # 的 budget_ok 事件，让「当日派发了多少」可直接从遥测对账，不必逐条
        # 数 events.jsonl。DRY_RUN=1 下同样只打印不落盘（沙盒零残留）。
        _ledger_budget_telemetry "budget_ok" \
            "source=${b_source} kind=${b_kind:-*} count=${b_count} limit=${b_limit_raw}"
    fi
    return 0
}

# ============================================================================
# _ledger_budget_telemetry <event_type> <detail> — budget 遥测双写（best-effort）
# PostHog 外发经 lib/posthog.sh（POSTHOG_DRY_RUN=1 时其守卫打印不发送）；
# events.jsonl 审计留痕与 key 无关，DRY_RUN 下一并静默（沙盒零残留）。
# 任何失败都不得影响调用方 rc（观测面，非判定面）。
# ============================================================================
_ledger_budget_telemetry() {
    local t_event="$1" t_detail="$2"
    local t_uuid
    t_uuid="budget-$(date -u +%Y%m%d)-$(printf '%s' "${t_detail}" | shasum -a 256 2>/dev/null | cut -c1-8)"
    t_uuid="${t_uuid// /}"
    # dry-run 打印面是沙盒观测点，不吞 stdout；发送面在无 key/缺配置时自跳过
    if declare -F send_posthog_event_named >/dev/null 2>&1; then
        send_posthog_event_named "$LEDGER_BUDGET_POSTHOG_EVENT" "$t_event" \
            "$LEDGER_BUDGET_POSTHOG_ID" "budget" "$t_detail" || true
    fi
    if [ "${POSTHOG_DRY_RUN:-0}" != "1" ]; then
        ledger_append "$t_uuid" "" "$t_event" "$t_detail"
    fi
    return 0
}
