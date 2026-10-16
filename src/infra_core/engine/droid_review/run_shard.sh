#!/usr/bin/env bash
# run_shard.sh — TD-DR-01 单 shard 执行脚本（7 缺陷修复版 + H1 失败分类/脱敏采集）
# 职责：
# 1. 解析 SHARD_FILES 环境变量（clean JSON，无引号包裹）
# 2. 校验文件存在性（head-src 或 BASE 任一侧存在即可——修已删除文件永久红）
# 3. 在 head-src 单 clone 内生成分片 diff（merge-base 已在 workflow 预计算）
# 4. 调用 droid exec，捕获 stdout（-o json），解析 findings
#    4a. exit 137（SIGKILL）且 stdout 空 → 同参数单次重试（2026-08-29 竞态加固）
#    4b. stdout 不可用 → 从本 run session jsonl 恢复 findings（recover_shard_findings.sh）
# 5. 校验 findings JSON schema（fail-closed；stdout 与会话两路皆空才红）
# 6. 输出 findings + diff artifact
# 7. 失败出口产 error_class 分类（H1 六件套 ①，h1-classify-capture）：
#    classify_shard_failure.sh 指纹表 → step summary（${GITHUB_STEP_SUMMARY}）
#    + workspace 根 error-classification.txt（workflow 上传入 debug artifact）。
#    同步打印实际命中的 droid 二进制路径与版本（宿主/下载二选一，Opus C4
#    相邻项：版本只告警不阻断）；宿主日志脱敏采集 capture_host_log.sh 入
#    droid-host-log-redacted.txt（Bearer/sk-/x-ci-key/apiKey → [REDACTED]，
#    上限 50 行）——debug artifact 只准携带脱敏内容（圆3 暴露面收敛裁决）。
# 8. F3 有界退出（PLAN v2.1 §3-F3，②有界+④数据留痕）：
#    心跳循环 30s 采样 session jsonl（行数/字节数）→ shard-heartbeat.log 独立
#    文件 + job log 单行摘要（双落）；软超时（SHARD_SOFT_TIMEOUT_SEC 默认
#    25min，只依赖 elapsed）与 turn 上限（SHARD_MAX_TURNS 默认 60，assistant
#    行计数，jsonl stale 时自动失效）越界 → 哨兵先行终止序列（写
#    shard-terminated.reason → SIGTERM 进程组 → 宽限 → SIGKILL）→ 失败出口
#    按哨兵分类（soft_timeout/turn_limit 排 exit_137 之前，遗书增补
#    turns/elapsed_sec/soft_timeout_sec/max_turns 校准字段）。137 重试分支带
#    「哨兵不存在」守卫（主动 kill 不进竞态重试，真实退出码照记不伪造）；
#    哨兵在 = findings 不采信（哨兵-恢复强制失败优先，防越界假绿）；droid exec
#    stdout 直写禁 tee（tee 下 $! 是 tee 的 PID）、exec 以 setsid 新进程组启动
#    且禁 kill -- -$$ 形态（会杀脚本自身——kill 隔离）。
set -euo pipefail

# 环境变量（从 workflow 注入）
# SHARD_ID, SHARD_FILES（JSON 数组）, BASE_REF, HEAD_REF, MERGE_BASE, RUN_ID
# FACTORY_API_KEY, GH_TOKEN

# ── 前置校验（fail-closed）──
if [ -z "${SHARD_ID:-}" ]; then
  echo "::error::SHARD_ID not set"
  exit 1
fi
if [ -z "${SHARD_FILES:-}" ]; then
  echo "::error::SHARD_FILES not set or empty — fail-closed"
  exit 1
fi
if [ -z "${BASE_REF:-}" ] || [ -z "${HEAD_REF:-}" ] || [ -z "${MERGE_BASE:-}" ]; then
  echo "::error::Missing BASE_REF/HEAD_REF/MERGE_BASE — fail-closed"
  exit 1
fi

echo "::group::Shard $SHARD_ID setup"
echo "Shard ID: $SHARD_ID"
echo "BASE_REF: $BASE_REF"
echo "HEAD_REF: $HEAD_REF"
echo "MERGE_BASE: $MERGE_BASE"

# ── Fix #1: 解析文件列表（clean JSON, 无引号包裹）──
# SHARD_FILES 现在是干净的 JSON 数组（workflow 用 toJson() 注入，无字面量引号包裹）
echo "SHARD_FILES raw: $SHARD_FILES"

# Validate JSON first — fail-closed if jq can't parse
if ! echo "$SHARD_FILES" | jq empty 2>/dev/null; then
  echo "::error::SHARD_FILES is not valid JSON: $SHARD_FILES"
  exit 1
fi

# bash 3.2 兼容（readarray/mapfile 是 bash 4+，macOS 自带 bash 无法本地测试）；
# 行为与 `readarray -t FILES < <(jq -r '.[]')` 等价：按行切分，无尾随空元素
FILES=()
while IFS= read -r f; do
  FILES+=("$f")
done < <(echo "$SHARD_FILES" | jq -r '.[]')

