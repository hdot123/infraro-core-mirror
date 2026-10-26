#!/usr/bin/env python3
"""共用派发账本行为断言（VAL-LEDGER-002，shared-ledger）。

语义（mission shared-ledger / architecture §6 M5）：
- 键 = Linear UUID（不用 INFRA-xxx：跨团队移动变号）；append-only（一卡一派发记录）
- 宿主 = ${WEBHOOK_BASE}/status/ 文件族目录扩展（trigger-droid.sh STATUS_DIR 先例）
- trigger-droid 启动先查账本去重（droid exec 前拦截）
- GAP-A 补偿创建路径写账本（linear_uuid + 源 GH issue 号）
- 与 R1 派发共用同一账本语义（一卡一派发记录，一卡一记录全来源通吃）

分层：
  Part A  账本库纯函数契约（rc 契约 0/1/2、append-only no-clobber、by-ref 视图、
          事件流留痕、跨团队变号 ref_moved）——bash -c 直调真源库。
  Part B  trigger-droid.sh 集成（沙盒全链路：账本命中→零 droid exec；未命中→
          记录落账本后放行；账本不可用→fail-open；演化卡零账本副作用）。
  Part C  reconcile GAP-A 补偿路径（沙盒：补偿创建→账本记录含 linear_uuid+GH 号；
          幂等 tick 不重补偿=账本非重放载体但记录稳定；账本不可用仅 WARN 降级）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.ledger_check_helpers import (
    LEDGER_LIB,
    make_base,
    read_events,
    read_record,
    run_ledger_fn,
)
from tests.trigger_sandbox_helpers import (
    darwin_skip_reason,
    make_trigger_sandbox,
    run_trigger_sandbox,
    trigger_sandbox_log,
    wait_droid_marker,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_DIR = REPO_ROOT / "webhook-scripts"
TRIGGER_SCRIPT = WEBHOOK_DIR / "trigger-droid.sh"
RECONCILE_SCRIPT = WEBHOOK_DIR / "reconcile-evolution.sh"
MANIFEST_PATH = WEBHOOK_DIR / "MANIFEST.sh"

UUID_A = "11111111-aaaa-bbbb-cccc-00000000000a"
UUID_B = "22222222-aaaa-bbbb-cccc-00000000000b"
UUID_EVIL = "../../evil-uuid"


def _requires_darwin(func):
    return pytest.mark.skipif(darwin_skip_reason() is not None, reason=darwin_skip_reason() or "")(
        func
    )


# ============================================================================
# Part A — 账本库纯函数契约
# ============================================================================


class TestLedgerClaimContract:
    """ledger_claim rc 契约：0=首条创建 / 1=记录已存在 / 2=不可用。

    claim 是「事实登记」不是「派发裁决」——去重判定走 ledger_dispatch_done
    （来源=trigger-droid 且 status completed+exit=0 才命中），claim 本身对
    重试/补偿记录永不构成拦截。
    """

    def test_first_claim_creates_record_rc0(self, tmp_path):
        base = make_base(tmp_path)
        r = run_ledger_fn(
            f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001 "T" INFRA linear-gateway 12345; echo "rc=$?"',
            base,
        )
        assert "rc=0" in r.stdout
        rec = read_record(base, UUID_A)
        assert rec is not None
        assert rec["linearUuid"] == UUID_A
        assert rec["linearRef"] == "INFRA-1001"
        assert rec["kind"] == "linear-gateway"
        assert rec["source"] == "trigger-droid"

    def test_second_claim_is_record_exists_rc1_record_unchanged(self, tmp_path):
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        before = read_record(base, UUID_A)
        r = run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout, r.stdout
        assert read_record(base, UUID_A) == before, "append-only：重复 claim 不得改写记录"

    def test_claim_creates_no_record_on_rc2_empty_uuid(self, tmp_path):
        base = make_base(tmp_path)
        r = run_ledger_fn('ledger_claim "" trigger-droid INFRA-1; echo "rc=$?"', base)
        assert "rc=2" in r.stdout
        assert not (base / "status" / "ledger" / ".json").exists()

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root 不受 0o500 写权限约束，权限拒绝语义不成立",
    )
    def test_rc2_when_base_unwritable(self, tmp_path):
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        try:
            r = run_ledger_fn(
                f'ledger_claim "{UUID_A}" trigger-droid INFRA-1; echo "rc=$?"', blocked
            )
            assert "rc=2" in r.stdout, r.stdout
        finally:
            os.chmod(blocked, 0o755)

    def test_uuid_path_traversal_is_sanitized(self, tmp_path):
        """UUID 键折叠非 [A-Za-z0-9._-]：路径穿越形态落不出 status/ledger 之外。"""
        base = make_base(tmp_path)
        r = run_ledger_fn(f'ledger_claim "{UUID_EVIL}" trigger-droid INFRA-1; echo "rc=$?"', base)
        assert "rc=0" in r.stdout or "rc=1" in r.stdout
        escaped = base.parent / "evil-uuid.json"
        assert not escaped.exists(), "路径穿越键不得逃出账本目录"
        assert not (base.parent / "status" / "evil-uuid.json").exists()

    def test_one_card_one_record_across_sources(self, tmp_path):
        """同一卡二次来源（trigger-droid 后 gap-a 重放）仍 rc=1：一卡一记录全来源通吃。"""
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        r = run_ledger_fn(f'ledger_claim "{UUID_A}" reconcile-gap-a INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout
        assert read_record(base, UUID_A)["source"] == "trigger-droid"


class TestLedgerDispatchDoneSemantics:
    """ledger_dispatch_done：去重=「本通道成功完成的派发」，非「存在记录」。"""

    def test_no_record_rc1(self, tmp_path):
        base = make_base(tmp_path)
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout

    def test_gap_a_record_never_dedup_hit(self, tmp_path):
        """补偿记录（source=reconcile-gap-a）永不构成去重命中——否则补偿卡被拦截。"""
        base = make_base(tmp_path)
        run_ledger_fn(
            f'ledger_claim "{UUID_A}" reconcile-gap-a INFRA-1001 T INFRA gap-a-compensation 555',
            base,
        )
        status = base / "status" / "INFRA-1001.json"
        status.write_text(json.dumps({"status": "completed", "exitCode": 0}))
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout, r.stdout

    def test_trigger_record_without_completed_status_rc1(self, tmp_path):
        """trigger 记录但 status 未 completed（running/failed/缺失）→ 放行（重试合法）。"""
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        # running（在跑）
        (base / "status" / "INFRA-1001.json").write_text(json.dumps({"status": "running"}))
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout
        # failed（失败重试）
        (base / "status" / "INFRA-1001.json").write_text(
            json.dumps({"status": "failed", "exitCode": 1})
        )
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout
        # status 缺失（pre-mechanism/归档）
        (base / "status" / "INFRA-1001.json").unlink()
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout

    def test_trigger_record_with_completed_status_dedup_hit(self, tmp_path):
        """trigger 记录 + completed + exitCode=0 → rc=0 去重命中（重放拦截场景）。"""
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        (base / "status" / "INFRA-1001.json").write_text(
            json.dumps({"status": "completed", "exitCode": 0})
        )
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=0" in r.stdout, r.stdout

    def test_completed_with_nonzero_exit_rc1(self, tmp_path):
        """completed 但 exitCode!=0 → 非成功派发 → 放行（重试合法）。"""
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        (base / "status" / "INFRA-1001.json").write_text(
            json.dumps({"status": "completed", "exitCode": 3})
        )
        r = run_ledger_fn(f'ledger_dispatch_done "{UUID_A}" INFRA-1001; echo "rc=$?"', base)
        assert "rc=1" in r.stdout


class TestLedgerAppendOnlyAndViews:
    """append-only 语义 + by-ref 视图维护 + 事件流。"""

    def test_by_ref_view_created_and_points_to_record(self, tmp_path):
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        link = base / "status" / "ledger" / "by-ref" / "INFRA-1001.json"
        assert link.is_symlink()
        assert os.readlink(link) == str(base / "status" / "ledger" / f"{UUID_A}.json")

    def test_team_move_ref_change_updates_view_but_not_record(self, tmp_path):
        """跨团队变号：record 永不改写（UUID 键不变），by-ref 视图重链 + ref_moved 事件。"""
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        before = read_record(base, UUID_A)
        r = run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-2002; echo "rc=$?"', base)
        assert "rc=1" in r.stdout
        assert read_record(base, UUID_A) == before, "变号不得改写 record 本体"
        link = base / "status" / "ledger" / "by-ref" / "INFRA-2002.json"
        assert link.is_symlink(), "变号后 by-ref 视图必须重链到新 ref"
        events = read_events(base)
        moved = [e for e in events if e["event"] == "ref_moved"]
        assert moved and moved[0]["detail"] == "previous=INFRA-1001"

    def test_events_jsonl_appends_claim_events(self, tmp_path):
        base = make_base(tmp_path)
        run_ledger_fn(
            f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001 T INFRA linear-gateway 12345', base
        )
        run_ledger_fn(
            f'ledger_append "{UUID_A}" INFRA-1001 dispatch_attempted "action=create"', base
        )
        events = read_events(base)
        assert [e["event"] for e in events] == ["dispatch_attempted"]
        assert events[0]["uuid"] == UUID_A
        assert events[0]["ref"] == "INFRA-1001"
        assert events[0]["detail"] == "action=create"
        assert re.match(r"\d{4}-\d{2}-\d{2}T", events[0]["ts"])

    def test_compensation_event_name(self, tmp_path):
        """GAP-A 事件名=compensation_created（与 trigger 通道区分，影子周对账按事件分流）。"""
        base = make_base(tmp_path)
        run_ledger_fn(
            f'ledger_claim "{UUID_A}" reconcile-gap-a INFRA-1001 T INFRA gap-a-compensation 555',
            base,
        )
        run_ledger_fn(
            f'ledger_append "{UUID_A}" INFRA-1001 compensation_created "ghIssue=555"', base
        )
        events = read_events(base)
        assert [e["event"] for e in events] == ["compensation_created"]
        assert "ghIssue=555" in events[0]["detail"]

    def test_ledger_get_ref_of_helpers(self, tmp_path):
        base = make_base(tmp_path)
        run_ledger_fn(f'ledger_claim "{UUID_A}" trigger-droid INFRA-1001', base)
        r = run_ledger_fn(f'printf "[%s]" "$(ledger_ref_of "{UUID_A}")"; echo " rc=$?"', base)
        assert "[INFRA-1001] rc=0" in r.stdout, r.stdout
        r2 = run_ledger_fn(f'OUT=$(ledger_ref_of "{UUID_B}"); echo "got=[$OUT]rc=$?"', base)
        assert "got=[]rc=1" in r2.stdout, r2.stdout

    def test_gh_issue_persisted_as_int(self, tmp_path):
        base = make_base(tmp_path)
        run_ledger_fn(
            f'ledger_claim "{UUID_A}" reconcile-gap-a INFRA-1001 T INFRA gap-a-compensation 555',
            base,
        )
        rec = read_record(base, UUID_A)
        assert rec["ghIssue"] == 555
        assert rec["kind"] == "gap-a-compensation"


# ============================================================================
# Part B — trigger-droid.sh 集成（沙盒全链路）
# ============================================================================


def _seed_completed_dispatch(sandbox: dict, ref: str, uuid: str) -> None:
    """预置「已成功完成的派发」账本态：record + completed status 文件（重放拦截前提）。"""
    base = sandbox["sandbox"]
    ledger_dir = base / "status" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / f"{uuid}.json").write_text(
        json.dumps(
            {
                "version": 1,
                "schema": "dispatch-ledger/1",
                "linearUuid": uuid,
                "linearRef": ref,
                "kind": "linear-gateway",
                "source": "trigger-droid",
                "ghIssue": None,
                "title": "normal dispatch title",
                "team": "INFRA",
                "claimedAt": "2026-01-01T00:00:00Z",
            }
        )
    )
    (base / "status" / f"{ref}.json").write_text(json.dumps({"status": "completed", "exitCode": 0}))


@pytest.fixture()
def trigger_sandbox(tmp_path):
    return make_trigger_sandbox(tmp_path)


class TestTriggerDroidLedgerDedup:
    """trigger-droid 启动先查账本去重（droid exec 前拦截，仅命中「成功完成的派发」）。"""

    @pytest.fixture()
    def dispatched_once(self, trigger_sandbox):
        r = run_trigger_sandbox(trigger_sandbox)
        wait_droid_marker(trigger_sandbox)
        return trigger_sandbox, r

    @_requires_darwin
    def test_first_dispatch_creates_ledger_record(self, dispatched_once):
        sandbox, r = dispatched_once
        log = trigger_sandbox_log(sandbox, r)
        assert sandbox["marker"].exists(), f"首派发必须放行到 droid exec: {log[-1500:]}"
        rec = read_record(sandbox["sandbox"], UUID_A)
        assert rec is not None, "放行的派发必须落账本记录"
        assert rec["source"] == "trigger-droid"
        assert rec["linearRef"] == "INFRA-3301"
        assert "dispatch_attempted" in [e["event"] for e in read_events(sandbox["sandbox"])]

    def test_redispatch_after_completed_dispatch_blocked(self, trigger_sandbox):
        """重投递（completed 派发后 flock 已释放）：账本命中 → 零 droid exec + 告警事件。"""
        _seed_completed_dispatch(trigger_sandbox, "INFRA-3301", UUID_A)
        r = run_trigger_sandbox(trigger_sandbox)
        log = trigger_sandbox_log(trigger_sandbox, r)
        assert "SKIP: dispatch ledger hit" in log, log
        assert not trigger_sandbox["marker"].exists(), "账本命中必须零 droid exec"
        assert "ledger_dedup_hit" in log, "去重命中须发 PostHog 告警事件"

    def test_redispatch_after_failed_dispatch_not_blocked(self, trigger_sandbox):
        """失败后的重触发（reconcile retrigger 面）：账本记录在但 status=failed → 放行。"""
        _seed_completed_dispatch(trigger_sandbox, "INFRA-3301", UUID_A)
        (trigger_sandbox["sandbox"] / "status" / "INFRA-3301.json").write_text(
            json.dumps({"status": "failed", "exitCode": 1})
        )
        r = run_trigger_sandbox(trigger_sandbox)
        log = trigger_sandbox_log(trigger_sandbox, r)
        assert "SKIP: dispatch ledger hit" not in log, "失败派发不得被账本拦截"
        assert "dispatch ledger hit" not in log
        assert read_record(trigger_sandbox["sandbox"], UUID_A)["source"] == "trigger-droid"

    def test_ledger_record_without_status_not_blocked(self, trigger_sandbox):
        """有账本记录但 status 缺失/归档 → 放行（去重面只认可判定的成功终态）。"""
        _seed_completed_dispatch(trigger_sandbox, "INFRA-3301", UUID_A)
        (trigger_sandbox["sandbox"] / "status" / "INFRA-3301.json").unlink()
        r = run_trigger_sandbox(trigger_sandbox)
        log = trigger_sandbox_log(trigger_sandbox, r)
        assert "SKIP: dispatch ledger hit" not in log

    @_requires_darwin
    def test_ledger_unavailable_fail_open(self, tmp_path):
        """账本目录不可建 → rc=2 → fail-open 继续派发（去重面降级，非闸门）。"""
        sandbox = make_trigger_sandbox(tmp_path)
        # 把 status 变成普通文件：mkdir -p status/ledger 必败 → ledger_init rc=1 → claim rc=2
        (sandbox["sandbox"] / "status").rmdir()
        (sandbox["sandbox"] / "status").write_text("not-a-dir")
        r = run_trigger_sandbox(sandbox)
        log = trigger_sandbox_log(sandbox, r)
        assert "WARN: dispatch ledger unavailable" in log, log
        assert wait_droid_marker(sandbox), "fail-open：账本不可用不得瘫痪派发链路"
        assert "ledger_unavailable" in log

    @_requires_darwin
    def test_ledger_lib_missing_degrades_dispatch(self, tmp_path):
        """lib 缺失（部署漂移）→ source 失败兜底分支 → fail-open 派发不瘫痪。"""
        sandbox = make_trigger_sandbox(tmp_path)
        (sandbox["sandbox"] / "scripts" / "lib" / "dispatch-ledger.sh").unlink()
        r = run_trigger_sandbox(sandbox)
        log = trigger_sandbox_log(sandbox, r)
        assert "dispatch ledger lib unavailable" in log, log
        assert wait_droid_marker(sandbox), "lib 缺失不得瘫痪派发链路（fail-open）"

    def test_evolution_card_never_writes_ledger(self, tmp_path):
        """演化卡在守卫处 REJECTED：账本零副作用（record 是本机执行过的持久凭证）。"""
        sandbox = make_trigger_sandbox(tmp_path)
        r = run_trigger_sandbox(
            sandbox,
            extra_env={"LINEAR_LABELS": json.dumps(["evolution-found"])},
        )
        log = trigger_sandbox_log(sandbox, r)
        assert "REJECTED: evolution card reached Mac" in log
        ledger_dir = sandbox["sandbox"] / "status" / "ledger"
        assert not ledger_dir.exists() or not any(ledger_dir.iterdir()), (
            "演化卡 REJECTED 路径不得写账本"
        )
        assert not sandbox["marker"].exists()

    def test_resume_path_not_ledger_claimed(self, tmp_path):
        """resume（人工恢复入口）不是 create 派发：不写账本（账本=自动派发事实源）。"""
        sandbox = make_trigger_sandbox(tmp_path)
        env = os.environ.copy()
        env.update(
            {
                "WEBHOOK_BASE": str(sandbox["sandbox"]),
                "REPO_CONFIG": str(sandbox["tmp"] / "repositories.yml"),
                "PATH": f"{sandbox['tmp'] / 'bin'}:{env.get('PATH', '')}",
                "POSTHOG_DRY_RUN": "1",
                "LINEAR_API_KEY": "",
                "RESUME_SESSION_ID": "sess-manual-1",
            }
        )
        env.pop("LINEAR_LABELS", None)
        subprocess.run(
            [
                "bash",
                str(sandbox["sandbox"] / "scripts" / "trigger-droid.sh"),
                "resume",
                "Issue",
                "INFRA-3301",
                UUID_A,
                "INFRA",
                "normal dispatch title",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(sandbox["tmp"]),
        )
        ledger_dir = sandbox["sandbox"] / "status" / "ledger"
        assert not ledger_dir.exists() or not any(
            f for f in ledger_dir.iterdir() if f.name.endswith(".json")
        ), "resume 人工入口不写账本"


# ============================================================================
# Part C — reconcile GAP-A 补偿路径写账本（沙盒）
# ============================================================================

SITECUSTOMIZE = """# 沙盒 Linear GraphQL stub（urllib 劫持；真网络到达即失败）
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


