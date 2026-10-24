#!/usr/bin/env bash
# capture_host_log.sh — 宿主 CLI 日志脱敏采集（H1 六件套 ②，h1-classify-capture）
#
# 用途：droid exec 失败出口的 debug artifact。droid exec 报错的真实指纹
# （MetaError 原文 / 模型路由 / CLI 版本）常只在宿主 CLI 日志里——但该日志
# 全量携带凭证样字（Bearer/sk-/x-ci-key/apiKey），不得原样进入 artifact。
# 本脚本只抓与失败分类相关的行，先抓后脱敏，落盘一个有界文件。
#
# 用法：capture_host_log.sh <out_file>
#   可选环境变量：
#     DROID_LOG_FILE   宿主日志路径（默认 ~/.factory/logs/droid-log-single.log）
#     CAPTURE_MAX_LINES 上限行数（默认 50，匹配特征行 + 紧邻上下文超限时截断）
#
# 采集规则：
#   1. 命中特征（Egrep）：MetaError / 模型不允许 / 模型路由 / CLI 版本行
#   2. 每个命中行带 2 行前导上下文（-B 2，路由错误的原因行常在前面）
#   3. 合并去重（保留原始顺序）、脱敏（redact_secrets.sh）、按序截断至上限
#   4. 日志不存在/无命中 → 空文件 + exit 0（尽力而为，不因采集失败改写主退出码）
#
# bash 3.2 兼容（macOS 自带 bash 可直接测试）；零新增依赖（find/sed/awk）。
set -euo pipefail

OUT_FILE="${1:-}"
if [ -z "$OUT_FILE" ]; then
  echo "usage: capture_host_log.sh <out_file>" >&2
  exit 2
fi

LOG_FILE="${DROID_LOG_FILE:-$HOME/.factory/logs/droid-log-single.log}"
MAX_LINES="${CAPTURE_MAX_LINES:-50}"

REDACT_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/redact_secrets.sh"

: > "$OUT_FILE"

if [ ! -f "$LOG_FILE" ]; then
  echo "host log capture: log file not found: $LOG_FILE (best-effort, skipping)" >&2
  exit 0
fi

# 特征行（与失败分类指纹同域）：MetaError 原文、模型不允许/不可解析/路由、
# CLI 版本自报。Egrep 单表达式，grep -B 输出上下文分组（-- 分隔行自然去重锚）。
PATTERN='MetaError|[Mm]odel not allowed|[Ii]nvalid model|routing|route|fallback|version [0-9]+\.[0-9]+'

MATCH_BLOCKS="$(mktemp "${TMPDIR:-/tmp}/droid-hostlog.XXXXXX")"
trap 'rm -f "$MATCH_BLOCKS"' EXIT

grep -a -B 2 -E "$PATTERN" "$LOG_FILE" > "$MATCH_BLOCKS" 2>/dev/null || true

if [ ! -s "$MATCH_BLOCKS" ]; then
  echo "host log capture: no matching lines in $LOG_FILE (best-effort, empty capture)" >&2
  exit 0
fi

# 合并 -B 块的重叠（丢弃 -- 分组分隔行，按首次出现保序去重），再脱敏再截断。
awk '!/^--$/ && !seen[$0]++ { print }' "$MATCH_BLOCKS" \
  | bash "$REDACT_HELPER" \
  | head -n "$MAX_LINES" > "$OUT_FILE"

echo "host log capture: $(wc -l < "$OUT_FILE" | tr -d ' ') lines captured (limit $MAX_LINES) from $LOG_FILE"
