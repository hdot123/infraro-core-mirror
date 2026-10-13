#!/usr/bin/env python3
"""TDD tests for reconcile-evolution.sh dedup-key fix (VAL-LEDGER-001).

缺陷链（architecture.md §6 M5 / mission reconcile-dedup-fix）：
1. :286 主查询只取非终态 → cancel 后的卡不在查询集 → GAP-A 下个整点把其 open
   GitHub 镜像重判孤儿 → 补偿重建幽灵 Linear 卡（INFRA-1288 幽灵卡工厂）。
2. :433-486 查重 = 标题字符串精确比对 + linear-linkback 子串检查：
   标题是键（有碰撞/改名风险），linkback 只做子串包含、非等值键。
3. :319-337 终态查询 first:50 无分页且缺 duplicate 终态，终态积压 >50 或
   duplicate 判定时镜像永不收敛 → open 镜像滞留 → 被 GAP-A 重判孤儿。
4. terminal absorption 是三个 cancel 点中唯一缺 stale-orphan-${REF}.canceled
   墓碑（哨兵）检查/写入的点。

修复语义（行为断言；真源脚本静态断言 + 沙盒 stub 网络，零活体探测）：
- 查重键 = GH issue 号锚点（linear-linkback 回链，经 extract_anchor 等值验证）。
  终态关闭循环顺带收集锚点验证过的终态 ref 注册表（GAP_A_TERMINAL_REFS，
  一次遍历两个消费者：先于 gh close 收集，close 失败不影响「Linear 已有终态」
  事实）。GAP-A 孤儿判定四段：①锚点 ∈ 终态注册表 → skip（幽灵卡工厂修复）；
  ②锚点 ∈ 非终态 ref 集 → skip；③锚点空（无键）→ 标题仅作非补偿信号
  （终态/非终态标题命中即 skip，标题永不作为关闭/补偿的肯定键）；④否则孤儿 →
  补偿创建并回写 <!-- linear-linkback 新ref --> 锚点评论（键自愈，并发幂等）。
- 终态对账查询扩全部非取消即终态（completed/canceled/duplicate）+ after 游标
  内部分页（first:50 截断修复，页数安全上限 50）。
- terminal absorption 前查墓碑（幂等 skip）→ 成功后写墓碑 + 清 fallback 计数器。

沙盒网络面：脚本内嵌 python 全部经 urllib 调 Linear——用 PYTHONPATH
sitecustomize 劫持 urlopen（零真实网络；真网络到达即 401 失败）；
gh/trigger-droid 用 PATH stub。观测锚定沙盒自身产物（日志/墓碑/创建记录）。
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
REPO_SCRIPT = REPO_ROOT / "webhook-scripts" / "reconcile-evolution.sh"

# 素数尾号且互不为子串：GH 镜像号 ↔ Linear ref 语义（沙盒 stub 数据集）
GH_CANCELLED_NUMBER = 900001  # Linear INFRA-900001 已 cancel，锚点 #900001
GH_DUPLICATE_NUMBER = 900002  # Linear INFRA-900002 终态=duplicate，锚点 #900002
GH_DONE_ANCHORED = 900003  # Linear INFRA-900003 Done，href 形态锚点 #900003
GH_DONE_ANCHORLESS = 900004  # Linear INFRA-900004 Done，无锚点（fail-closed 不关镜像）
GH_OPEN_NUMBER = 900010  # 非终态 INFRA-900010 的 open 镜像
OPEN_REF = "INFRA-900010"
CANCELLED_REF = "INFRA-900001"

PY3 = "/opt/homebrew/bin/python3"

# 0001 年日期：fromisoformat 可解析且年龄恒为超大正数；内含引号会破坏
# f-string 单引号包裹的字面量（:611/:871），故禁用引号注入
ANCIENT_DATE = "0001-01-01T00:00:00Z"


# ============================================================================
# Part 1 — 静态语义断言（真源脚本源码，可执行语义锚定）
# ============================================================================


class TestQuerySemantics:
    """查重查询语义：终态全集 + 分页修复。"""

    def test_nonterminal_query_state_set(self):
        """主查询（孤儿判定候选集）保留非终态全集（文件内 shell 转义引号）。"""
        content = REPO_SCRIPT.read_text()
        assert 'in: [\\"triage\\", \\"backlog\\", \\"unstarted\\", \\"started\\"]' in content

    @pytest.mark.parametrize("state_type", ["completed", "canceled", "duplicate"])
    def test_terminal_query_covers_all_noncancel_terminal_states(self, state_type):
        """终态对账查询覆盖全部非取消即终态（duplicate 缺失 = 镜像永不收敛）。"""
        content = REPO_SCRIPT.read_text()
        assert f'\\"{state_type}\\"' in content, f"终态查询状态集缺少 {state_type}"

    def test_terminal_query_has_after_cursor_pagination(self):
        """终态查询带 after 游标（first:50 截断修复）+ pageInfo 投影 + 页数上限。"""
        content = REPO_SCRIPT.read_text()
        assert re.search(r"\$after:\s*String", content), "缺 after 游标参数"
        assert re.search(r"\$first:\s*Int!", content), "分页需显式 first 变量"
        assert "pageInfo { hasNextPage endCursor }" in content
        assert re.search(r"range\(50\)", content), "分页循环需要安全上限（防死循环）"


class TestDedupKeySemantics:
    """查重键：GH issue 号锚点（linear-linkback），替代标题字符串精确比对。"""

    def test_anchor_extraction_helper_in_use(self):
        """extract_anchor.py 至少两处消费：终态镜像定位 + 孤儿判定（补偿后回写检查用 python 内联 helper）。"""
        content = REPO_SCRIPT.read_text()
        assert content.count(f'{PY3} "$SCRIPT_DIR/extract_anchor.py" issue') >= 2

    def test_terminal_mirror_location_by_anchor(self):
        """终态镜像定位仍为 GH search（GH 侧定位）+ 锚点等值判定（既有先例保留）。"""
        content = REPO_SCRIPT.read_text()
        assert '--search "${T_REF}"' in content, "镜像定位用 GH search"
        assert (
            f'ANCHOR=$({PY3} "$SCRIPT_DIR/extract_anchor.py" issue "$GH_NUMBER" "$REPO" 2>"$ANCHOR_ERR" || echo "")'
            in content
        ), "镜像判定必须经 extract_anchor.py 锚点等值"
        assert "anchor consistent, closing GitHub Issue" in content

    def test_anchor_registry_collected_in_closure_loop(self):
        """关闭循环必须收集锚点验证过的终态 ref 注册表，且先于 close（close 失败不丢事实）。"""
        content = REPO_SCRIPT.read_text()
        assert 'GAP_A_TERMINAL_REFS=""' in content, "注册表必须初始化"
        assert (
            """if [ "$ANCHOR" = "$T_REF" ]; then
                # VAL-LEDGER-001：锚点验证过的终态 ref 注册表（GAP-A 查重键数据源）；
                # 先于 close 收集——close 失败也不影响「Linear 已有终态记录」事实
                GAP_A_TERMINAL_REFS="${GAP_A_TERMINAL_REFS} ${T_REF}\""""
            in content
        ), "关闭循环内缺终态 ref 注册表收集（须在 anchor consistent 分支首行）"

    def test_gap_a_dedup_uses_anchor_key_not_titles(self):
        """GAP-A 孤儿判定按锚点等值查重；标题降级为「无键时」的非补偿信号。"""
        content = REPO_SCRIPT.read_text()
        assert "GAP_A_TERMINAL_REFS" in content
        assert "LINEAR_TITLES_TMP" not in content, "旧标题临时文件名必须移除"
        assert re.search(r"linear_titles", content) is None, (
            "GAP-A 孤儿判定不得再消费旧标题集合（linear_titles）"
        )
        assert "'linear-linkback' in (r.stdout or '')" not in content, (
            "linkback 子串包含检查必须移除（升级为锚点等值键）"
        )
        # 孤儿判定用 python 内联 get_anchor（同 CLI 语义），补偿后回写才用
        # CLI 形态；两个消费点各在其位（负向断言：孤儿判定段不得再出现 CLI 调用）
        assert "def get_anchor(number):" in content, "孤儿判定必须内联锚点提取 helper"
        # python 内四段判定的可执行语义锚
        assert "terminal_refs" in content and "live_refs" in content
        assert "terminal_titles" in content and "live_titles" in content
        assert re.search(r"if anchor and anchor in terminal_refs", content), "缺终态注册表等值判定"
        assert re.search(r"if anchor and anchor in live_refs", content), "缺非终态 ref 判定"
        assert re.search(r"if not anchor and title in terminal_titles", content), (
            "缺无键时终态标题非补偿信号"
        )
        assert re.search(r"if not anchor and title in live_titles", content), (
            "缺无键时 live 标题信号"
        )
        # bash 侧两个复核消费点（python 环境传递故障的双保险）
        assert 'grep -qx "$CAND_ANCHOR"' in content
        assert 'echo "$LINEAR_REFS" | grep -qw "$CAND_ANCHOR"' in content
        assert "LINEAR_REFS=" in content

    def test_gap_a_creation_anchors_to_gh_number(self):
        """补偿创建以 GH issue 号为锚：desc 引用 + 新 ref 锚点评论回写。"""
        content = REPO_SCRIPT.read_text()
        assert "linear-linkback %s" in content
        assert "print(f'{linear_ref}|{linear_uuid}|{o_number}')" in content, (
            "补偿结果必须带 GH 号（驱动锚点回写）"
        )
        assert "o_number = os.environ.get('O_NUMBER', '')" in content
        assert re.search(r"gh issue comment.*--body-file", content), (
            "锚点评论必须经 gh issue comment --body-file 写入 GitHub 镜像"
        )

    def test_gap_a_anchor_enrichment_after_compensation(self):
        """补偿成功后回写锚点评论，且并发已写场景幂等跳过。"""
        content = REPO_SCRIPT.read_text()
        assert (
            f'O_ANCHOR=$({PY3} "$SCRIPT_DIR/extract_anchor.py" issue "$G_O_NUMBER" "$REPO" 2>/dev/null || echo "")'
            in content
        )
        assert (
            'log "GAP-A: anchor already present (#${G_O_NUMBER}: ${O_ANCHOR}), skip comment write-back (idempotent)"'
            in content
        )
        assert "printf '<!-- linear-linkback %s -->" in content, (
            "锚点评论必须含 <!-- linear-linkback INFRA-新ref --> HTML 注释"
        )