def _fake_urlopen(req, timeout=None):
    try:
        payload = _json.loads(req.data.decode())
    except Exception:
        _log("UNPARSED")
        return _FakeResp({"data": {}})
    q = payload.get("query", "")
    if "issueCreate" in q:
        rows = _created()
        ident = "INFRA-%d" % (800021 + len(rows))
        node = {"id": "uuid-comp-%d" % len(rows), "identifier": ident,
                "url": "https://linear.app/infra/issue/" + ident}
        with open(_os.path.join(_SANDBOX, "linear-created.jsonl"), "a") as f:
            f.write(_json.dumps(node) + "\\n")
        _log("issueCreate:" + ident)
        return _FakeResp({"data": {"issueCreate": {"issue": node}}})
    if "commentCreate" in q:
        _log("commentCreate")
        return _FakeResp({"data": {"commentCreate": {"comment": {"id": "c1"}}}})
    if "labels(filter:" in q:
        return _FakeResp({"data": {"team": {"labels": {"nodes": [
            {"id": "LID1", "name": "evolution-found"}]}}}})
    if "issues(" in q:
        # 主查询（非终态）+ 终态查询：空集（focus=补偿路径）
        _log("issuesQuery")
        return _FakeResp({"data": {"issues": {"nodes": []}}})
    _log("UNMATCHED:" + q[:60].replace("\\n", " "))
    return _FakeResp({"data": {}})


