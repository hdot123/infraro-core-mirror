#!/bin/bash
# gateway-patrol.sh — node1 Kong 网关巡查派发器（launchd 每 30 分钟）
# 官方方案：Droid Exec 无头模式（droid exec）+ Factory Skill runbook
# 模型：custom:Hy3-Go-([REDACTED-HOST])（OpenCode Go 通道，hy3 模型池）
#       失败回退 Sonnet 5（Factory 托管——网关自身故障时仍可用）
# 演练：GATEWAY_PATROL_FORCE=1 bash gateway-patrol.sh
set -uo pipefail

BASE="$HOME/.factory/webhook"
LOG_DIR="$BASE/logs/gateway-patrol"
LOCKS="$BASE/locks"
STATUS="$BASE/status"
STATE="$BASE/state"
WORKSPACE="$BASE/gateway-patrol-workspace"
mkdir -p "$LOG_DIR" "$LOCKS" "$STATUS" "$STATE" "$WORKSPACE"

DAY_LOG="$LOG_DIR/patrol-$(date +%Y-%m-%d).jsonl"
RUN_LOCK="$LOCKS/gateway-patrol.run"
STATE_FILE="$STATUS/gateway-patrol.json"
FAIL_FILE="$STATE/patrol-probe-fails"
DROID_BIN="${DROID_BIN:-$(command -v droid || echo /usr/local/bin/droid)}"
PATROL_MODEL="${PATROL_MODEL:-custom:Hy3-Go-([REDACTED-HOST])}"
FALLBACK_MODEL="${PATROL_FALLBACK_MODEL:-claude-sonnet-5}"
now=$(date +%s)

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG_DIR/dispatcher.log"; }
rec() { echo "$1" >> "$DAY_LOG"; }

# ---- 运行锁（防重叠）----
if [ -f "$RUN_LOCK" ]; then
  # mtime 探测双分支：macOS launchd 宿主 = stat -f %m，GNU/Linux = stat -c %Y；
  # 两者皆不可用（极端嵌入式）→ 0 视作过期，宁重叠不误跳过巡查。
  lock_mtime=$(stat -f %m "$RUN_LOCK" 2>/dev/null || stat -c %Y "$RUN_LOCK" 2>/dev/null || echo 0)
  age=$(( now - lock_mtime ))
  if [ "$age" -lt 1500 ]; then log "skip: previous run active (${age}s)"; exit 0; fi
fi
touch "$RUN_LOCK"
trap 'rm -f "$RUN_LOCK"' EXIT

# ---- 1) 探测（只读）----
PROBE=$(bash "$BASE/scripts/gateway-patrol-probe.sh" 2>>"$LOG_DIR/probe-stderr.log")
prc=$?

ALARM=""
if [ $prc -ne 0 ]; then
  fails=$(( $(cat "$FAIL_FILE" 2>/dev/null || echo 0) + 1 ))
  echo "$fails" > "$FAIL_FILE"
  log "probe failed rc=$prc consecutive=$fails"
  rec "{\"ts\":$now,\"status\":\"probe_error\",\"consecutive\":$fails}"
  if [ "$fails" -ge 3 ]; then
    ALARM="{\"alarm\":true,\"severity\":\"critical\",\"fingerprint\":\"probe-unreachable\",\"findings\":[{\"severity\":\"critical\",\"code\":\"probe_unreachable\",\"detail\":\"node1 SSH/探测连续失败 $fails 次\"}]}"
  else
    exit 0
  fi
