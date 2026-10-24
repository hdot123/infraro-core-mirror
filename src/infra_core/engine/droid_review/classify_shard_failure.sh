#!/usr/bin/env bash
# classify_shard_failure.sh — 失败出口 error_class 分类器（H1 六件套 ①）
#
# 背景（h1-classify-capture）：droid-review 失败 run 的 job log 只有
# "fail-closed" 无差别文案，真实原因散落在 stderr / 宿主日志 / session 里，
# 每次都要人工下载 artifact 逐层翻。本脚本在 run_shard.sh 的四个失败出口
# 统一产 error_class：按指纹表分类 → ::error 标注 + step summary
# （$GITHUB_STEP_SUMMARY）+ workspace 根 error-classification.txt
# （workflow 收进 debug artifact）。
#
# 用法：
#   classify_shard_failure.sh <shard_id> <droid_exit> <exec_start_epoch> \
#                             <droid_bin> <droid_version> <stderr_log> [stdout_file] [sentinel_file]
#   环境变量：RUN_ID、GITHUB_STEP_SUMMARY（缺省写 workspace 根 github-step-summary.txt，
#   CI 中由 runner 注入真实路径）、SENTINEL_FILE（第 8 位置参数同义）、
#   SHARD_SOFT_TIMEOUT_SEC / SHARD_MAX_TURNS / TURNS_AT_TERMINATION（遗书校准字段）
#
# 分类指纹表（v6 冻结清单，九类；优先级自上而下，先匹配先得）：
#   error_class          指纹
#   ───────────────────  ────────────────────────────────────────────────
#   soft_timeout         F3 哨兵 shard-terminated.reason 内容=soft_timeout
#                        （软超时越界——判定靠哨兵文件，非 elapsed 反推）
#   turn_limit           F3 哨兵内容=turn_limit（assistant 行计数超 SHARD_MAX_TURNS）
#   exit_137             droid exec 退出码 137（SIGKILL；137 重试与会话兜底
#                        皆空——run_shard.sh 已做重试，到此处即终态）
#   silent_death         exit 1 + stdout/stderr 双流空 + 耗时 <10s
#                        （二进制秒死：凭证未读/启动即崩）
#   model_not_allowed    "MetaError" + "Model not allowed"
#                        （BYOM id 未在服务端注册的精确错误对）
#   model_unresolvable   "Invalid model" 或 fallback 耗尽指纹
#   auth_401             "401" / "unauthorized"
#   rate_limit_429       "429" / "quota exceeded"
#   exit_137 终态之外的非零退出且双流有内容 → unclassified（非空降兜底，零丢弃）
#   ───────────────────  ────────────────────────────────────────────────
#   未命中任何指纹但 exit != 0 → unclassified（fail-closed 语义保留，人工看 artifact）
#
# 脱敏边界：本脚本产出的 summary/标注行只包含指纹词与元数据，不携带 stderr
# 原文；stderr 仅读取判类不落盘（原文经 redact_secrets.sh 脱敏后另行显示，
# 见 run_shard.sh）。
#
# bash 3.2 兼容；零新增依赖（grep/jq 可缺省——缺 jq 时双流空判定退化为 -s 尺寸判）。
set -euo pipefail

SHARD_ID="${1:-}"
DROID_EXIT="${2:-}"
EXEC_START="${3:-}"
DROID_BIN="${4:-unknown}"
DROID_VERSION="${5:-unknown}"
STDERR_LOG="${6:-}"
STDOUT_FILE="${7:-}"

# F3 哨兵路径：第 8 位置参数（追加可选，既有 7 参数调用兼容）与 SENTINEL_FILE env
# 双形态；缺省当前目录 shard-terminated.reason（run_shard.sh 的工作区根，哨兵与
# error-classification.txt 同目录）。哨兵不存在 = 既有判定序原样生效（零回归）。
SENTINEL_FILE="${8:-${SENTINEL_FILE:-shard-terminated.reason}}"

if [ -z "$SHARD_ID" ] || [ -z "$DROID_EXIT" ]; then
  echo "usage: classify_shard_failure.sh <shard_id> <droid_exit> <exec_start> <droid_bin> <droid_version> <stderr_log> [stdout_file]" >&2
  exit 2
fi