# ============================================================================
# Part 2 — 三处 cancel 点墓碑（哨兵）全覆盖
# ============================================================================


class TestCancelTombstones:
    """cancel 墓碑 = stale-orphan-${REF}.canceled 哨兵（failed-session/stale-orphan 先例）。"""

    def test_failed_session_sentinel_check_and_write(self):
        """failed-session 出口：先查哨兵（幂等 skip）→ 成功后写哨兵。"""
        content = REPO_SCRIPT.read_text()
        assert (
            """STALE_ORPHAN_SENTINEL_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
                if [ -f "$STALE_ORPHAN_SENTINEL_FILE" ]; then
                    log "  ${LINEAR_REF}: failed session fallback already executed (sentinel found), skip (idempotent)"
                    continue
                fi"""
            in content
        )
        assert (
            """log "  ${LINEAR_REF}: FAILED SESSION FALLBACK executed — Linear canceled (count=${STALE_ORPHAN_COUNT})"
                    # 写幂等 sentinel
                    touch "$STALE_ORPHAN_SENTINEL_FILE\""""
            in content
        )

    def test_stale_orphan_sentinel_check_and_write(self):
        """stale-orphan 出口：先查哨兵 → 成功后写哨兵（先例复用）。"""
        content = REPO_SCRIPT.read_text()
        assert (
            """STALE_ORPHAN_SENTINEL_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
            if [ -f "$STALE_ORPHAN_SENTINEL_FILE" ]; then
                log "  ${LINEAR_REF}: stale orphan fallback already executed (sentinel found), skip (idempotent)"
                continue
            fi"""
            in content
        )
        assert (
            """log "  ${LINEAR_REF}: STALE ORPHAN FALLBACK executed — Linear canceled (count=${STALE_ORPHAN_COUNT})"
                # 写幂等 sentinel
                touch "$STALE_ORPHAN_SENTINEL_FILE\""""
            in content
        )

    def test_terminal_absorption_sentinel_check_before_cancel(self):
        """terminal absorption：执行前查墓碑（三处 cancel 唯一缺检查的点）。"""
        content = REPO_SCRIPT.read_text()
        assert (
            """TOMBSTONE_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.canceled"
        if [ -f "$TOMBSTONE_FILE" ]; then
            log "  ${LINEAR_REF}: tombstone found (already canceled by fallback), skip absorption (idempotent)"
            continue
        fi"""
            in content
        ), "terminal absorption 缺 cancel 前墓碑哨兵检查"

    def test_terminal_absorption_sentinel_write_after_success(self):
        """terminal absorption 成功后写墓碑+清 fallback 计数器。"""
        content = REPO_SCRIPT.read_text()
        assert (
            """if [ "$ABSORB_RESULT" = "OK" ]; then
            log "  ${LINEAR_REF}: terminal absorption completed (GitHub mirror closed, moved to canceled)"
            # VAL-LEDGER-001：写 cancel 墓碑（与 failed-session/stale-orphan 同语义，
            # 三处 cancel 点全覆盖）+ 清理 fallback 计数器（墓碑已落，计数器失效）
            touch "$TOMBSTONE_FILE"
            STALE_ORPHAN_COUNT_FILE="${LOCK_DIR}/stale-orphan-${LINEAR_REF}.count"
            rm -f "$STALE_ORPHAN_COUNT_FILE"
        else"""
            in content
        ), "terminal absorption 缺成功后墓碑写入"

    def test_terminal_absorption_dry_run_logs_tombstone_intent(self):
        """DRY_RUN 下 terminal absorption 记录墓碑意图但不写文件。"""
        content = REPO_SCRIPT.read_text()
        assert (
            'log "  ${LINEAR_REF}: [DRY_RUN] tombstone will be written after absorption succeeds"'
            in content
        )


