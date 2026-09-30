#!/usr/bin/env bash
# redact_secrets.sh — 宿主日志/错误流脱敏过滤器（stdin→stdout）
#
# H1 观测性六件套 ②（h1-classify-capture）：debug artifact 只准携带脱敏内容。
# 圆3 裁决的暴露面收敛：未脱敏全量 stderr / session jsonl 不再上传；
# 一切进入 artifact 的宿主侧文本先过本过滤器。
#
# 脱敏面（四类凭证样字 → [REDACTED]）：
#   1. Bearer <token>          — Authorization 头 / OAuth token
#   2. sk-<…>                  — OpenAI 风格 API key（sk- 起始的密钥串）
#   3. x-ci-key: <value>       — droid-review-gateway Worker 门禁头
#   4. apiKey=<value> / api_key=<value> — 查询串与配置文件常见形态
#   兜底：Authorization: <scheme> <token>（非 Bearer scheme 同样脱敏）
#
# 设计约束：
#   - 只做替换不做丢弃——保留行结构（MetaError/版本/路由行可读）；
#   - sed -E 跨 GNU/BSD；无状态、零临时文件、可管道串联；
#   - 已脱敏行幂等（[REDACTED] 不含凭证样字，二次过滤不变）。
#
# 契约测试：tests/test_run_shard_error_classification.py（正则逐条 + 幂等 + 无损行结构）。
set -euo pipefail

sed -E \
  -e 's/[Bb]earer +[^"'"'"' ,;)}\`]+/[REDACTED]/g' \
  -e 's/sk-[A-Za-z0-9_-]{8,}/[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy] *[:=] *)"[^"]*"/\1"[REDACTED]"/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy] *[:=] *)[^"'"'"' ,;)}\`]+/\1[REDACTED]/g' \
  -e 's/(api[_-]?[Kk][Ee][Yy] *[:=] *)"[^"]*"/\1"[REDACTED]"/g' \
  -e 's/(api[_-]?[Kk][Ee][Yy] *[:=] *)[^"'"'"' ,;)}\`]+/\1[REDACTED]/g' \
  -e 's/([Aa]uthorization *: *)[A-Za-z]+ +[^"'"'"' ,;)}\`]+/\1[REDACTED]/g'