ERROR_CLASS_FILE="${ERROR_CLASS_FILE:-error-classification.txt}"
SUMMARY_FILE="${GITHUB_STEP_SUMMARY:-${GITHUB_WORKSPACE:-.}/github-step-summary.txt}"

# ── 证据汇拢（只读判类，不落盘原文）──
EVIDENCE=""
if [ -n "$STDERR_LOG" ] && [ -f "$STDERR_LOG" ]; then
  EVIDENCE="$(cat "$STDERR_LOG" 2>/dev/null || true)"
fi
STDOUT_TEXT=""
if [ -n "$STDOUT_FILE" ] && [ -f "$STDOUT_FILE" ]; then
  STDOUT_TEXT="$(cat "$STDOUT_FILE" 2>/dev/null || true)"
fi
COMBINED="${EVIDENCE}
${STDOUT_TEXT}"

# 双流空判定：文件尺寸 + 有效内容（jq 可用时校验非空白 JSON，缺省退化为 -s）
stderr_empty=0
if [ -z "$(tr -d '[:space:]' 2>/dev/null < "$STDERR_LOG")" ]; then
  stderr_empty=1
fi
stdout_empty=0
if [ -n "$STDOUT_FILE" ] && [ -f "$STDOUT_FILE" ]; then
  if [ -z "$(tr -d '[:space:]' 2>/dev/null < "$STDOUT_FILE")" ]; then
    stdout_empty=1
  fi
else
  stdout_empty=1
fi
both_streams_empty=$(( stderr_empty && stdout_empty ))

# 耗时（秒，整数）；起点缺失记 -1（不参与 silent_death 判定）
DURATION_SEC=-1
if [ -n "$EXEC_START" ] && [ "$EXEC_START" -gt 0 ] 2>/dev/null; then
  DURATION_SEC=$(( $(date +%s) - EXEC_START ))
fi

# ── F3 哨兵指纹（v2 P0-C：判定序排 exit_137 之前）──
# 判定靠哨兵文件内容，非 elapsed 反推；哨兵在 = 本审查已被判越界，
# exit code（含主动 kill 的 137）不改写分类。
SENTINEL_CONTENT=""
if [ -n "$SENTINEL_FILE" ] && [ -f "$SENTINEL_FILE" ]; then
  SENTINEL_CONTENT="$(tr -d '[:space:]' < "$SENTINEL_FILE" 2>/dev/null || true)"
fi

# ── 指纹分类（优先级自上而下）──
ERROR_CLASS="unclassified"
REASON_HINT="非零退出但指纹未命中——人工查看 debug artifact"

if [ "$SENTINEL_CONTENT" = "soft_timeout" ]; then
  ERROR_CLASS="soft_timeout"
  REASON_HINT="软超时越界（SHARD_SOFT_TIMEOUT_SEC 哨兵先行终止序列：SIGTERM→宽限→SIGKILL）——检查心跳 shard-heartbeat.log 与 session jsonl 最后进度"
elif [ "$SENTINEL_CONTENT" = "turn_limit" ]; then
  ERROR_CLASS="turn_limit"
  REASON_HINT="turn 上限越界（assistant 行计数超 SHARD_MAX_TURNS，哨兵先行终止）——检查心跳 shard-heartbeat.log 的 turns 增长轨迹"
elif [ "${DROID_EXIT:-0}" -eq 137 ]; then
  ERROR_CLASS="exit_137"
  REASON_HINT="SIGKILL 完成期竞态（重试+会话兜底均未恢复）——参考 2026-08-29 #40/#45/#47"
elif echo "$COMBINED" | grep -q "MetaError" && echo "$COMBINED" | grep -qi "Model not allowed"; then
  ERROR_CLASS="model_not_allowed"
  REASON_HINT="模型未被服务端允许（MetaError Model not allowed）——检查 BYOM id 注册名"
elif echo "$COMBINED" | grep -q "Invalid model"; then
  ERROR_CLASS="model_unresolvable"
  REASON_HINT="模型名不可解析（Invalid model）——检查 -m 传参与 settings.json customModels"
elif echo "$COMBINED" | grep -qi "fallback.*exhaust\|all.*fallback.*failed\|no available model"; then
  ERROR_CLASS="model_unresolvable"
  REASON_HINT="模型 fallback 耗尽——全部候选模型不可用"