# ============================================================================
# Part 3 — 全脚本沙盒行为测试（stub 网络；观测锚定沙盒日志/墓碑/创建记录）
# ============================================================================

SITECUSTOMIZE = """# 沙盒 Linear GraphQL stub：劫持 urllib（零真实网络，真网络到达即失败）
import json as _json
import os as _os
import urllib.request as _ur

_SANDBOX = _os.environ.get("SANDBOX", "")


class _FakeResp:
    def __init__(self, payload):
        self._b = _json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _log(line):
    if _SANDBOX:
        with open(_os.path.join(_SANDBOX, "linear-api.log"), "a") as f:
            f.write(line + "\\n")


def _created():
    try:
        with open(_os.path.join(_SANDBOX, "linear-created.jsonl")) as f:
            return [_json.loads(l) for l in f if l.strip()]
    except Exception:
        return []


_P1 = _json.loads(
    '{"data":{"issues":{"nodes":['
    '{"identifier":"INFRA-900001","title":"[evolution] dup A","state":{"name":"Canceled","type":"canceled"}},'
    '{"identifier":"INFRA-900002","title":"[evolution] dup B","state":{"name":"Duplicate","type":"duplicate"}}],'
    '"pageInfo":{"hasNextPage":true,"endCursor":"CUR1"}}}}'
)
_P2 = _json.loads(
    '{"data":{"issues":{"nodes":['
    '{"identifier":"INFRA-900003","title":"[evolution] dup C","state":{"name":"Done","type":"completed"}},'
    '{"identifier":"INFRA-900004","title":"[evolution] dup D","state":{"name":"Done","type":"completed"}}],'
    '"pageInfo":{"hasNextPage":false,"endCursor":"CUR2"}}}}'
)


def _fake_urlopen(req, timeout=None):
    try:
        payload = _json.loads(req.data.decode())
    except Exception:
        _log("UNPARSED")
        return _FakeResp({"data": {}})
    q = payload.get("query", "")
    v = payload.get("variables") or {}
    if "issueCreate" in q:
        rows = _created()
        ident = "INFRA-%d" % (900021 + len(rows))
        node = {"id": "uuid-new-%d" % len(rows), "identifier": ident,
                "url": "https://linear.app/infra/issue/" + ident}
        with open(_os.path.join(_SANDBOX, "linear-created.jsonl"), "a") as f:
            f.write(_json.dumps(node) + "\\n")
        _log("issueCreate:" + ident)
        return _FakeResp({"data": {"issueCreate": {"issue": node}}})
    if "issueUpdate" in q:
        _log("issueUpdate")
        return _FakeResp({"data": {"issueUpdate": {"success": True}}})
    if "commentCreate" in q:
        _log("commentCreate")
        return _FakeResp({"data": {"commentCreate": {"comment": {"id": "c1"}}}})
    if "comments { nodes { body } }" in q:
        _log("commentScan")
        return _FakeResp({"data": {"issue": {"comments": {"nodes": []}}}})
    if "issue(id:" in q:
        st = "started"
        try:
            with open(_os.path.join(_SANDBOX, "absorb-current-state.txt")) as f:
                st = f.read().strip() or st
        except Exception:
            pass
        _log("issueState:" + st)
        return _FakeResp({"data": {"issue": {"state": {"type": st}}}})
    if "states" in q and "team(" in q:
        return _FakeResp({"data": {"team": {"states": {"nodes": [
            {"id": "SID_CANCELED", "name": "Canceled", "type": "canceled"}]}}}})
    if "labels(filter:" in q:
        return _FakeResp({"data": {"team": {"labels": {"nodes": [
            {"id": "LID1", "name": "evolution-found"}]}}}})
    if "issues(" in q and "backlog" in q:
        rows = [{"id": "uuid-open", "identifier": "INFRA-900010", "title": "Open card",
                 "state": {"name": "Backlog", "type": "backlog"},
                 "updatedAt": "0001-01-01T00:00:00Z"}]
        for n in _created():
            rows.append({"id": n["id"], "identifier": n["identifier"], "title": "Comp card",
                         "state": {"name": "Backlog", "type": "backlog"},
                         "updatedAt": "0001-01-01T00:00:00Z"})
        _log("mainQuery:" + ",".join(r["identifier"] for r in rows))
        # 主查询已分页：单页 + hasNextPage:false（pageInfo 缺失时新代码同样 break）
        return _FakeResp({"data": {"issues": {"nodes": rows,
                                              "pageInfo": {"hasNextPage": False,
                                                           "endCursor": "MCUR"}}}})
    if "issues(" in q and "pageInfo" in q:
        if not v.get("after"):
            _log("terminalPage:1")
            return _FakeResp(_P1)
        _log("terminalPage:2:" + str(v.get("after")))
        return _FakeResp(_P2)
    _log("UNMATCHED")
    return _FakeResp({"data": {}})


if _SANDBOX:
    _ur.urlopen = _fake_urlopen
"""

