#!/usr/bin/env bash
# redact_secrets.sh — 宿主日志/错误流脱敏过滤器（stdin→stdout）
#
# H1 观测性六件套 ②（h1-classify-capture）：debug artifact 只准携带脱敏内容。
# 圆3 裁决的暴露面收敛：未脱敏全量 stderr / session jsonl 不再上传；
# 一切进入 artifact 的宿主侧文本先过本过滤器。
#
# 脱敏面（四类凭证样字 → [REDACTED]）：
#   1. Bearer <token>          — Authorization 头 / OAuth token（scheme 与
#                                头名任意大小写——HTTP 头不区分大小写，
#                                AUTHORIZATION: BEARER 同灭）
#   2. sk-<…>                  — OpenAI 风格 API key（sk- 起始的密钥串）
#   3. x-ci-key: <value>       — droid-review-gateway Worker 门禁头
#   4. apiKey=<value> / api_key=<value> — 查询串与配置文件常见形态
#
# 键形态（api 与 xci 两族同构）：
#   - 键名大小写任意组合（API_KEY=/ApiKey= 环境变量形态同灭）；
#   - 键后允许闭引号与单引号（"apiKey": — JSON 引号键形态）；
#   - 键名尾随标识符并入键族（API_KEYS= / apiKeyName= / x-ci-keys= /
#     x-ci-key-id= 一并命中——安全过滤器宁可多脱不漏）；
#   - 键/分隔符间空白用 [[:space:]]（含 tab——字面空格会被 tab 形态绕过）。
# 值形态（成对引号 → 结构化 → 未终结引号 → 裸值四层）：
#   - 成对双引号值含 JSON 转义引号（"(\\.|[^"\\])*"——否则 {"apiKey":"a\"b"}
#     只吞到内嵌引号处、尾部残泄）；单引号对为 '…'；
#   - 结构化嵌套值（{"apiKey": {"token": "inner"}} 值以 { 或 [ 起始）：整值
#     吞至 & （api 族，行内上下文取舍）或行尾——结构内分号/右括号可能是
#     值材料（"b;ctail"），截断即残泄，宁多脱不漏；xci 族无 & 截断
#     （header 值允许裸 &）；回声形态（[REDACTED]）被再匹配是同字面替换，
#     幂等安全；
#   - 裸值/未终结引号值（截断行常见）：值类允许引号穿透（成对引号已由前
#     层承接；裸值内嵌引号如 ab"cd 与未终结引号（含单引号）都整值吞没，
#     出类会在引号处截断残泄 cd）——输出统一为不带引号的 [REDACTED] 令牌
#     （保留闭合引号会被同管后层裸值规则整值再吞）；裸值停止符集仅空格/
#     逗号/分号/右括号（} 不入集——tok}cd 会在 } 处截断残泄，} 入裸值整
#     吞；xci/api 主值类同理），吞至停止符或行尾、不补闭引号（二次过滤
#     幂等）；行内后随上下文一并脱敏，宁多脱不漏；
#   - api 族裸值 & 截断（查询串裸 & 是分隔符，截断它保住 &mode=fast 行内
#     上下文）；两族与 Bearer 逗号均吞全且逗号续项允许内部空格——
#     api=abc, def ghi、Bearer abc, def、x-ci-key: tok1, tok2 tok3 与紧连
#     aaa,bbb 都整值脱敏（逗号不是值终止符：主值类不含逗号，紧连逗号由
#     `, 续项` 重复组承接）。
#   兜底：Authorization: <scheme> <credentials>——键名允许引号包裹（JSON
#   序列化形态 "authorization": 同灭），分隔符 : 或 =（AUTHORIZATION= 环境
#   变量形态同灭），值以 [ 开头（数组值 ["tok"]）同样命中（分隔符后整行
#   吞至行尾，无回退形态——回声替换天然幂等）：纯数字开头裸 token、
#   Digest 多凭据段、quoted credentials、含停止符的值全部整灭——行尾扫
#   尾语义，宁多脱不漏）。
#
#   注意：sed 括号表达式内的停止符集都是字面成员，成员必须逐个核对——
#   反斜杠在 POSIX 括号内是字面量而非转义（历史上反斜杠/反引号误入值类
#   造成 tok\xxx 尾泄的根因）。
#
# 设计约束：
#   - 只做替换不做丢弃——保留行结构（MetaError/版本/路由行可读）；
#   - sed -E 跨 GNU/BSD；无状态、零临时文件、可管道串联；
#   - 已脱敏行幂等（[REDACTED] 不含凭证样字，二次过滤不变）。
#
# 契约测试：tests/test_run_shard_error_classification.py（正则逐条 + 幂等 + 无损行结构）。
set -euo pipefail

sed -E \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)"[^"]*"([[:space:]]+"[^"]*")*/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)"[^"]*$/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)[^ ,;)]+(,[[:space:]]*[^;)]*)*/\1[REDACTED]/g' \
  -e 's/sk-[A-Za-z0-9_-]{8,}/[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])*"|'"'"'[^'"'"']*'"'"')([[:space:]]+"[^"]*")*/\1"[REDACTED]"/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)(\{.*|[[][^)]*["'"'"'][^)]*.*)/\1[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])+|'"'"'[^'"'"']+)/\1[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)([^ ,;)]+)(,[[:space:]]*[^;)]*)*/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_-]?[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])*"|'"'"'[^'"'"']*'"'"')([[:space:]]+"[^"]*")*/\1"[REDACTED]"/g' \
  -e 's/([Aa][Pp][Ii][_-]?[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)(\{.*|[[][^;&)]*["'"'"'][^;&)]*.*)/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_-]?[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])+|'"'"'[^'"'"']+)/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_-]?[Kk][Ee][Yy][[:alnum:]_-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)([^ ,;)&]+)(,[[:space:]]*[^;)&]*)*/\1[REDACTED]/g' \
  -e 's/(["'"'"']?[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn]["'"'"']?[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/g'