elif echo "$COMBINED" | grep -qw "401" || echo "$COMBINED" | grep -qi "unauthorized"; then
  ERROR_CLASS="auth_401"
  REASON_HINT="认证失败（401/unauthorized）——检查 FACTORY_API_KEY / worker_ci_key 门禁头"
elif echo "$COMBINED" | grep -qw "429" || echo "$COMBINED" | grep -qi "quota exceeded"; then
  ERROR_CLASS="rate_limit_429"
  REASON_HINT="限流/配额（429/quota exceeded）——等恢复窗口或换订阅"
elif [ "${DROID_EXIT:-0}" -eq 1 ] && [ "$both_streams_empty" -eq 1 ] && [ "$DURATION_SEC" -ge 0 ] && [ "$DURATION_SEC" -lt 10 ]; then
  ERROR_CLASS="silent_death"
  REASON_HINT="静默死亡（exit 1 + 双流空 + <10s）——二进制秒死：凭证未读/启动即崩/DROID_CWD 无效"
fi

# silent_death 优先级：指纹词皆无 + 秒死特征 → 已在上面 elif 链尾；
# 但若指纹词与秒死同时存在（如 401 且 <10s），指纹词优先——故 silent_death
# 只在全部词指纹未命中后判定（elif 链保证）。

# ── 落盘（error-classification.txt + step summary）──
# F3 校准字段（④数据留痕）：turns=哨兵时刻 assistant 行计数、elapsed_sec=exec 实际
# 耗时、soft_timeout_sec / max_turns=当时的界值。哨兵缺席时 turns/elapsed 为空、
# 界值仍记录当前配置——数据校准每次失败都有遗书可查。
SOFT_TIMEOUT_SEC="${SHARD_SOFT_TIMEOUT_SEC:-2400}"
MAX_TURNS_VAL="${SHARD_MAX_TURNS:-60}"
TURNS_VAL="${TURNS_AT_TERMINATION:-}"
ELAPSED_VAL="$DURATION_SEC"
{
  echo "shard_id: $SHARD_ID"
  echo "run_id: ${RUN_ID:-unknown}"
  echo "error_class: $ERROR_CLASS"
  echo "droid_exit: $DROID_EXIT"
  echo "duration_sec: $DURATION_SEC"
  echo "sentinel: ${SENTINEL_CONTENT:-none}"
  echo "turns: ${TURNS_VAL:-n/a}"
  echo "elapsed_sec: ${ELAPSED_VAL:--1}"
  echo "soft_timeout_sec: $SOFT_TIMEOUT_SEC"
  echo "max_turns: $MAX_TURNS_VAL"
  echo "both_streams_empty: $both_streams_empty"
  echo "droid_bin: $DROID_BIN"
  echo "droid_version: $DROID_VERSION"
  echo "hint: $REASON_HINT"
} > "$ERROR_CLASS_FILE"

{
  echo ""
  echo "## droid-review shard $SHARD_ID 失败分类"
  echo ""
  echo "| 字段 | 值 |"
  echo "|---|---|"
  echo "| error_class | \`$ERROR_CLASS\` |"
  echo "| droid_exit | $DROID_EXIT |"
  echo "| duration_sec | $DURATION_SEC |"
  echo "| sentinel | \`${SENTINEL_CONTENT:-none}\` |"
  echo "| turns | ${TURNS_VAL:-n/a} |"
  echo "| elapsed_sec | ${ELAPSED_VAL:--1} |"
  echo "| soft_timeout_sec | $SOFT_TIMEOUT_SEC |"
  echo "| max_turns | $MAX_TURNS_VAL |"
  echo "| both_streams_empty | $both_streams_empty |"
  echo "| droid_bin | \`$DROID_BIN\` |"
  echo "| droid_version | \`$DROID_VERSION\` |"
  echo ""
  echo "诊断提示：$REASON_HINT"
} >> "$SUMMARY_FILE"

echo "::error::shard $SHARD_ID error_class=$ERROR_CLASS (exit=$DROID_EXIT, ${DURATION_SEC}s, droid=$DROID_BIN v$DROID_VERSION) — $REASON_HINT"

# 分类器本身永远 exit 0：失败分类是观测面，不得改写主退出码（run_shard.sh
# 调用处带 || true 双保险）。
exit 0