GH_STUB = """#!/bin/bash
: "${WEBHOOK_BASE:=${SANDBOX:-/tmp/reconcile-dedup-stub}}"
mkdir -p "${WEBHOOK_BASE}"
echo "gh $*" >> "${WEBHOOK_BASE}/gh.log"
GH_NUM=""
for cand in 900001 900002 900003 900004 900010; do
  for a in "$@"; do
    if [ "$a" = "$cand" ]; then GH_NUM="$cand"; fi
  done
done
if [[ "$*" == *"issue comment"* ]]; then
  BODY_FILE=""
  prev=""
  for a in "$@"; do
    if [ "$prev" = "--body-file" ]; then BODY_FILE="$a"; fi
    prev="$a"
  done
  mkdir -p "${WEBHOOK_BASE}/comments"
  if [ -n "$BODY_FILE" ] && [ -f "$BODY_FILE" ]; then
    cat "$BODY_FILE" >> "${WEBHOOK_BASE}/comments/${GH_NUM}.txt"
  fi
  echo "comment:${GH_NUM}" >> "${WEBHOOK_BASE}/gh.log"
  exit 0
fi
if [[ "$*" == *"issue close"* ]]; then
  if [ "${GH_CLOSE_FAIL:-}" = "$GH_NUM" ]; then
    echo "closefail:${GH_NUM}" >> "${WEBHOOK_BASE}/gh.log"
    exit 1
  fi
  mkdir -p "${WEBHOOK_BASE}/closed"
  touch "${WEBHOOK_BASE}/closed/${GH_NUM}"
  echo "close:${GH_NUM}" >> "${WEBHOOK_BASE}/gh.log"
  exit 0
fi
if [[ "$*" == *"issue list"* ]]; then
  open_json="["
  first=1
  for cand in ${GH_LIST_ORDER:-900004 900010 900001 900002 900003}; do
    [ -f "${WEBHOOK_BASE}/closed/${cand}" ] && continue
    if [[ "$*" == *"--search"* ]]; then
      # search 分支：仅返回 ref 匹配的候选
      echo "$*" | grep -q "INFRA-${cand}" || continue
      [ $first -eq 1 ] || open_json="$open_json,"
      open_json="$open_json {\\"number\\": $cand}"
      first=0
    else
      # list-all 分支（GAP-A 源清单）：带 title/createdAt
      [ $first -eq 1 ] || open_json="$open_json,"
      open_json="$open_json {\\"number\\": $cand, \\"title\\": \\"[evolution] mirror $cand\\", \\"createdAt\\": \\"2026-01-01T00:00:00Z\\"}"
      first=0
    fi
  done
  echo "$open_json ]"
  exit 0
fi
exit 0
"""