if _SANDBOX:
    _ur.urlopen = _fake_urlopen
"""

GH_STUB = """#!/bin/bash
: "${SANDBOX:=/tmp/ledger-stub}"
mkdir -p "${SANDBOX}"
echo "gh $*" >> "${SANDBOX}/gh.log"
# open evolution-found 清单：恒返回一张超龄无锚点镜像（触发 GAP-A 补偿）
if [[ "$*" == *"issue list"* ]]; then
  if [[ "$*" == *"--search"* ]]; then
    echo "[]"
  else
    echo '[{"number": 700001, "title": "[evolution] ledger mirror", "createdAt": "0001-01-01T00:00:00Z"}]'
  fi
  exit 0
fi
if [[ "$*" == *"issue comment"* ]]; then
  mkdir -p "${SANDBOX}/comments"
  echo "comment:700001" >> "${SANDBOX}/gh.log"
  exit 0
fi
exit 0
"""

EXTRACT_ANCHOR_STUB = """#!/usr/bin/env python3
# 沙盒锚点助手：镜像恒无锚点（孤儿判定直落补偿路径）
"""


def _build_reconcile_sandbox(tmp_path: Path) -> Path:
    sandbox = tmp_path / "rsandbox"
    sandbox.mkdir()
    for sub in ("status", "logs", "locks", "bin", "lib", "pylib", "comments"):
        (sandbox / sub).mkdir()
    content = RECONCILE_SCRIPT.read_text()
    content = content.replace("/opt/homebrew/bin/python3", "python3")
    content = content.replace(
        'WEBHOOK_BASE="${HOME}/.factory/webhook"',
        f'WEBHOOK_BASE="{sandbox}"\nexport WEBHOOK_BASE',
    )
    content = content.replace(
        'SCRIPT_DIR="${HOME}/.factory/webhook/scripts"', f'SCRIPT_DIR="{sandbox}"'
    )
    content = content.replace('SCRIPT_DIR="${WEBHOOK_BASE}/scripts"', f'SCRIPT_DIR="{sandbox}"')
    # TEAM_ID 解析块整段替换（读 ~/.factory/config/repositories.yml 的真实 I/O 面 → stub）
    content = re.sub(
        r'TEAM_ID=\$\(TEAM_KEY="\$TEAM_KEY" python3 -c ".*?" 2>/dev/null \|\| echo ""\)',
        'TEAM_ID="fake-team-id"',
        content,
        flags=re.DOTALL,
    )
    script = sandbox / "reconcile-evolution.sh"
    script.write_text(content)
    script.chmod(0o755)
    (sandbox / "pylib" / "sitecustomize.py").write_text(SITECUSTOMIZE)
    (sandbox / "lib" / "op-mcp.sh").write_text(
        'OP_VAULT_SEVER="sever"\nop_get_field() { echo "fake-key"; }\n'
    )
    (sandbox / "lib" / "posthog.sh").write_text("#!/bin/bash\nsend_posthog_event() { :; }\n")
    (sandbox / "lib" / "dispatch-ledger.sh").write_text(LEDGER_LIB.read_text())
    (sandbox / "extract_anchor.py").write_text(EXTRACT_ANCHOR_STUB)
    (sandbox / "extract_anchor.py").chmod(0o755)
    gh = sandbox / "bin" / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    trig = sandbox / "trigger-droid.sh"
    trig.write_text('#!/bin/bash\necho "trigger:$*" >> "${WEBHOOK_BASE}/trigger.log"\n')
    trig.chmod(0o755)
    return sandbox


def _run_reconcile_tick(
    sandbox: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
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


class TestReconcileGapALedgerWrite:
    """GAP-A 补偿创建 → 账本记录（linear_uuid + 源 GH issue 号）。"""

    def test_compensation_writes_ledger_with_uuid_and_gh_number(self, tmp_path):
        sandbox = _build_reconcile_sandbox(tmp_path)
        r = _run_reconcile_tick(sandbox)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        logs = sorted((sandbox / "logs").glob("reconcile-*.log"))
        assert logs
        log = logs[-1].read_text()
        assert "orphan detected" in log
        assert "created Linear Issue INFRA-800021" in log, log

        created = [
            json.loads(line) for line in (sandbox / "linear-created.jsonl").read_text().splitlines()
        ]
        assert created, "补偿创建必须发生"
        comp_uuid = created[0]["id"]
        rec = read_record(sandbox, comp_uuid)
        assert rec is not None, "补偿创建必须写账本（VAL-LEDGER-002）"
        assert rec["linearUuid"] == comp_uuid
        assert rec["source"] == "reconcile-gap-a"
        assert rec["ghIssue"] == 700001, "账本记录必须带源 GH issue 号"
        assert rec["linearRef"] == "INFRA-800021"
        assert rec["kind"] == "gap-a-compensation"
        assert "LEDGER: dispatch record created" in log

    def test_ledger_failure_degrades_with_warn_only(self, tmp_path):
        """账本不可用 → 补偿照常完成 + WARN（账本是审计面非补偿闸门）。"""
        sandbox = _build_reconcile_sandbox(tmp_path)
        # status 变普通文件 → 账本目录不可建 → claim rc=2
        (sandbox / "status").rmdir()
        (sandbox / "status").write_text("not-a-dir")
        r = _run_reconcile_tick(sandbox)
        assert r.returncode == 0, r.stderr[-2000:]
        logs = sorted((sandbox / "logs").glob("reconcile-*.log"))
        log = logs[-1].read_text()
        assert "created Linear Issue INFRA-800021" in log, "补偿不得因账本失败回滚"
        assert "dispatch ledger unavailable" in log, "rc=2 必须 WARN 降级"


# ============================================================================
# Part D — 结构不变量
# ============================================================================


class TestStructuralInvariants:
    """库入 MANIFEST 受管 + 集成点位置锚定。"""

    def test_lib_registered_in_manifest(self):
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert '"lib/dispatch-ledger.sh"' in manifest, (
            "账本库必须入 MANIFEST.sh MANAGED_LIB_FILES（随 sync 部署生产）"
        )

    def test_ledger_dedup_precedes_lock_acquisition(self):
        """trigger-droid：账本去重必须在锁获取之前（先查账本→才谈锁）。"""
        text = TRIGGER_SCRIPT.read_text(encoding="utf-8")
        ledger_pos = text.find("dispatch ledger hit")
        lock_pos = text.find('LOCK_FILE="${LOCK_DIR}/l2d-${ISSUE_REF}.lock"')
        assert ledger_pos != -1 and lock_pos != -1
        assert ledger_pos < lock_pos, "账本去重必须先于 per-issue 锁（启动先查账本）"

    def test_ledger_claim_after_evolution_guard(self):
        """trigger-droid：账本 claim 在演化守卫之后（演化卡零副作用不变量）。"""
        text = TRIGGER_SCRIPT.read_text(encoding="utf-8")
        guard_pos = text.find("evolution_reject_or_pass\n")
        ledger_pos = text.find("ledger_claim")
        assert guard_pos != -1 and ledger_pos != -1
        assert guard_pos < ledger_pos

    def test_trigger_fail_open_wording_present(self):
        text = TRIGGER_SCRIPT.read_text(encoding="utf-8")
        assert "fail-open" in text, "降级语义必须显式（账本不可用继续派发）"

    def test_trigger_dedup_uses_dispatch_done_not_claim(self):
        """去重判定必须走 ledger_dispatch_done（来源+终态语义），不得用 claim rc=1
        （否则失败重试与补偿卡被拦截）。"""
        text = TRIGGER_SCRIPT.read_text(encoding="utf-8")
        assert "ledger_dispatch_done" in text, "去重判定入口必须是 ledger_dispatch_done"
        assert text.count("ledger_dispatch_done") >= 1

    def test_reconcile_ledger_write_uses_claim(self):
        text = RECONCILE_SCRIPT.read_text(encoding="utf-8")
        assert "ledger_claim" in text
        assert "reconcile-gap-a" in text
        assert "compensation_created" in text, "补偿事件名必须区分 trigger 通道"

    def test_ledger_lib_bash_syntax(self):
        r = subprocess.run(["bash", "-n", str(LEDGER_LIB)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def test_ledger_lib_shellcheck_clean(self):
        # 池内 runner 异构（muse1 无 shellcheck）：对齐 shellcheck_helpers 降级口径
        if not shutil.which("shellcheck"):
            pytest.skip("shellcheck not installed")
        r = subprocess.run(
            [
                "shellcheck",
                "--severity=error",
                "--exclude=SC1090,SC1091,SC2016,SC2154",
                str(LEDGER_LIB),
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout
