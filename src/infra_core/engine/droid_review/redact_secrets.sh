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
#                                AUTHORIZATION: BEARER 同灭）；scheme 后分隔
#                                符 : 或 = 同灭（BEARER:tok / Bearer: tok /
#                                Bearer=tok 三形态，issue #204 P1——只认空白
#                                的旧形态实测放行）；头名尾随标识符并入键族
#                                （BEARER_TOKEN= 环境变量形态同灭）
#   2. sk-<…>                  — OpenAI 风格 API key（sk- 起始的密钥串，
#                                大小写不敏感——SK- 同灭，圆17 P3）
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
#     （成对引号输出由占位字节保护存活到终行，见下方成对引号输出契约；
#     本层裸值输出不补闭引号，二次过滤幂等）；裸值停止符集仅空格/
#     逗号/分号/右括号（} 不入集——tok}cd 会在 } 处截断残泄，} 入裸值整
#     吞；api 主值类同理），吞至停止符或行尾、不补闭引号（二次过滤
#     幂等）；行内后随上下文一并脱敏，宁多脱不漏；xci 族裸值停止符集
#     仅空格/逗号（; ) 与 } 同为 header 值材料——tok)tail 在 ) 处截断
#     即残泄，圆17 P2 与 test_26g 的 } 修复对称；;q=1 形态随值整吞属
#     过度脱敏安全方向，test_37 引号值的 ; 保留契约走占位符续项规则
#     不受影响；逗号续项组同步放宽（; ) 不再截断续项）；
#   - api 族裸值 & 截断（查询串裸 & 是分隔符，截断它保住 &mode=fast 行内
#     上下文）；两族与 Bearer 逗号均吞全且逗号续项允许内部空格——
#     api=abc, def ghi、Bearer abc, def、x-ci-key: tok1, tok2 tok3 与紧连
#     aaa,bbb 都整值脱敏（逗号不是值终止符：主值类不含逗号，紧连逗号由
#     `, 续项` 重复组承接）；逗号前空白（tok1 , tok2）由续项重复组的
#     `[[:space:]]*,` 前缀承接（#229 round-2 P1——续项规则此前要求逗号
#     紧邻值/占位符，"t1" , "t2" 形态实测 t2 残泄）。
#   兜底：Authorization: <scheme> <credentials>——键名允许引号包裹（JSON
#   序列化形态 "authorization": 同灭），分隔符 : 或 =（AUTHORIZATION= 环境
#   变量形态同灭），值以 [ 开头（数组值 ["tok"]）同样命中（分隔符后整行
#   吞至行尾，无回退形态——回声替换天然幂等）：纯数字开头裸 token、
#   Digest 多凭据段、quoted credentials、含停止符的值全部整灭——行尾扫
#   尾语义，宁多脱不漏）。
#
#   值内停止符纪律（#229 round-4 P2/P3② + round-5 P1）：成对引号闭合后的
#   行尾扫尾语义——裸值/占位符续项吞至 ; ) 或行尾，续吞分隔符仅逗号
#   （; 是截断符不是续吞分隔符——续项组此前把 ; 当分隔符，"t1";q=1 的
#   ;q=1 整段被吞，#229 round-5 P1 实证；api 族另排除 &，查询串行内上
#   下文契约——粘连尾首字符类此前漏 &，"q1"&mode=fast 的 &mode=fast 整
#   段被吞，同轮 P1；Bearer 裸值同管——Bearer 头整行即凭据，空格/逗号/
#   冒号/等号都是值材料，"Bearer abc: def" / "Bearer abc, def" 整值灭，
#   仅 ; ) 截断；} 始终不入停止符集——出类截断即残泄）。键类尾随标识符
#   含 . 点分形态（bearer.token= / x-ci-key.id= / api.key.name=——
#   properties/config 常见形态，[[:alnum:]_.-]* 三族对称）。
#
#   Bearer 分隔符形态（issue #204 P1，与 api/xci 键形态层及 Authorization
#   兜底同构）：scheme 后允许 : 或 = 分隔符（BEARER:tok / Bearer: tok /
#   Bearer=tok），头名尾随标识符并入键族（BEARER_TOKEN=），头名允许闭
#   单/双引号（"Bearer": / 'Bearer': JSON 键形态，#229 round-2 P1——键
#   引号缺位时 {"Bearer":"tok"} 实测整行放行），分隔符后整行
#   吞至行尾（行尾扫尾语义，同 Authorization 兜底——回声替换天然幂等）。
#   规则次序不变量（#229 droid-review P1）：分隔符规则必须先于裸值规则——
#   裸值值类容纳裸 : / =（Bearer : tok 的分隔符会被当作裸值先行吞掉、
#   真 token 残泄），后置即失明；分隔符先行命中后，裸值规则对残留裸分隔
#   符的再吞属安全冗余（Bearer : tok 分隔符规则的 .* 吞掉含 token 的整
#   段尾部，终态单令牌 Bearer [REDACTED]——test_28 锁定，宁多脱不漏）。
#
#   Bearer 单引号值形态（圆17 P3，与 api/xci 族对称）：成对
#   （Bearer 'tok with space'——引号内空格是值材料，裸值规则在空格处
#   截断即残泄）与未终结（Bearer 'tok… 截断行）两形态；成对多 token
#   空格续项（'t1' "t2"）一并承接。输出为无引号 [REDACTED]——Bearer
#   不适用占位字节带引号契约（#229 round-4 P3①），同管后层裸值规则
#   再吞属同字面回声，幂等安全。
#
#   成对引号输出契约（issue #204 P3②，改实现对齐文档）：成对引号规则
#   输出统一为带引号的 "[REDACTED]"，且必须存活到终行——同管后层未终结
#   引号/裸值规则不得再吞。实现：成对引号规则先输出 \x01 占位字节（日志
#   文本不含该控制字符），后层裸值值类排除 \x01，逗号续项由专门的占位符
#   续项规则承接（"t1", "t2" 整值灭、占位符存活），末条规则统一还原
#   \x01 → "[REDACTED]"。JSON 闭合 } 随之存活到终行（仅单键 JSON——值后
#   紧跟逗号时占位符续项规则吞至行尾、} 一并吸收，多键 JSON 的 } 不保证
#   存活，过度脱敏方向可接受）；还原后的 "[REDACTED]" 不含凭证样字也不含
#   \x01，二次过滤幂等。
#
#   本契约限 xci/api 两族（#229 round-4 P3①）：Bearer 成对引号规则
#   （test_26i 锁定）输出无引号 [REDACTED]——Bearer 头整行即凭据，无 JSON
#   结构语境，不适用带引号令牌契约。
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

# 成对引号占位字节（issue #204 P3②）：\x01 是日志文本不含的控制字符；BSD/GNU
# sed 均无可移植的 \xHH 转义写法，故经 shell 参数注入字面字节。成对引号规则
# 先输出占位字节，后层裸值值类排除它（括号成员尾部，] 前一字节），逗号续项
# 由占位符续项规则承接，末条还原规则统一置换为 "[REDACTED]"——占位字节不可
# 能泄漏到终行；输入原生含 \x01 时由下方 tr -d 预清除先行整字节删除
#（#229 round-4 P1——删除后值为空或恢复连续，非还原规则改写，控制字符
# 不入日志文本，可接受）。
PH="$(printf '\001')"

# 原生 \x01 预清除（#229 round-4 P1）：输入携带裸 \x01 时，裸值值类对占位
# 字节的排除会在该字节处截断（apiKey=abc\x01def 尾段 def 残泄、\x01 起始值
# 整值跳过、两遍输出不一致破坏幂等契约）。\x01 是日志文本不含的控制字符，
# sed 前整字节删除——值恢复整段连续，裸值规则整值吞没，幂等成立。
tr -d "$PH" | sed -E \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)"[^"]*"([[:space:]]+"[^"]*")*/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)'"'"'[^'"'"']*'"'"'([[:space:]]+"[^"]*")*/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)"[^"]*$/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)'"'"'[^'"'"']*$/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/g' \
  -e 's/([Bb][Ee][Aa][Rr][Ee][Rr][[:space:]]+)[^;)]+([[:space:]]*,[[:space:]]*[^;)]*)*/\1[REDACTED]/g' \
  -e 's/[Ss][Kk]-[A-Za-z0-9_-]{8,}/[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])*"|'"'"'[^'"'"']*'"'"')([[:space:]]+"[^"]*")*/\1'"${PH}"'/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)(\{.*|[[][^)]*["'"'"'][^)]*.*)/\1[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])+|'"'"'[^'"'"']+)/\1[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)([^ ,'"${PH}"']+)([[:space:]]*,[[:space:]]*[^,]*)*/\1[REDACTED]/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'([^;,)]*,[[:space:]]*[^;)]*)+/\1'"${PH}"'/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'[^"[[:space:]};)][^;)]*([[:space:]]*,[[:space:]]*[^;)]*)*$/\1'"${PH}"'/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'("[^"]*")+/\1'"${PH}"'/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'[[:space:]]+"[^"]*$/\1'"${PH}"'/g' \
  -e 's/([Xx]-[Cc][Ii]-[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'"[^"]*$/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])*"|'"'"'[^'"'"']*'"'"')([[:space:]]+"[^"]*")*/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)(\{.*|[[][^;&)]*["'"'"'][^;&)]*.*)/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)("(\\.|[^"\\])+|'"'"'[^'"'"']+)/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)([^ ,;)&'"${PH}"']+)([[:space:]]*,[[:space:]]*[^;)&]*)*/\1[REDACTED]/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'([^;,&)]*,[[:space:]]*[^;)&]*)+/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'[^"[[:space:]};)&][^;)&]*([[:space:]]*,[[:space:]]*[^;)&]*)*$/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'("[^"]*")+/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'[[:space:]]+"[^"]*$/\1'"${PH}"'/g' \
  -e 's/([Aa][Pp][Ii][_.-]?[Kk][Ee][Yy][[:alnum:]_.-]*[[:space:]]*["'"'"']?[[:space:]]*[:=][[:space:]]*)'"${PH}"'"[^"]*$/\1'"${PH}"'/g' \
  -e 's/(["'"'"']?[Aa][Uu][Tt][Hh][Oo][Rr][Ii][Zz][Aa][Tt][Ii][Oo][Nn]["'"'"']?[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/g' \
  -e 's/'"${PH}"'/"[REDACTED]"/g'