# 沙盒 GH 镜像锚点库（与 evolution_utils Tier1/Tier2 同语义的三种形态）
SANDBOX_ANCHORS = {
    "900001": "<!-- linear-linkback INFRA-900001 -->",  # Tier1 HTML 注释
    "900002": '<a href="https://linear.app/infra/issue/INFRA-900002">INFRA-900002</a>',  # Tier2 锚文本
    "900003": "https://linear.app/infra/issue/INFRA-900003",  # Tier2 href
    # 900004：无锚点（fail-closed 不关镜像；GAP-A 侧为第一孤儿）
}

EXTRACT_ANCHOR_STUB = """#!/usr/bin/env python3
import os, re, sys

number = sys.argv[2]
base = os.path.join(os.environ['SANDBOX'], 'comments')
try:
    text = open(os.path.join(base, number + '.txt')).read()
except Exception:
    sys.exit(0)

m = re.search(r'<!--\\s*linear-linkback\\s+(INFRA-\\d+)\\s*-->', text)
if m:
    print(m.group(1))
    sys.exit(0)
m = re.search(r'>\\s*(INFRA-\\d+)\\s*<', text)
if m:
    print(m.group(1))
    sys.exit(0)
m = re.search(r'linear\\.app/\\S*?/(INFRA-\\d+)', text)
if m:
    print(m.group(1))
"""


def _build_sandbox(tmp_path: Path, anchored: bool = True) -> Path:
    """复制真源脚本到沙盒并 stub 全部网络面（urllib 劫持 + PATH stub）。"""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    for sub in ("status", "logs", "locks", "bin", "lib", "pylib", "comments"):
        (sandbox / sub).mkdir()

    script = sandbox / "reconcile-evolution.sh"
    content = REPO_SCRIPT.read_text()
    content = content.replace("/opt/homebrew/bin/python3", "python3")
    content = content.replace(
        'WEBHOOK_BASE="${HOME}/.factory/webhook"',
        f'WEBHOOK_BASE="{sandbox}"\nexport WEBHOOK_BASE',
    )
    content = content.replace(
        'SCRIPT_DIR="${HOME}/.factory/webhook/scripts"', f'SCRIPT_DIR="{sandbox}"'
    )
    content = content.replace('SCRIPT_DIR="${WEBHOOK_BASE}/scripts"', f'SCRIPT_DIR="{sandbox}"')
    content = re.sub(
        r'TEAM_ID=\$\(TEAM_KEY="\$TEAM_KEY" python3 -c ".*?" 2>/dev/null \|\| echo ""\)',
        'TEAM_ID="fake-team-id"',
        content,
        flags=re.DOTALL,
    )
    script.write_text(content)
    script.chmod(0o755)

    (sandbox / "pylib" / "sitecustomize.py").write_text(SITECUSTOMIZE)
    (sandbox / "lib" / "op-mcp.sh").write_text(
        '#!/bin/bash\nexport LINEAR_API_KEY="fake-key"\nOP_VAULT_SEVER="sever"\nop_get_field() { echo "fake-key"; }\n'
    )
    (sandbox / "lib" / "posthog.sh").write_text(
        '#!/bin/bash\nsend_posthog_event() { echo "posthog:$*" >> "${WEBHOOK_BASE}/posthog.log"; }\n'
    )
    (sandbox / "lib" / "linear-queue.sh").write_text("# stub\n")

    (sandbox / "extract_anchor.py").write_text(EXTRACT_ANCHOR_STUB)
    (sandbox / "extract_anchor.py").chmod(0o755)
    if anchored:
        for number, text in SANDBOX_ANCHORS.items():
            (sandbox / "comments" / f"{number}.txt").write_text(text + "\n")

    gh = sandbox / "bin" / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)

    trig = sandbox / "trigger-droid.sh"
    trig.write_text('#!/bin/bash\necho "trigger:$*" >> "${WEBHOOK_BASE}/trigger.log"\n')
    trig.chmod(0o755)
    return sandbox


def _run_tick(sandbox: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{sandbox / 'bin'}:{env['PATH']}",
            "HOME": str(sandbox.parent),
            "SANDBOX": str(sandbox),
            "PYTHONPATH": str(sandbox / "pylib"),
            "TEAM_ID": "fake-team-id",
        }
    )
    env.pop("LINEAR_API_KEY", None)
    env.pop("DRY_RUN", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(sandbox / "reconcile-evolution.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(sandbox),
    )


def _latest_log(sandbox: Path, result: subprocess.CompletedProcess) -> str:
    logs = sorted((sandbox / "logs").glob("reconcile-*.log"))
    assert logs, f"reconcile log missing; stderr={result.stderr[-2000:]}"
    return logs[-1].read_text()


def _api_log(sandbox: Path) -> str:
    p = sandbox / "linear-api.log"
    return p.read_text() if p.exists() else ""


@pytest.fixture()
def sandbox(tmp_path) -> Path:
    return _build_sandbox(tmp_path)


class TestFailClosedOnIncompleteKeyFace:
    """GAP-A fail-closed 语义（droid-review round-2 修复的行为级回归防护）。"""

    def test_terminal_query_failure_skips_orphan_judgement(self, tmp_path):
        """终态查询失败 → 键面不完整 → GAP-A 孤儿判定整体跳过（不补偿任何卡）。"""
        sandbox = _build_sandbox(tmp_path)
        # sitecustomize 劫持层注入终态查询异常：marker 文件存在时 raise
        (sandbox / "fail-terminal-query").write_text("1")
        sitecustomize = (sandbox / "pylib" / "sitecustomize.py").read_text()
        sitecustomize = sitecustomize.replace(
            "def _fake_urlopen(req, timeout=None):",
            (
                "def _fake_urlopen(req, timeout=None):\n"
                "    try:\n"
                "        _q = _json.loads(req.data.decode()).get('query', '')\n"
                "    except Exception:\n"
                "        _q = ''\n"
                "    if 'canceled' in _q and _os.path.exists(_os.path.join(_SANDBOX, 'fail-terminal-query')):\n"
                "        _log('terminalQueryBoom')\n"
                "        raise RuntimeError('terminal query injected failure')\n"
            ),
        )
        (sandbox / "pylib" / "sitecustomize.py").write_text(sitecustomize)

        r = _run_tick(sandbox)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        # 键面不完整的显式留痕 + GAP-A fail-closed 跳过
        assert "terminal-state query failed" in log, log
        assert (
            "terminal registry incomplete (query failed) — skip orphan judgement entirely" in log
        ), log
        # 无锚点镜像（#900004，正常 tick 下会被补偿）不被补偿：fail-closed 证据
        assert "orphan detected" not in log
        assert "issueCreate" not in _api_log(sandbox)

    def test_anchor_not_in_any_registry_skips_compensation(self, tmp_path):
        """锚点存在但不命中任何注册表（键面未知）→ SKIP_ANCHOR_UNKNOWN 不补偿。"""
        sandbox = _build_sandbox(tmp_path)
        # #900010（open 卡 INFRA-900010 的镜像）改挂一个不存在于任何注册表的锚点
        (sandbox / "comments" / "900010.txt").write_text("<!-- linear-linkback INFRA-999999 -->\n")
        # 900004 预关闭 → GAP-A 首候选变为 900010（GH_LIST_ORDER 默认序）
        (sandbox / "closed").mkdir(exist_ok=True)
        for n in ("900004",):
            (sandbox / "closed" / n).touch()

        r = _run_tick(sandbox)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "not in terminal/live registries (fail-closed)" in log, log
        assert "orphan detected" not in log, "未知锚点绝不得流入补偿"
        assert "issueCreate" not in _api_log(sandbox)


class TestSandboxDedupFix:
    """沙盒全链路：cancel 卡不被重判孤儿 + 终态镜像按锚点关闭 + 补偿锚点自愈。"""

    def test_cancelled_card_not_orphaned_and_terminal_mirrors_closed(self, sandbox):
        # open 卡的状态文件：running + 死 PID → 标记 retrigger（触发路径可观测）
        (sandbox / "status" / f"{OPEN_REF}.json").write_text(
            '{"status": "running", "pid": 999999999}'
        )

        r1 = _run_tick(sandbox)
        assert r1.returncode == 0, f"tick1 failed: {r1.stderr[-3000:]}"
        log1 = _latest_log(sandbox, r1)

        # 终态镜像按锚点关闭：canceled/duplicate/done 三种终态 + 三种锚点形态
        assert "anchor consistent, closing GitHub Issue #900001" in log1, log1
        assert "anchor consistent, closing GitHub Issue #900002" in log1, (
            "duplicate 终态必须被对账关闭"
        )
        assert "anchor consistent, closing GitHub Issue #900003" in log1
        # 无锚点镜像 fail-closed 不关闭
        assert "no anchor in GitHub Issue #900004" in log1
        assert "closing GitHub Issue #900004" not in log1
        gh_log = (sandbox / "gh.log").read_text()
        assert "close:900001" in gh_log
        assert "close:900002" in gh_log
        assert "close:900003" in gh_log
        assert "close:900004" not in gh_log

        # 分页两页均被消费（first:50 截断修复的行为证据）
        api1 = _api_log(sandbox)
        assert "terminalPage:1" in api1
        assert "terminalPage:2:CUR1" in api1

        # open 卡正常触发（未被误伤）
        trig_log = (sandbox / "trigger.log").read_text()
        assert OPEN_REF in trig_log

        # GAP-A：锚点为空的 open 镜像按源序在批次上限（GAP_A_BATCH_CAP 默认 2，
        # gap-a-batch-cap feature）内补偿，且补偿后回写锚点评论（键自愈）
        assert "orphan detected" in log1
        assert "created Linear Issue INFRA-900021" in log1, log1
        assert "for GitHub #900004" in log1
        assert "comment:900004" in (sandbox / "gh.log").read_text()
        anchor_text = (sandbox / "comments" / "900004.txt").read_text()
        assert "<!-- linear-linkback INFRA-900021 -->" in anchor_text

        # 批次上限内的全候选补偿：真孤儿 #900010 与 #900004 同 tick 补偿——
        # 三张终态镜像的 SKIP_* 行存在于同一次扫描中却未遮蔽它（补偿饥饿
        # 旧 break 语义的强反证；批次 summary 计数可见）
        assert "orphan detected — GitHub Issue #900010" in log1
        assert "created Linear Issue INFRA-900022" in log1
        assert "for GitHub #900010" in log1
        assert "GAP-A: batch summary — cap=2 compensated=2 deferred=0 total_orphans=2" in log1
        anchor10_text = (sandbox / "comments" / "900010.txt").read_text()
        assert "<!-- linear-linkback INFRA-900022 -->" in anchor10_text

        # 幽灵卡工厂否定断言：canceled 卡（INFRA-900001）绝不被重建
        assert "issueCreate:INFRA-900001" not in api1

        # --- tick 2：收敛——两个孤儿已在 tick1 批次上限内补偿且锚点回写 →
        # 键面全命中（live/终态注册表）→ 零孤儿、零新增补偿 ---
        (sandbox / "gh.log").write_text("")
        r2 = _run_tick(sandbox)
        assert r2.returncode == 0, f"tick2 failed: {r2.stderr[-3000:]}"
        log2 = _latest_log(sandbox, r2)
        assert "for GitHub #900004" not in log2, "#900004 已有键不得重补偿"
        assert "orphan detected" not in log2, "tick1 已批次补偿，tick2 必须收敛"
        assert "GAP-A: batch summary — cap=2 compensated=0 deferred=0 total_orphans=0" in log2, log2
        created = (sandbox / "linear-created.jsonl").read_text().strip().splitlines()
        assert len(created) == 2, f"收敛后不得再补偿: {created}"
        api2 = _api_log(sandbox)
        assert api2.count("issueCreate:") == 2, "tick2 累计补偿应为 2（无新增）"
        # 幽灵否定：canceled 卡仍绝不被重建
        assert "issueCreate:INFRA-900001" not in api2

        # --- tick 3：#900010 补偿锚点已回写 → 键面命中 → 收敛（零新增补偿）---
        r3 = _run_tick(sandbox)
        assert r3.returncode == 0, f"tick3 failed: {r3.stderr[-3000:]}"
        log3 = _latest_log(sandbox, r3)
        assert "no orphaned GitHub issues detected" in log3, log3
        created3 = (sandbox / "linear-created.jsonl").read_text().strip().splitlines()
        assert len(created3) == 2, f"收敛后不得再补偿: {created3}"

    def test_cancelled_mirror_close_failure_still_not_ghost(self, tmp_path):
        """核心缺陷行为证明：close 失败 → 镜像滞留 open，但锚点 ∈ 终态注册表
        → GAP-A 不补偿（幽灵卡工厂修复的行为级证据）。"""
        sandbox = _build_sandbox(tmp_path)
        env = {"GH_CLOSE_FAIL": "900001", "GH_LIST_ORDER": "900001 900004 900010 900002 900003"}
        r = _run_tick(sandbox, env)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        # close 确实失败（镜像滞留 open 的前提）
        assert "WARNING: failed to close GitHub Issue #900001" in log, log
        # 注册表防线：锚点命中终态注册表 → skip 孤儿判定
        assert (
            "anchored to INFRA-900001 (terminal registry) — skip orphan judgement (dedup key hit)"
            in log
        ), log
        api = _api_log(sandbox)
        assert "issueCreate:INFRA-900001" not in api, "canceled 卡绝不被重建"

    def test_tombstone_guards_terminal_absorption(self, tmp_path):
        """无墓碑 DRY_RUN：只记录墓碑意图；执行成功：写墓碑+清计数器；
        墓碑存在：幂等 skip absorption。"""
        sandbox = _build_sandbox(tmp_path, anchored=False)
        # 全镜像预关闭 → 终态循环全部 skip（focus 在 absorption 路径）；
        # open 卡 #900010 镜像关闭 → 5a2 判定 absorption 路径
        for n in ("900001", "900002", "900003", "900004", "900010"):
            (sandbox / "closed").mkdir(exist_ok=True)
            (sandbox / "closed" / n).touch()
        # 计数器预置（absorption 成功后应被清理）
        (sandbox / "locks" / f"stale-orphan-{OPEN_REF}.count").write_text("3")

        # tick1: DRY_RUN —— 记录意图，不写墓碑
        r1 = _run_tick(sandbox, {"DRY_RUN": "1"})
        assert r1.returncode == 0, f"dry tick failed: {r1.stderr[-3000:]}"
        log1 = _latest_log(sandbox, r1)
        assert "[DRY_RUN] tombstone will be written after absorption succeeds" in log1, log1
        assert not (sandbox / "locks" / f"stale-orphan-{OPEN_REF}.canceled").exists()

        # tick2: 真执行 —— absorption 完成 + 墓碑落 + 计数器清
        r2 = _run_tick(sandbox)
        assert r2.returncode == 0, f"exec tick failed: {r2.stderr[-3000:]}"
        log2 = _latest_log(sandbox, r2)
        assert "terminal absorption completed" in log2, log2
        tombstone = sandbox / "locks" / f"stale-orphan-{OPEN_REF}.canceled"
        assert tombstone.exists(), "absorption 成功后必须写墓碑"
        assert not (sandbox / "locks" / f"stale-orphan-{OPEN_REF}.count").exists(), (
            "absorption 成功后必须清理 fallback 计数器"
        )

        # tick3: 墓碑幂等 —— 即使 stub 仍报非终态，墓碑拦截重复 absorption
        r3 = _run_tick(sandbox)
        assert r3.returncode == 0, f"idem tick failed: {r3.stderr[-3000:]}"
        log3 = _latest_log(sandbox, r3)
        assert (
            "tombstone found (already canceled by fallback), skip absorption (idempotent)" in log3
        ), log3
        assert "terminal absorption completed" not in log3


class TestScriptStaticQuality:
    """静态质量门：bash 语法 + shellcheck（仓库侧 disable 与 CI 同款）。"""

    def test_bash_syntax(self):
        result = subprocess.run(["bash", "-n", str(REPO_SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_shellcheck_no_errors(self):
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed on this runner")
        result = subprocess.run(
            [
                "shellcheck",
                "--severity=error",
                "--exclude=SC1090,SC1091,SC2016,SC2154",
                str(REPO_SCRIPT),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