else
  echo 0 > "$FAIL_FILE"
  # 阈值评估（SC2259 修复，评审线程 P1 2026-10-10 裁定修）：生产副本固有形态
  # 是 printf | python3 - <<'PY'，heredoc 覆盖 fd 0 使 sys.stdin.read() 恒空 →
  # 真实告警永不触发。此处保持单进程（printf 管道直入 python3 -c），探测 JSON
  # 真正到达评估块；PATROL_EVAL_MODE 供沙盒放行本步（见
  # TestThresholdDispatchBehavior 沙盒锁定）。
  ALARM=$(printf '%s' "$PROBE" | PATROL_EVAL_MODE=real python3 -c '
import json, sys, hashlib
raw = sys.stdin.read()
try:
    p = json.loads(raw)
except Exception:
    print(json.dumps({"alarm": False, "fingerprint": "", "findings": []}))
    raise SystemExit(0)

findings = []
def add(sev, code, detail):
    findings.append({"severity": sev, "code": code, "detail": str(detail)[:160]})

containers = p.get("containers") or {}
for name in ("kong", "kong-database"):
    st = containers.get(name)
    if not st:
        add("critical", "container_missing:" + name, "not running")
    elif "Up" not in st:
        add("critical", "container_unhealthy:" + name, st)

c5 = p.get("http_5xx_30m") or 0
if c5 >= 15: add("critical", "5xx_spike", "5xx in 30m: %d" % c5)
elif c5 >= 6: add("warn", "5xx_elevated", "5xx in 30m: %d" % c5)

el = p.get("error_lines_30m") or 0
if el >= 5: add("warn", "error_log", "error lines in 30m: %d" % el)

disk = p.get("disk_root_pct") or 0
if disk >= 92: add("critical", "disk_critical", "root disk %d%%" % disk)
elif disk >= 85: add("warn", "disk_high", "root disk %d%%" % disk)

cd = p.get("cert_days_left")
if cd is not None:
    if cd <= 7: add("critical", "cert_expiry", "cert days left: %s" % cd)
    elif cd <= 14: add("warn", "cert_expiring", "cert days left: %s" % cd)

smoke = p.get("smoke") or {}
if smoke.get("ok") is False:
    add("critical", "smoke_fail", str(smoke.get("error"))[:150])

ups = p.get("upstreams") or {}
if isinstance(ups, dict):
    for name, h in ups.items():
        if isinstance(h, dict) and h.get("unhealthy", 0) > 0:
            add("critical", "upstream_down:" + name, json.dumps(h, ensure_ascii=False))

crit = [f for f in findings if f["severity"] == "critical"]
warn = [f for f in findings if f["severity"] == "warn"]
alarm = bool(crit) or len(warn) >= 2
fp = hashlib.sha1(("|".join(sorted(f["code"] for f in findings)) or "none").encode()).hexdigest()[:12]
sev = "critical" if crit else ("warn" if warn else "ok")
print(json.dumps({"alarm": alarm, "severity": sev, "fingerprint": fp, "findings": findings}, ensure_ascii=False))
')
fi

# ---- 演练强制注入 ----
if [ "${GATEWAY_PATROL_FORCE:-0}" = "1" ]; then
  ALARM='{"alarm":true,"severity":"critical","fingerprint":"forced-drill","findings":[{"severity":"critical","code":"forced_drill","detail":"SYNTHETIC DRILL — 派发链路演练（只读巡检，不做修改）"}]}'
fi

# ---- 2) 决策（去重/限流）----
DECISION=$(python3 - "$ALARM" "$BASE" "$DAY_LOG" <<'PY'
import json, os, sys, time
alarm = json.loads(sys.argv[1]); base = sys.argv[2]; day_log = sys.argv[3]
now = int(time.time())
if not alarm.get("alarm"):
    print("none none"); raise SystemExit(0)
fp = alarm.get("fingerprint") or "unknown"
lock = os.path.join(base, "locks", "gateway-patrol-%s.json" % fp)
if os.path.exists(lock):
    age = now - int(os.path.getmtime(lock))
    if age < 21600:
        print("suppress " + fp); raise SystemExit(0)
try: lines = open(day_log).read().splitlines()
except Exception: lines = []
dispatched = sum(1 for l in lines if '"dispatched"' in l)
if dispatched >= 8:
    print("rate_limited " + fp); raise SystemExit(0)
state_file = os.path.join(base, "status", "gateway-patrol.json")
last = 0
try: last = int(json.load(open(state_file)).get("last_dispatch_ts", 0))
except Exception: pass
if now - last < 1200:
    print("throttle " + fp); raise SystemExit(0)
print("dispatch " + fp)
PY
)
read -r ACTION FP <<< "$DECISION"

case "$ACTION" in
  none)
    rec "{\"ts\":$now,\"status\":\"ok\"}"
    log "ok: no alarm"
    ;;
  suppress|throttle|rate_limited)
    rec "{\"ts\":$now,\"status\":\"$ACTION\",\"fingerprint\":\"$FP\"}"
    log "$ACTION: fp=$FP"
    ;;
  dispatch)
    # === 派发账本接线（VAL-BUD-003，a′ 收门 observe-first）===
    # 计量+记账在 droid exec 之前（本通道派发事实在此产生）。守卫式 source：
    # lib 缺失（sync 漂移窗口）只禁用本次计量记账，禁绝派发即违规。
    # budget rc 恒 0（超限/不可达只 WARN+遥测）；claim rc=2（账本不可用）fail-open 继续派发。
    _LEDGER_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/dispatch-ledger.sh"
    if [ -f "$_LEDGER_LIB" ]; then
      # shellcheck disable=SC1090,SC1091  # 沙盒/部署路径动态解析，非跟随面
      source "$_LEDGER_LIB"
    else
      log "WARN: dispatch ledger lib unavailable (sync drift?), budget/ledger metering disabled this invocation"
    fi
    DISPATCH_UUID=$(python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
    if declare -F ledger_budget_check >/dev/null 2>&1; then
      ledger_budget_check "gateway-patrol" "gateway-patrol"
    fi
    if declare -F ledger_claim >/dev/null 2>&1; then
      ledger_claim "$DISPATCH_UUID" "gateway-patrol" "$FP" "gateway patrol alarm fp=${FP}" "" "gateway-patrol"
      _ledger_rc=$?
      ledger_append "$DISPATCH_UUID" "$FP" "dispatch_attempted" "source=gateway-patrol kind=gateway-patrol severity=$(printf '%s' "$ALARM" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('severity','?'))" 2>/dev/null || echo '?')"
      if [ "$_ledger_rc" -eq 2 ]; then
        log "WARN: dispatch ledger unavailable, fail-open continue (fp=${FP})"
      fi
    fi

    echo "{\"ts\":$now}" > "$LOCKS/gateway-patrol-$FP.json"
    PROMPT_FILE="$LOG_DIR/prompt-$now.md"
    RUN_LOG="$LOG_DIR/run-$now.log"
    PRETTY_PROBE=$(printf '%s' "$PROBE" | python3 -c "import json,sys
try: print(json.dumps(json.loads(sys.stdin.read()), indent=2, ensure_ascii=False))
except Exception: print(sys.stdin.read())" 2>/dev/null || printf '%s' "$PROBE")
    PRETTY_ALARM=$(printf '%s' "$ALARM" | python3 -c "import json,sys
try: print(json.dumps(json.loads(sys.stdin.read()), indent=2, ensure_ascii=False))
except Exception: print(sys.stdin.read())" 2>/dev/null || printf '%s' "$ALARM")
    {
      echo "# 网关巡查告警 — 自动派发"
      echo
      echo "- 时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "- 级别: $(printf '%s' "$ALARM" | python3 -c "import json,sys;print(json.load(sys.stdin).get('severity','?'))" 2>/dev/null || echo '?')"
      echo "- 指纹: $FP"
      echo
      echo "## 探测数据（gateway-patrol-probe.sh，只读）"
      echo '```json'
      echo "$PRETTY_PROBE"
      echo '```'
      echo
      echo "## 阈值评估"
      echo '```json'
      echo "$PRETTY_ALARM"
      echo '```'
      echo
      echo "## 任务"
      echo "1. 调用 gateway-patrol skill（~/.factory/skills/gateway-patrol/SKILL.md），按 triage 矩阵定位根因。"
      echo "2. 若确认故障：按对应 playbook 修复（备份 → 语法检查 → 部署 → reload → 端到端验证 → 失败回滚）。修复目标在 node1（SSH 别名 node1，root）。"
      echo "3. 修复后必须真实端到端验证（经网关调用模型收到出话）。"
      echo "4. 若为演练告警（forced_drill）：只做只读巡检 + 汇报链路可用性，不做任何修改。"
      echo "5. 无法修复时不要蛮干：输出阻塞点、建议、已收集证据。"
      echo
      echo "## 任务范围（硬边界，一次会话只做这一件事）"
      echo "- 只处理上方 findings 列出的告警项；范围外的发现只写进简报转人工，绝不动手。"
      echo "- 允许域（仅限网关巡查修复）：SSH node1 的只读诊断（docker ps/logs、管理 API、df/ls/curl、openssl 证书检查）；"
      echo "  修复仅限：/opt/kong/plugins/ai-router/ 下的 handler.lua 与 .opencode_go_key（备份→改→reload→smoke）、"
      echo "  kong/kong-database 容器重启、acme.sh 证书续期、日志清理（保留 handler.lua.bak.*）。"
      echo "- 禁止：修改 Mac 侧任何文件（settings.json、skills、webhook 脚本、LaunchAgents）；"
      echo "  触碰 Kong 之外的任何容器与服务（otelcol/prometheus/loki/tailscale 等只看不碰）；"
      echo "  安装软件；改动网络/防火墙；删除任何备份文件；创建 PR/推送/对外发消息；打印或落盘任何密钥。"
      echo
      echo "## 纪律"
      echo "- 绝不打印密钥/令牌明文；绝不 cat /opt/kong/plugins/ai-router/.opencode_go_key"
      echo "- 改生产文件必须备份；每次改动必须验证，失败回滚"
      echo "- 不修改 Mac 侧 ~/.factory/settings.json 等本地配置"
      echo
      echo "## 输出（中文，≤400 字）"
      echo "根因 / 动作（含备份名）/ 验证结果 / 遗留风险"
    } > "$PROMPT_FILE"

    log "dispatching fp=$FP model=$PATROL_MODEL"
    "$DROID_BIN" exec --auto high -m "$PATROL_MODEL" --cwd "$WORKSPACE" --tag gateway-patrol -o json -f "$PROMPT_FILE" > "$RUN_LOG" 2> "$RUN_LOG.err"
    rc=$?
    MODEL_USED="$PATROL_MODEL"
    IS_ERR=$(python3 -c "import json
try: print(str(bool(json.load(open('$RUN_LOG')).get('is_error'))).lower())
except Exception: print('unknown')" 2>/dev/null || echo unknown)
    if [ $rc -ne 0 ] || [ "$IS_ERR" = "true" ]; then
      log "primary model failed rc=$rc is_error=$IS_ERR — retrying with fallback $FALLBACK_MODEL"
      "$DROID_BIN" exec --auto high -m "$FALLBACK_MODEL" --cwd "$WORKSPACE" --tag gateway-patrol -o json -f "$PROMPT_FILE" > "$RUN_LOG" 2>> "$RUN_LOG.err"
      rc=$?
      MODEL_USED="$FALLBACK_MODEL"
      # 回退后重算执行面（评审线程 P3）：IS_ERR 若沿用主模型旧值，回退成功时
      # 会把 last_is_error=true 与 last_rc=0 一起持久化，污染下游消费面。
      IS_ERR=$(python3 -c "import json
try: print(str(bool(json.load(open('$RUN_LOG')).get('is_error'))).lower())
except Exception: print('unknown')" 2>/dev/null || echo unknown)
    fi

    python3 - "$STATE_FILE" "$now" "$rc" "$FP" "$MODEL_USED" "$IS_ERR" <<'PY'
import json, sys, os
p, now, rc, fp, model, is_err = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
d = {}
try: d = json.load(open(p))
except Exception: pass
d.update({"last_dispatch_ts": now, "last_rc": rc, "last_is_error": is_err, "last_fingerprint": fp, "last_model": model})
tmp = p + ".tmp"
open(tmp, "w").write(json.dumps(d, ensure_ascii=False))
os.replace(tmp, p)
PY
    rec "{\"ts\":$now,\"status\":\"dispatched\",\"rc\":$rc,\"fingerprint\":\"$FP\",\"model\":\"$MODEL_USED\",\"run_log\":\"$RUN_LOG\"}"
    log "dispatched rc=$rc fp=$FP model=$MODEL_USED log=$RUN_LOG"
    ;;
  *)
    # 决策异常兜底（评审线程 P2）：DECISION 为空（ALARM 非法 JSON / python3
    # 缺失）或未知 ACTION 时静默 exit 0，launchd 报成功掩盖巡查失败——落
    # rec/log 留痕后照常退出（观测面修复，不改变派发语义）。
    rec "{\"ts\":$now,\"status\":\"decision_error\",\"action\":\"$ACTION\",\"fingerprint\":\"$FP\"}"
    log "WARN: unknown/empty decision (action='${ACTION:-<empty>}' fp='${FP:-}') — check dispatcher.log / ALARM evaluation"
    ;;
esac