if [ ${#FILES[@]} -eq 0 ]; then
  echo "::error::SHARD_FILES parsed to empty file list — fail-closed"
  exit 1
fi

echo "Files in shard (${#FILES[@]}):"
printf '  %s\n' "${FILES[@]}"

# Log shard environment for debug artifact
{
  echo "SHARD_ID=$SHARD_ID"
  echo "BASE_REF=$BASE_REF"
  echo "HEAD_REF=$HEAD_REF"
  echo "MERGE_BASE=$MERGE_BASE"
  echo "RUN_ID=${RUN_ID:-unknown}"
  echo "FILES_COUNT=${#FILES[@]}"
  echo "FILES=${FILES[*]}"
} > shard-env.log

# ── Fix #3: 文件存在性校验——接受 head-src 或 BASE 任一侧 ──
# 已删除文件只存在于 BASE checkout（根目录），不在 head-src
# 过滤掉 deleted 状态的文件（它们在 diff 中作为删除出现，但不需要模型审查）
EXISTING_FILES=()
for f in "${FILES[@]}"; do
  if [ -f "head-src/$f" ]; then
    # 文件存在于 HEAD 侧（正常情况）
    EXISTING_FILES+=("$f")
  elif [ -f "$f" ]; then
    # 文件存在于 BASE 侧但不在 HEAD 侧 → 这是已删除文件
    # 仍然纳入 diff（作为删除），但记录日志
    echo "Note: $f exists in BASE but not HEAD (deleted file — included in diff for context)"
    EXISTING_FILES+=("$f")
  else
    echo "::warning::File $f not found in either HEAD or BASE checkout — skipping"
  fi
done

if [ ${#EXISTING_FILES[@]} -eq 0 ]; then
  echo "::warning::No reviewable files in shard $SHARD_ID after filtering"
  echo "{\"shard_id\":$SHARD_ID,\"findings\":[]}" > "findings-shard-${SHARD_ID}.json"
  : > "shard-${SHARD_ID}.diff"
  echo "::endgroup::"
  exit 0
fi

# ── Fix #2: 在 head-src 单 clone 内生成 diff ──
# merge-base 已在 workflow 的 shard_env 步骤中计算（fetch base SHA 后）
echo "Generating shard diff in head-src..."
DIFF_FILE="shard-${SHARD_ID}.diff"

cd head-src
git config --global --add safe.directory "$GITHUB_WORKSPACE/head-src" 2>/dev/null || true

# Build file list for git diff (relative to repo root)
# Use -- to separate paths; fail-closed if diff command fails
set +e
git diff "${MERGE_BASE}...HEAD" -- "${EXISTING_FILES[@]}" > "$GITHUB_WORKSPACE/$DIFF_FILE" 2>shard-diff-error.log
DIFF_EXIT=$?
set -e

if [ $DIFF_EXIT -ne 0 ]; then
  echo "::error::git diff failed (exit $DIFF_EXIT): $(cat shard-diff-error.log)"
  cd "$GITHUB_WORKSPACE"
  exit 1
fi

cd "$GITHUB_WORKSPACE"

if [ ! -s "$DIFF_FILE" ]; then
  echo "::warning::No diff content for shard $SHARD_ID (files may be unchanged in this range)"
  echo "{\"shard_id\":$SHARD_ID,\"findings\":[]}" > "findings-shard-${SHARD_ID}.json"
  : > "$DIFF_FILE"
  echo "::endgroup::"
  exit 0
fi

echo "Diff generated: $(wc -l < "$DIFF_FILE") lines"
echo "::endgroup::"

# ── 读取 prompt 模板（从 BASE checkout，非 HEAD）──
PROMPT_TEMPLATE="$(cat .github/review/shard-review-prompt.md)"

# ── F3 有界退出预算面（②有界：审查带软超时+turn 上限，超限带原因退出）──
# 数字链：软超时（默认 25min）< shard job timeout（45min）——越界自然终结留遗书，
# 不打穿硬杀。SHARD_KILL_GRACE_SEC / SHARD_HEARTBEAT_INTERVAL_SEC 仅测试调小；
# 生产 CI 不注入即走默认（30s 采样 / 10s 宽限）。
SHARD_SOFT_TIMEOUT_SEC="${SHARD_SOFT_TIMEOUT_SEC:-1500}"
SHARD_MAX_TURNS="${SHARD_MAX_TURNS:-60}"
SHARD_HEARTBEAT_INTERVAL_SEC="${SHARD_HEARTBEAT_INTERVAL_SEC:-30}"
SHARD_KILL_GRACE_SEC="${SHARD_KILL_GRACE_SEC:-10}"
# 哨兵先行（v2 P0-B）：越界时先写哨兵再发信号——分类判定靠哨兵文件，非 elapsed 反推
SENTINEL_FILE="shard-terminated.reason"
HEARTBEAT_LOG="shard-heartbeat.log"
# 发现6（2026-10-16 PR review）：预算分钟数向上取整 + 最低 1——地板除在软超时
# <60s（如 E2E 的 3s）时会把预算渲染成 0 分钟；展示层取整不影响软超时判定
# 本体（仍按 SHARD_SOFT_TIMEOUT_SEC 精确生效）。
BUDGET_MINUTES=$(( (SHARD_SOFT_TIMEOUT_SEC + 59) / 60 ))
[ "$BUDGET_MINUTES" -ge 1 ] 2>/dev/null || BUDGET_MINUTES=1
# 发现3（2026-10-16 PR review）：预算硬截止分钟 = ceil(软超时/60)——软超时
# SIGKILL 落在 [N·60, (N+1)·60) 区间内时，声称的「maximum of N minutes」会让
# 贴预算收尾的合规审查与心跳采样窗（SHARD_HEARTBEAT_INTERVAL_SEC）叠加出
# 「刚到预算即被杀」的边界误伤。prompt 同时披露硬截止，模型按硬截止收敛。
BUDGET_HARD_MINUTES="$BUDGET_MINUTES"

# ── Fix #4 (partial): 用文件传参避免 ARG_MAX ──
# 构造 prompt 文件（不用命令行参数传递）
cat > prompt.md << PROMPT_EOF
$PROMPT_TEMPLATE

<!-- droid-review-shard-marker:${RUN_ID:-unknown}-${SHARD_ID} -->

## Shard $SHARD_ID Diff

\`\`\`diff
$(cat "$DIFF_FILE")
\`\`\`

## Files in this shard

$(printf '%s\n' "${EXISTING_FILES[@]}")

## Budget Instructions

Budget: at most ${BUDGET_MINUTES} minutes; hard cutoff at ${BUDGET_HARD_MINUTES} minutes. Finish writing valid JSON output well before the hard cutoff.
Limit your output to the top 20 findings maximum. Return only valid JSON.
PROMPT_EOF

# ── Fix #4: 调用 droid exec，捕获 stdout (-o json)，解析 findings ──
# 2026-08-29 exit 137 完成期竞态加固（#40/#45/#47 多次实证，run 33226955612
# debug artifact：session jsonl 最后一行已是完整 findings JSON 但 stdout 0 字节）：
#   (1) exit 137 且 stdout 空 → 同参数单次重试
#   (2) stdout 不可用 → 会话 jsonl 兜底恢复（::warning 标注来源）
#   (3) fail-closed 语义保留：stdout 与会话两路皆空才红
echo "::group::Running droid exec for shard $SHARD_ID"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECOVER_HELPER="$SCRIPT_DIR/recover_shard_findings.sh"
# 失败分类 + 脱敏采集助手（H1 六件套 ①②）
CLASSIFY_HELPER="$SCRIPT_DIR/classify_shard_failure.sh"
REDACT_HELPER="$SCRIPT_DIR/redact_secrets.sh"
CAPTURE_HELPER="$SCRIPT_DIR/capture_host_log.sh"
DROID_CWD="${GITHUB_WORKSPACE:-$PWD}/head-src"
# F3 软超时/elapsed 时钟起点：紧贴 exec 调用捕获——前置面（二进制探测/BYOM
# 解析/diff 生成）的耗时不计入审查预算，也不计入遗书 elapsed_sec（它们不是
# 审查进程的存活时间）。

# ── 实际命中 droid 二进制路径与版本（H1 六件套 ①，Opus C4 相邻项）──
# 背景：宿主预装与 curl 下载两条路径（workflow Install 步）并存，失败诊断
# 需要知道 exec 实际用哪份二进制。解析在 exec 前一次完成；版本 < 0.200 只
# ::warning 告警不阻断（C4 裁决——不能因诊断信息把 shard 打红）。
DROID_BIN="$(command -v droid 2>/dev/null || true)"
DROID_VERSION="unknown"
if [ -n "$DROID_BIN" ]; then
  DROID_VERSION="$(droid --version 2>/dev/null | head -1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -1 || true)"
  DROID_VERSION="${DROID_VERSION:-unknown}"
  echo "droid binary: $DROID_BIN (version $DROID_VERSION)"
  case "$DROID_VERSION" in
    unknown)
      echo "::warning::droid --version 不可解析（binary: ${DROID_BIN}）——仅告警不阻断"
      ;;
    0.*)
      _droid_major="${DROID_VERSION%%.*}"
      _droid_minor="${DROID_VERSION#*.}"
      _droid_minor="${_droid_minor%%.*}"
      if [ "${_droid_major:-0}" -eq 0 ] && [ "${_droid_minor:-0}" -lt 200 ] 2>/dev/null; then
        echo "::warning::droid version $DROID_VERSION < 0.200（binary: ${DROID_BIN}）——已知相对 --cwd 静默崩溃版本，仅告警不阻断"
      fi
      ;;
  esac
else
  echo "::warning::droid not found on PATH at classification time（路径/版本不可打印）——仅告警不阻断"
fi

# BYOM 模型（2026-09-21 go-github 切换）：优先取 workflow 注入的
# BYOM_MODEL env（pull_request_target 下脚本本体来自 BASE checkout，
# env 才是 head 工作流定义的活值；值为 customModels[].model 形如
# dynamic/droid-review）；缺省读 settings.json customModels[0]
# 的 model 字段（droid exec 需要 model 名称而非 ID）；
# 两级皆失回退 deepseek-v4.1-flash。
if [ -z "${BYOM_MODEL:-}" ]; then
  BYOM_MODEL="$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.factory/settings.json'))); print(d['customModels'][0].get('model') or d['customModels'][0].get('id') or 'deepseek-v4.1-flash')" 2>/dev/null || true)"
fi
BYOM_MODEL="${BYOM_MODEL:-deepseek-v4.1-flash}"
echo "BYOM model: $BYOM_MODEL"

# Capture stdout for JSON findings output
DROID_OUTPUT_FILE="droid-exec-stdout.json"
# 重试前预清理：droid exec 崩溃态可能先写入半截输出再死（错误日志先于模型
# 输出落盘），残留会让 137 重试分支的 [ ! -s ] 误判「已有输出」而跳过重试，
# 且污染下游 jq 解析（2026-10-02 探针实证：错误文本前缀使 JSON 整体失配）。
: > "$DROID_OUTPUT_FILE"
# 发现4/5（2026-10-16 PR review）：脏 workspace 运行态残留清理——
#   ① 哨兵：上次 run 遗留的 shard-terminated.reason 会误触发哨兵强制失败
#      bail，并压制合法的 137 竞态重试（哨兵守卫把本次失败误判为主动 kill）；
#   ② 心跳日志：按 run 截断，跨 run 行混入会扭曲心跳轨迹判读；
#   ③ stderr：exec 前截断——跨 run 残留的 429/MetaError 行会污染分类器内容
#      指纹（rate_limit_429/model_* 误判）；137 重试 exec 用 2>> 追加，
#      两次 attempt 的诊断在同一文件内保留。
rm -f "$SENTINEL_FILE"
: > "$HEARTBEAT_LOG"
: > "shard-exec-error.log"
DROID_EXEC_START=$(date +%s)

# ── F3 心跳循环（双落：独立文件 + job log 单行摘要；纯观测，不杀进程）──
# R1 探针（2026-10-16 实测）：session jsonl 实时逐事件落盘 → 行数可用作 turn
# 计数；-o json 的 stdout 进程退出才落盘 → 软超时判定只依赖 elapsed，jsonl
# 缺失/停涨打 heartbeat=stale（turn 上限自动失效，遗书经分类器注明）。
# 心跳持 exec 的 $!（无 pgrep——共享 runner 误伤面根除，PLAN R2）。
heartbeat_loop() {
  # $1 = droid exec 的 PID（setsid 新进程组长）
  local exec_pid="$1"
  local start_epoch="$2"
  local last_bytes=-1
  local stale="live"
  # 采样间隔切片化：sleep 以 0.2s 步进轮询进程存活，exec 早退时循环即时收尾
  # （否则快进程场景空等一个完整采样间隔）
  local slices=$(( SHARD_HEARTBEAT_INTERVAL_SEC * 5 ))
  while kill -0 "$exec_pid" 2>/dev/null; do
    local now elapsed bytes turns
    now=$(date +%s)
    elapsed=$(( now - start_epoch ))
    pick_session_jsonl
    bytes=0
    if [ -n "$SESSION_JSONL" ] && [ -f "$SESSION_JSONL" ]; then
      bytes=$(wc -c < "$SESSION_JSONL")
    fi
    # jsonl 缺失或停涨 → stale（turn 计数不可信，turn 上限判定自动跳过；
    # 软超时判定不受影响——只依赖 elapsed）
    if [ "$last_bytes" = "-1" ]; then
      stale="live"
    elif [ ! -f "$SESSION_JSONL" ] || [ "$bytes" -eq "$last_bytes" ]; then
      stale="stale"
    else
      stale="live"
    fi
    # 发现2（2026-10-16 PR review）：turn 计数 = assistant 角色行数（与会话解析
    # 口径一致），非 lines-2 固定减法——头部事件数漂移、tool_use/meta 等非
    # assistant 事件行都会让固定减法虚增计数，turn 上限提前误杀健康审查。
    if [ -n "$SESSION_JSONL" ] && [ -f "$SESSION_JSONL" ]; then
      turns=$(grep -c '"role": *"assistant"' "$SESSION_JSONL" 2>/dev/null || true)
      turns=${turns:-0}
      case "$turns" in ''|*[!0-9]*) turns=0 ;; esac
    else
      turns=0
    fi
    {
      echo "epoch=$now elapsed=${elapsed}s turns=$turns bytes=$bytes heartbeat=$stale"
    } >> "$HEARTBEAT_LOG"
    echo "::[heartbeat] shard=$SHARD_ID elapsed=${elapsed}s turns=$turns bytes=$bytes heartbeat=$stale"
    # 越界判定：软超时只看 elapsed（jsonl 面无关）；turn 上限只在心跳非 stale 时生效
    if [ "$elapsed" -ge "$SHARD_SOFT_TIMEOUT_SEC" ]; then
      terminate_bounded "$exec_pid" "soft_timeout" "$turns"
      return
    fi
    if [ "$stale" != "stale" ] && [ "$turns" -gt "$SHARD_MAX_TURNS" ]; then
      terminate_bounded "$exec_pid" "turn_limit" "$turns"
      return
    fi
    last_bytes="$bytes"
    local i=0
    while [ "$i" -lt "$slices" ] && kill -0 "$exec_pid" 2>/dev/null; do
      sleep 0.2
      i=$(( i + 1 ))
    done
  done
}

# ── F3 终止序列（v2 P0-B：哨兵先行）──
# ① 写哨兵（分类依据，先于任何信号——分类器靠它判 soft_timeout/turn_limit）
# ② SIGTERM 进程组 → ③ 宽限 → ④ 进程组 SIGKILL。
# kill 隔离（v2.1）：exec 以 setsid 启动（新进程组长，PGID=PID），对
# $exec_pid 所在组发负号信号；禁用 kill -- -$$（非交互 bash 下 $$ 是脚本自身
# 进程组，会连 run_shard.sh 一起杀——脚本自身退出码必须非 SIGTERM 系）。
terminate_bounded() {
  local exec_pid="$1" reason="$2" turns_at_kill="$3"
  {
    echo "epoch=$(date +%s) reason=$reason turns_at_termination=$turns_at_kill"
  } >> "$HEARTBEAT_LOG"
  printf '%s\n' "$reason" > "$SENTINEL_FILE"
  echo "::warning::sentinel written: $SENTINEL_FILE ($reason, turns=$turns_at_kill)"
  TURNS_AT_TERMINATION="$turns_at_kill"
  export TURNS_AT_TERMINATION
  # bash 3.2 兼容（P1-1）：$var 后紧跟多字节字符（如全角括号）时，其首字节
  # 会被并入变量名展开（set -u 下直接 fatal，终止序列中断 → droid 孤儿化），
  # 引用多字节前的变量必须加花括号。
  echo "::warning::droid exec 越界（${reason}）— 终止序列启动：SIGTERM → ${SHARD_KILL_GRACE_SEC}s 宽限 → SIGKILL"
  # 负 PID 组信号（exec 以 setsid 启动：PGID=PID）；直发 PID 兜底。
  # 不用 kill -- 语法：非交互 bash 下 -$$ 形态指向脚本自身进程组（会连
  # run_shard.sh 一起杀）——脚本自身退出码必须非 SIGTERM 系（kill 隔离）。
  kill -TERM "-$exec_pid" 2>/dev/null || kill -TERM "$exec_pid" 2>/dev/null || true
  sleep "$SHARD_KILL_GRACE_SEC"
  if kill -0 "$exec_pid" 2>/dev/null; then
    echo "::warning::宽限期结束进程仍存活 — 进程组 SIGKILL"
    kill -KILL "-$exec_pid" 2>/dev/null || kill -KILL "$exec_pid" 2>/dev/null || true
  fi
}

# 会话 jsonl 路径（与 recover_shard_findings.sh 同构：cwd 中 / 替换为 -）。
# 每轮心跳重扫：jsonl 在 droid exec 启动后才创建，文件名是启动时未知的 UUID。
SESSION_JSONL_DIR="${FACTORY_SESSIONS_DIR:-$HOME/.factory/sessions}/$(printf '%s' "$DROID_CWD" | tr '/' '-')"
SESSION_JSONL=""
pick_session_jsonl() {
  # 标记时间点之后的最新 jsonl；无会话目录/无新文件 → 置空（心跳打 stale）
  SESSION_JSONL=""
  [ -d "$SESSION_JSONL_DIR" ] || return 0
  # glob + -nt 选最新（SC2012；候选名为 UUID，无空格/换行）。
  # 空目录下裸 glob 不展开 → -f 前置判断；同秒多文件取 glob 序最后一个
  # （非确定但无害——fresh 窗口内的会话都是本 run 的）。
  local candidate f
  candidate=""
  for f in "$SESSION_JSONL_DIR"/*.jsonl; do
    [ -f "$f" ] || continue
    if [ -z "$candidate" ] || [ "$f" -nt "$candidate" ]; then
      candidate="$f"
    fi
  done
  if [ -n "$candidate" ] && [ ! "$candidate" -ot "$DROID_EXEC_START_FILE" ]; then
    SESSION_JSONL="$candidate"
  fi
}
# 新鲜度参照文件（mtime=exec 启动时刻；便携：BSD/GNU test 都支持 -nt）。
# sleep 切片的 0.2s 粒度 > 文件系统 mtime 精度（1s），同秒竞态由「候选 mtime ≥
# ref mtime」容忍（recover_shard_findings.sh 同款语义）；候选早于 exec 启动
# （上一 run 遗留最新文件）才判 stale——宁可 stale 误标也不采旧会话 turn 数。
DROID_EXEC_START_FILE=".droid-exec-start-ref.$$"
python3 -c "import os,sys; open(sys.argv[1],'w').close(); os.utime(sys.argv[1],(int(sys.argv[2]),int(sys.argv[2])))" \
  "$DROID_EXEC_START_FILE" "$DROID_EXEC_START" 2>/dev/null || : > "$DROID_EXEC_START_FILE"

set +e
# 直写必须（v2.1）：tee 管道下 $! 是 tee 的 PID，心跳「直接持 PID」前提被破坏。
# exec 以显式新进程组启动（新进程组长，PGID=PID）——终止序列按组发信号不波及
# 本脚本。setsid 二进制是 util-linux 专属（macOS 无），用 python3 os.setsid +
# execvp 等价形态（引擎面已依赖 python3，双平台一致）。
# bash 3.2 陷阱（2026-10-16 killmicro 实证）：`setsid_exec droid ... &` 这种
# 「函数 + &」后台作业由 fork 出的中间 subshell 执行——$! 是 subshell 的 PID
# 且仍在脚本自身进程组，真正 setsid 的 droid 是孙进程、组不相干。此时按
# -$! 发组信号 = 「no such group」，SIGTERM 打不到 droid（孤儿化）；wait 拿
# 的是 subshell 的 143，遗书退出码失真。因此 exec 调用必须内联 setsid 包装
# （简单命令 + & 直接 exec，$! 即 droid PID=PGID），不引入函数间接层。
python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
  droid exec \
  --auto medium \
  -m "$BYOM_MODEL" \
  --cwd "$DROID_CWD" \
  --tag "shard-${SHARD_ID}" \
  -f prompt.md \
  -o json > "$DROID_OUTPUT_FILE" 2>>shard-exec-error.log &
DROID_PID=$!
heartbeat_loop "$DROID_PID" "$DROID_EXEC_START"
wait "$DROID_PID"
DROID_EXIT=$?
set -e
rm -f "$DROID_EXEC_START_FILE"

echo "droid exec exit code: $DROID_EXIT"

# (1) exit 137 = SIGKILL，完成期竞态特征：session 完整但 stdout 0 字节。
# 重试约 50% 恢复；仍败则走 (2) 会话兜底。
# F3 哨兵守卫（v2 P0-B）：哨兵在 = 主动 kill（软超时/turn 上限终止序列），
# 不得进入竞态重试（否则 25min×2 打穿 45min job timeout）；真实退出码照记不伪造。
if [ "$DROID_EXIT" -eq 137 ] && [ ! -s "$DROID_OUTPUT_FILE" ] && [ ! -f "$SENTINEL_FILE" ]; then
  echo "::warning::droid exec exit 137 (SIGKILL) with empty stdout — retrying once (same args)"
  : > "$DROID_OUTPUT_FILE"
  set +e
  # 直写必须：同首次调用（tee 下 $! 是 tee 的 PID）；内联 setsid 包装同理
  #（函数 + & 的 subshell 陷阱见首次调用处注释）。
  # 发现1（2026-10-16 PR review）：重试 exec 同样后台化 + heartbeat_loop 监控
  # ——裸 wait 让重试绕过软超时/turn 上限，吊死的重试只能等 45min job timeout
  # 硬杀，无哨兵无分类遗书。重试时钟独立起算（RETRY_START），秒数预算沿用
  # 同一 SHARD_SOFT_TIMEOUT_SEC：重试自身越界即被终止序列收口（写哨兵 →
  # 分类走 soft_timeout/turn_limit，fail-closed），不再打穿 job timeout。
  RETRY_START=$(date +%s)
  python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    droid exec \
    --auto medium \
    -m "$BYOM_MODEL" \
    --cwd "$DROID_CWD" \
    --tag "shard-${SHARD_ID}" \
    -f prompt.md \
    -o json > "$DROID_OUTPUT_FILE" 2>>shard-exec-error.log &
  DROID_PID=$!
  heartbeat_loop "$DROID_PID" "$RETRY_START"
  wait "$DROID_PID"
  DROID_EXIT=$?
  set -e
  echo "droid exec retry exit code: $DROID_EXIT"
fi

# ── F3 哨兵-恢复强制失败优先（v2.1 随修 B）──
# 哨兵在 = 本审查已被判越界，即使 droid 碰巧收敛产出 findings 也**不采信**，
# 仍按哨兵内容走 soft_timeout / turn_limit 分类（防「越界但碰巧收敛」假绿
# 混入验收数据）。此 bail 块必须在会话恢复调用之前——位置由
# tests/test_shard_bounded_exit.py::TestRunShardBoundedExitContract::test_06 锁定。
if [ -f "$SENTINEL_FILE" ]; then
  SENTINEL_REASON="$(tr -d '[:space:]' < "$SENTINEL_FILE")"
  echo "::error::哨兵在案（${SENTINEL_REASON}）——本次审查越界，findings 不采信（强制失败优先）"
  echo "::error::stderr log (redacted):"
  bash "$REDACT_HELPER" < shard-exec-error.log 2>/dev/null || true
  bash "$CAPTURE_HELPER" "droid-host-log-redacted.txt" || true
  bash "$CLASSIFY_HELPER" "$SHARD_ID" "$DROID_EXIT" "$DROID_EXEC_START" "$DROID_BIN" "$DROID_VERSION" shard-exec-error.log "$DROID_OUTPUT_FILE" "$SENTINEL_FILE" || true
  cd "$GITHUB_WORKSPACE"
  exit 1
fi

STDOUT_USABLE="false"
# 大小护栏：异常崩溃态的 stdout 可能达数百 MB（重试风暴/日志回显），
# jq 对超大文件既慢又可能 OOM；findings JSON 正常体积 << 10MB，超限按
# 不可用处理走会话恢复（2026-10-02 探针加固）。
STDOUT_MAX_BYTES=$((10 * 1024 * 1024))
if [ -s "$DROID_OUTPUT_FILE" ]; then
  STDOUT_SIZE=$(wc -c < "$DROID_OUTPUT_FILE")
  if [ "$STDOUT_SIZE" -le "$STDOUT_MAX_BYTES" ] && jq empty "$DROID_OUTPUT_FILE" 2>/dev/null; then
    STDOUT_USABLE="true"
  elif [ "$STDOUT_SIZE" -gt "$STDOUT_MAX_BYTES" ]; then
    echo "::warning::droid exec stdout ${STDOUT_SIZE} bytes exceeds ${STDOUT_MAX_BYTES} cap — treating as unusable, session recovery will handle"
  fi
fi

# (2) stdout 不可用（空或非 JSON）且执行失败/无输出 → 会话兜底，再 fail-closed
RECOVERED_SOURCE=""
if [ "$STDOUT_USABLE" = "false" ] && { [ "$DROID_EXIT" -ne 0 ] || [ ! -s "$DROID_OUTPUT_FILE" ]; }; then
  echo "::warning::droid exec stdout unusable (exit $DROID_EXIT) — trying session jsonl recovery before fail-closed"
  if bash "$RECOVER_HELPER" "$DROID_CWD" "$DROID_EXEC_START" "$SHARD_ID" "findings-shard-${SHARD_ID}.json"; then
    RECOVERED_SOURCE="session"
  else
    echo "::error::Session jsonl recovery produced no findings — fail-closed（两路皆空）"
    echo "::error::stderr log (redacted):"
    bash "$REDACT_HELPER" < shard-exec-error.log 2>/dev/null || true
    bash "$CAPTURE_HELPER" "droid-host-log-redacted.txt" || true
    bash "$CLASSIFY_HELPER" "$SHARD_ID" "$DROID_EXIT" "$DROID_EXEC_START" "$DROID_BIN" "$DROID_VERSION" shard-exec-error.log "" "$SENTINEL_FILE" || true
    cd "$GITHUB_WORKSPACE"
    exit 1
  fi
fi

# If droid exec failed AND we have no valid output, fail-closed
if [ "$DROID_EXIT" -ne 0 ]; then
  echo "::error::droid exec failed for shard $SHARD_ID (exit $DROID_EXIT)"
  if [ "$STDOUT_USABLE" != "true" ] && [ "$RECOVERED_SOURCE" = "" ]; then
    echo "::error::No valid JSON output from droid exec — fail-closed"
    cd "$GITHUB_WORKSPACE"
    exit 1
  fi
  if [ "$STDOUT_USABLE" = "true" ]; then
    echo "::warning::droid exec returned non-zero but produced valid JSON output, continuing"
  else
    echo "::warning::droid exec returned non-zero but findings recovered from session jsonl, continuing"
  fi
fi

if [ "$RECOVERED_SOURCE" = "" ] && [ ! -s "$DROID_OUTPUT_FILE" ]; then
  echo "::error::droid exec produced no stdout output — fail-closed"
  echo "::error::stderr log (redacted):"
  bash "$REDACT_HELPER" < shard-exec-error.log 2>/dev/null || true
  bash "$CAPTURE_HELPER" "droid-host-log-redacted.txt" || true
  bash "$CLASSIFY_HELPER" "$SHARD_ID" "$DROID_EXIT" "$DROID_EXEC_START" "$DROID_BIN" "$DROID_VERSION" shard-exec-error.log "" "$SENTINEL_FILE" || true
  cd "$GITHUB_WORKSPACE"
  exit 1
fi

echo "::endgroup::"

# ── 解析 findings from droid exec stdout ──
echo "::group::Parsing findings from droid exec output"

# Try to extract findings JSON from stdout
# droid exec -o json may wrap the model output in its own JSON envelope
# Try direct parse first, then look for nested result/content fields
FINDINGS_PARSED="false"

# 会话兜底已产出 findings 文件时跳过 stdout 解析
if [ "$RECOVERED_SOURCE" != "" ]; then
  FINDINGS_PARSED="true"
  echo "Findings recovered from session jsonl — skipping stdout parse"
fi

# Attempt 1: Direct JSON parse (stdout IS the findings)
if jq -e '.shard_id != null and .findings != null' "$DROID_OUTPUT_FILE" >/dev/null 2>&1; then
  cp "$DROID_OUTPUT_FILE" "findings-shard-${SHARD_ID}.json"
  FINDINGS_PARSED="true"
  echo "Direct JSON parse succeeded"
fi

# Attempt 2: Look for result field (droid envelope)
if [ "$FINDINGS_PARSED" = "false" ]; then
  if jq -e '.result' "$DROID_OUTPUT_FILE" >/dev/null 2>&1; then
    RESULT=$(jq -r '.result' "$DROID_OUTPUT_FILE")
    
    # Try direct parse first
    if echo "$RESULT" | jq -e '.shard_id != null and .findings != null' >/dev/null 2>&1; then
      echo "$RESULT" | jq '.' > "findings-shard-${SHARD_ID}.json"
      FINDINGS_PARSED="true"
      echo "Envelope .result parse succeeded"
    else
      # Extract JSON from markdown code blocks
      # shellcheck disable=SC2016  # 单引号是有意的：```json/``` 是字面量 markdown 围栏，不需要展开
      JSON_BLOCK=$(echo "$RESULT" | sed -n '/```json/,/```/p' | sed '1d;$d' | sed '/^$/d')
      if [ -n "$JSON_BLOCK" ] && echo "$JSON_BLOCK" | jq -e '.shard_id != null and .findings != null' >/dev/null 2>&1; then
        echo "$JSON_BLOCK" | jq '.' > "findings-shard-${SHARD_ID}.json"
        FINDINGS_PARSED="true"
        echo "Markdown code block extraction succeeded"
      fi
    fi
  fi
fi

# Attempt 3: Look for content/output field with embedded JSON
if [ "$FINDINGS_PARSED" = "false" ]; then
  # Try extracting JSON block from text content
  JSON_BLOCK=$(jq -r '
    if .content then .content
    elif .output then .output
    elif .message then .message
    elif type == "string" then .
    else empty
    end' "$DROID_OUTPUT_FILE" 2>/dev/null || true)

  if [ -n "$JSON_BLOCK" ]; then
    # Try to extract JSON object from the text
    EXTRACTED=$(echo "$JSON_BLOCK" | sed -n 's/.*\({.*"shard_id".*"findings".*}\).*/\1/p' | head -1)
    if [ -n "$EXTRACTED" ] && echo "$EXTRACTED" | jq -e '.shard_id != null and .findings != null' >/dev/null 2>&1; then
      echo "$EXTRACTED" | jq '.' > "findings-shard-${SHARD_ID}.json"
      FINDINGS_PARSED="true"
      echo "Content field JSON extraction succeeded"
    fi
  fi
fi

# Attempt 4 (2026-09-30 run 36623737488 实证)：JSON 修复兜底。
# 模型偶发违反「裸 JSON」约定（开场白 + ```json 围栏），且 message 字段内嵌
# 未转义双引号（如 `tail = lines[-30:]` then `"轮询开始"` cycle ...）——
# 严格 jq 解析必然失败，三路尝试全红 → fail-closed 把一次有效评审整个报废。
# 修复器只转义 JSON 字符串内部的裸引号（结构性 token 不动），修好的 JSON
# 必须通过 shard_id/findings 硬校验，失败继续 Attempt 5 / fail-closed。
if [ "$FINDINGS_PARSED" = "false" ]; then
  echo "Attempting JSON repair (unescaped quotes inside message strings)..."
  REPAIR_HELPER="$SCRIPT_DIR/repair_shard_findings.py"
  if python3 "$REPAIR_HELPER" "$DROID_OUTPUT_FILE" "findings-shard-${SHARD_ID}.json" "$SHARD_ID"; then
    FINDINGS_PARSED="true"
    echo "JSON repair extraction succeeded"
  else
    echo "JSON repair: no repairable findings block in stdout"
  fi
fi

# Attempt 5 (2026-09-30 run 36623737488 实证)：会话 jsonl 兜底扩展。
# 原逻辑只在 stdout 为空/非 JSON 时触发 recover_shard_findings.sh；stdout 是
# 合法 envelope 但内容不可解析的形态被漏掉。三路解析 + 修复全失败后再试
# 一次会话恢复（会话 assistant text 与 stdout result 同源同内容）。不改变
# fail-closed 语义：会话恢复也失败则照常 fail-closed。
if [ "$FINDINGS_PARSED" = "false" ]; then
  echo "::warning::stdout unparseable though valid JSON envelope — trying session jsonl recovery"
  if bash "$RECOVER_HELPER" "$DROID_CWD" "$DROID_EXEC_START" "$SHARD_ID" "findings-shard-${SHARD_ID}.json"; then
    RECOVERED_SOURCE="session-late"
    FINDINGS_PARSED="true"
    echo "Findings recovered from session jsonl after stdout parse exhaustion"
  else
    echo "::error::Session jsonl recovery also produced no findings"
  fi
fi

# Fail-closed: no valid findings JSON found
if [ "$FINDINGS_PARSED" = "false" ]; then
  echo "::error::Could not parse valid findings JSON from droid exec output"
  echo "::error::Raw output (first 20 lines):"
  head -20 "$DROID_OUTPUT_FILE" 2>/dev/null | bash "$REDACT_HELPER" || true
  echo "::error::— fail-closed: shard $SHARD_ID produces no valid findings"
  bash "$CAPTURE_HELPER" "droid-host-log-redacted.txt" || true
  bash "$CLASSIFY_HELPER" "$SHARD_ID" "$DROID_EXIT" "$DROID_EXEC_START" "$DROID_BIN" "$DROID_VERSION" shard-exec-error.log "$DROID_OUTPUT_FILE" "$SENTINEL_FILE" || true
  cd "$GITHUB_WORKSPACE"
  exit 1
fi

echo "Findings parsed successfully"
echo "::endgroup::"

# ── 校验 findings schema ──
echo "Validating findings schema..."
# validate_findings 从脚本自身目录解析（引擎自包含）：reusable workflow
# （droid-review-shards.yml）在消费仓任意 CWD 运行，不能假设消费仓存在
# scripts/droid_review/ 布局（M5 后消费副本删除）。独立变量名 _ENGINE_DIR，
# 与 PR #55 的 SCRIPT_DIR 会话恢复逻辑互不依赖、合并互不冲突。
_ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -c "
import json, sys
sys.path.insert(0, '$_ENGINE_DIR')
from publish_findings import validate_findings
data = json.load(open('findings-shard-${SHARD_ID}.json'))
if not validate_findings(data):
  print('::error::Invalid findings schema', file=sys.stderr)
  sys.exit(1)
finding_count = len(data.get('findings', []))
print(f'Findings schema valid ({finding_count} findings)')
" || {
  echo "::error::Findings schema validation failed — fail-closed"
  bash "$CAPTURE_HELPER" "droid-host-log-redacted.txt" || true
  bash "$CLASSIFY_HELPER" "$SHARD_ID" "$DROID_EXIT" "$DROID_EXEC_START" "$DROID_BIN" "$DROID_VERSION" shard-exec-error.log "" "$SENTINEL_FILE" || true
  exit 1
}

echo "Shard $SHARD_ID completed successfully"
