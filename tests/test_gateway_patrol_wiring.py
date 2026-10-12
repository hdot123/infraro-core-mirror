#!/usr/bin/env python3
"""gateway-patrol 入仓接线 + 评审缺陷修复契约（VAL-BUD-003，a′ 收门）。

入仓版 gateway-patrol.sh 派发点接线（architecture §3.3，对照 mac-members 模式）：
- dispatch 分支内、droid exec 之前守卫式 source lib/dispatch-ledger.sh
- ledger_budget_check（告警不拦，rc 恒 0；超限/不可达只 WARN+遥测）
- ledger_claim + ledger_append 记账（uuid=uuid4、kind=gateway-patrol）

评审线程修复（PR #303 六条，用户 2026-10-10 裁定「修复后过门」）：
- P1 阈值评估复活：生产副本固有 printf | python3 - <<'PY' 形态被 heredoc 覆盖
  fd 0（SC2259），sys.stdin.read() 恒空 → 真实告警（5xx/磁盘/证书/容器…）
  永不派发。入仓版改单进程 argv+管道混合形态，探测 JSON 真正到达评估块；
  沙盒用例锁定 ≥2 类越线形态触发派发、未越线静默（见
  TestThresholdDispatchBehavior；生产副本 diff 等价断言随修复裁定退役——
  真源自本仓起经 sync 部署生产）
- P2 决策 case 补 default 兜底：空/未知 ACTION 不再静默 exit 0，落
  decision_error 留痕
- P3 回退重试后重算 IS_ERR（不再持久化主模型旧值）
- P3 运行锁 mtime 探测双分支（stat -f %m / stat -c %Y，GNU/Linux 兼容）

预算三态断言（observe-first 裁定，任何拒发返回码都违规）：
- 窗口内正常 → 无 budget_over_limit/budget_unavailable，派发照常 + 记账落盘
- 超预算 → budget_over_limit WARN + 派发照常（不拒发）
- 基建不可达（status 变普通文件）→ budget_unavailable WARN + 派发照常 =
  fail-open 实证；lib 缺失（sync 漂移窗口）→ 只禁用计量记账，派发照常

沙盒模式（对齐 test_mac_members_wiring.py）：临时 HOME（脚本 BASE 自 $HOME 派生，
非 WEBHOOK_BASE）+ stub probe/droid bin（marker 观测 droid exec 到达面）+
POSTHOG_DRY_RUN=1 隔离 + GATEWAY_PATROL_FORCE=1 确定性进入 dispatch 分支 +
LC_ALL=C 归一（macOS bash 3.2 zh_CN ctype 陷阱，宿主脚本零改动），零生产接触
（禁写生产 ~/.factory/webhook/status/ledger/）。行为沙箱跑真源脚本全链路，
macOS 专属路径与既有套件同口径门控。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.ledger_check_helpers import read_events, read_record, run_ledger_fn
from tests.trigger_sandbox_helpers import darwin_skip_reason, non_comment_lines, write_stub

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_DIR = REPO_ROOT / "webhook-scripts"
MANIFEST_PATH = WEBHOOK_DIR / "MANIFEST.sh"
PATROL_SCRIPT = WEBHOOK_DIR / "gateway-patrol.sh"
PROD_PATROL = Path.home() / ".factory" / "webhook" / "scripts" / "gateway-patrol.sh"

BUDGET_SOURCE = "gateway-patrol"
BUDGET_KIND = "gateway-patrol"

# 阈值评估形态锚点（评审 P1 修复的结构不变量）：单进程 argv/管道混合形态，
# heredoc 形态禁止复活（复活即 SC2259 死告警路径回归）。决策链分支锚点同时
# 锁定（P2 default 兜底存在性）。
THRESHOLD_EVAL_ANCHORS = [
    "ALARM=$(printf '%s' \"$PROBE\" | PATROL_EVAL_MODE=real python3 -c '",
]
FORBIDDEN_HEREDOC_EVAL = "python3 - <<'PY'"
DECISION_BRANCH_ANCHORS = [
    "  none)",
    "  suppress|throttle|rate_limited)",
    "  dispatch)",
    "  *)",
]


def _requires_darwin(func):
    return pytest.mark.skipif(darwin_skip_reason() is not None, reason=darwin_skip_reason() or "")(
        func
    )


# ============================================================================
# 沙盒原语
# ============================================================================


def _sandbox_home(tmp_path: Path):
    """临时 HOME（gateway-patrol.sh 的 BASE 从 $HOME 派生，WEBHOOK_BASE 沙盒
    不足以隔离它）。返回 (home, webhook_base, scripts_dir)。"""
    home = tmp_path / "home"
    base = home / ".factory" / "webhook"
    scripts = base / "scripts"
    scripts.mkdir(parents=True)
    return home, base, scripts


def _install_probe_stub(scripts: Path, exit_code: int = 0) -> None:
    write_stub(
        scripts / "gateway-patrol-probe.sh",
        "#!/bin/bash\n"
        'echo \'{"containers":{"kong":"Up 2 hours","kong-database":"Up 2 hours"},'
        '"http_5xx_30m":0,"error_lines_30m":0,"disk_root_pct":42,"cert_days_left":90,'
        '"smoke":{"ok":true},"upstreams":{}}\'\n'
        f"exit {exit_code}\n",
    )


def _stub_droid_bin(tmp_path: Path, marker: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_stub(
        bin_dir / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho \'{{"is_error": false}}\'\nexit 0\n',
    )
    return bin_dir


def _sandbox_env(
    tmp_path: Path,
    home: Path,
    base: Path,
    bin_dir: Path,
    extra: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["WEBHOOK_BASE"] = str(base)
    env["DROID_BIN"] = str(bin_dir / "droid")
    env["PYTHON_BIN"] = sys.executable
    env["POSTHOG_DRY_RUN"] = "1"
    # macOS bash 3.2 在 zh_CN.UTF-8 ctype 下解析 CJK 邻接变量会吞多字节首字节
    # （宿主既有脚本固有特性，与接线无关）。沙盒钉 LC_ALL=C 归一化字节语义；
    # 生产 launchd 环境本就无该 locale 注入，保持对宿主脚本零改动。
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    if extra:
        env.update(extra)
    env["_SANDBOX_CWD"] = str(tmp_path)
    return env


def _run_patrol(env: dict, timeout: int = 90):
    return subprocess.run(
        ["bash", str(PATROL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=env["_SANDBOX_CWD"],
    )


def _combined(base: Path, result) -> str:
    parts = [result.stdout, result.stderr]
    logs = base / "logs"
    if logs.exists():
        for p in sorted(logs.rglob("*.log")):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def _dispatched_line(base: Path) -> str:
    day_logs = sorted((base / "logs" / "gateway-patrol").glob("patrol-*.jsonl"))
    assert day_logs, "day log 缺失：脚本未走到记录面"
    return "\n".join(
        line
        for line in day_logs[-1].read_text(encoding="utf-8").splitlines()
        if '"dispatched"' in line
    )


def _wired_dispatch_records(base: Path) -> list[dict]:
    return [
        rec
        for rec in (
            read_record(base, e["uuid"])
            for e in read_events(base)
            if e["event"] == "dispatch_attempted"
        )
        if rec
    ]


# ============================================================================
# 结构不变量（全平台执行）
# ============================================================================


class TestOnboardStructuralInvariants:
    def test_gateway_patrol_registered_in_manifest(self):
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        assert '"gateway-patrol.sh"' in manifest, "必须在 MANIFEST.sh 受管（随 sync 部署）"

    def test_sources_ledger_lib_defensively(self):
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        assert "lib/dispatch-ledger.sh" in text, "缺 source lib"
        assert re.search(r"if \[ -f \"\$_LEDGER_LIB\" \]; then", text), (
            "source 必须有文件存在性守卫（lib 缺失 fail-open，禁绝派发即违规）"
        )

    def test_budget_source_unique_and_exact(self):
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        calls = re.findall(r'ledger_budget_check\s+"([^"]+)"', text)
        assert calls == [BUDGET_SOURCE], f"budget source 必须恰为 {BUDGET_SOURCE}，got {calls}"
        assert 'ledger_budget_check "gateway-patrol" "gateway-patrol"' in text

    def test_claims_and_appends_inside_dispatch_branch(self):
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        assert "ledger_claim" in text, "缺 ledger_claim 记账"
        assert "dispatch_attempted" in text, "缺 dispatch_attempted 留痕"
        # 接线只允许出现在 dispatch 分支内（告警抑制/节流路径零账本副作用）
        dispatch_pos = text.find("  dispatch)")
        wiring_pos = text.find("_LEDGER_LIB=")
        exec_pos = text.find('"$DROID_BIN" exec')
        assert 0 < dispatch_pos < wiring_pos < exec_pos, (
            "接线必须落在 dispatch 分支内、droid exec 之前"
        )

    def test_no_budget_call_on_suppressed_paths(self):
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        none_pos = text.find("  none)")
        suppress_pos = text.find("  suppress|throttle|rate_limited)")
        dispatch_pos = text.find("  dispatch)")
        # 评审线程 P3（测试质量）：切片前断言锚点全部找到——find() 未命中返回
        # -1 时 text[-1:x] 得空切片，守卫将空洞通过（vacuous pass）。
        assert none_pos >= 0, "none) 分支锚点缺失"
        assert suppress_pos >= 0, "suppress|throttle|rate_limited) 分支锚点缺失"
        assert dispatch_pos >= 0, "dispatch) 分支锚点缺失"
        for label, pos in (("none", none_pos), ("suppress", suppress_pos)):
            segment = text[pos : dispatch_pos if label == "suppress" else suppress_pos]
            assert "ledger_" not in segment, f"{label} 路径不得有账本调用"

    def test_decision_case_has_default_fallback(self):
        """评审线程 P2：决策 case 必须有 default 兜底（空/未知 ACTION 落
        decision_error 留痕，禁止静默 exit 0）。"""
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        case_pos = text.find('case "$ACTION" in')
        assert case_pos >= 0, "决策 case 块缺失"
        esac_pos = text.find("\nesac", case_pos)
        assert esac_pos >= 0, "esac 缺失"
        for anchor in DECISION_BRANCH_ANCHORS:
            assert text.count(anchor) == 1, f"决策分支锚点异常: {anchor!r}"
        default_pos = text.find("\n  *)\n", case_pos)
        assert 0 < default_pos < esac_pos, "default *) 兜底分支缺失"
        default_body = text[default_pos:esac_pos]
        assert "decision_error" in default_body, "default 分支必须落 decision_error 留痕"

    def test_is_err_recomputed_after_fallback(self):
        """评审线程 P3：回退重试后必须重算 IS_ERR（持久化主模型旧值 = 状态
        面污染）。结构断言：fallback exec 之后、状态持久化之前有第二次求值。"""
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        fallback_exec = text.find('exec --auto high -m "$FALLBACK_MODEL"')
        assert fallback_exec >= 0, "fallback exec 调用缺失"
        second_eval = text.find("IS_ERR=$(python3 -c", fallback_exec)
        state_update = text.find('python3 - "$STATE_FILE"', fallback_exec)
        assert second_eval >= 0, "回退后未重算 IS_ERR"
        assert 0 < state_update, "状态持久化调用缺失"
        assert second_eval < state_update, "IS_ERR 重算必须先于状态持久化"

    def test_stat_mtime_probe_is_portable(self):
        """评审线程 P3：运行锁 mtime 探测必须双分支（stat -f %m 为 macOS
        专属；GNU/Linux 上多词输出 + set -u 组合会崩脚本）。"""
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        assert "stat -f %m" in text, "macOS 分支缺失"
        assert "stat -c %Y" in text, "GNU/Linux 分支缺失"

    def test_threshold_eval_single_process_form(self):
        """评审线程 P1 结构锁：阈值评估必须是单进程形态（管道到 python3 -c），
        heredoc 形态禁止复活（复活即 SC2259 死告警路径回归）。形态扫描只看
        非注释行（脚本注释里的形态引述不构成复活）。"""
        text = PATROL_SCRIPT.read_text(encoding="utf-8")
        code = "\n".join(line for _, line in non_comment_lines(PATROL_SCRIPT))
        assert code, "脚本非注释行枚举为空（锚点失效）"
        for anchor in THRESHOLD_EVAL_ANCHORS:
            assert anchor in text, f"阈值评估单进程形态锚点缺失: {anchor!r}"
        assert FORBIDDEN_HEREDOC_EVAL not in code, (
            f"heredoc 评估形态禁止复活（SC2259: fd 0 被 heredoc 覆盖，告警路径死亡）: {FORBIDDEN_HEREDOC_EVAL!r}"
        )


# ============================================================================
# 行为沙箱（macOS 同口径门控，跑真源脚本全链路）
# ============================================================================


class TestWiringBehavior:
    @_requires_darwin
    def test_dispatch_proceeds_and_claims_within_budget(self, tmp_path):
        """窗口内正常：无 over_limit/unavailable，派发放行 + 记账落账本。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir, {"GATEWAY_PATROL_FORCE": "1"})

        result = _run_patrol(env)
        log = _combined(base, result)

        assert marker.exists(), f"预算接线不得拦派发（放行到 droid exec）:\n{log[-1500:]}"
        assert "budget_over_limit" not in log, f"窗口内不得误报超限:\n{log[-1500:]}"
        assert "budget_unavailable" not in log, f"窗口内不得误报不可用:\n{log[-1500:]}"
        records = _wired_dispatch_records(base)
        assert records, f"派发放行必须留账本记录:\n{log[-1500:]}"
        assert records[0]["source"] == BUDGET_SOURCE
        assert records[0]["kind"] == BUDGET_KIND
        assert records[0]["linearRef"] == "forced-drill"
        assert "dispatched rc=0" in log
        dispatched = json.loads(_dispatched_line(base))
        assert dispatched["status"] == "dispatched"
        state = json.loads((base / "status" / "gateway-patrol.json").read_text())
        assert state["last_dispatch_ts"] > 0
        assert state["last_rc"] == 0

    @_requires_darwin
    def test_over_limit_still_dispatches(self, tmp_path):
        """超预算：budget_over_limit WARN + 派发照常（observe-only 不拒发）。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        seed = run_ledger_fn(
            f'ledger_claim "gateway-patrol-seed-0001" "{BUDGET_SOURCE}" "fp-seed" '
            f'"seed" "" "{BUDGET_KIND}"; echo "seeded"',
            base,
        )
        assert "seeded" in seed.stdout, f"seed 失败: {seed.stdout} {seed.stderr}"
        env = _sandbox_env(
            tmp_path,
            home,
            base,
            bin_dir,
            {
                "GATEWAY_PATROL_FORCE": "1",
                "DISPATCH_DAILY_BUDGET_GATEWAY_PATROL": "1",
            },
        )

        result = _run_patrol(env)
        log = _combined(base, result)

        assert marker.exists(), f"超限必须放行（observe-only，拒发即违规）:\n{log[-1500:]}"
        assert "budget_over_limit" in log, f"超限必须产生 WARN 遥测:\n{log[-1500:]}"
        records = _wired_dispatch_records(base)
        assert records, f"超限派发仍须记账:\n{log[-1500:]}"
        assert "dispatched rc=0" in log

    @_requires_darwin
    def test_ledger_infra_unavailable_fail_open(self, tmp_path):
        """基建不可达（status 变普通文件）：budget_unavailable WARN + fail-open
        派发照常，账本零残留。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir, {"GATEWAY_PATROL_FORCE": "1"})
        (base / "status").write_text("not-a-dir")

        result = _run_patrol(env)
        log = _combined(base, result)

        assert marker.exists(), f"账本不可用必须 fail-open 放行:\n{log[-1500:]}"
        assert "budget_unavailable" in log, f"不可用必须产生 WARN 遥测:\n{log[-1500:]}"
        assert "WARN: dispatch ledger unavailable, fail-open continue" in log
        assert not (base / "status" / "ledger").is_dir(), "不可用路径不得产生账本落盘"

    @_requires_darwin
    def test_lib_missing_zero_side_effects_dispatch_proceeds(self, tmp_path):
        """lib 缺失（sync 漂移窗口）：只禁用本次计量记账，派发照常。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir, {"GATEWAY_PATROL_FORCE": "1"})
        # 脚本副本落沙盒 scripts/（BASH_SOURCE 目录无 lib/ → 守卫跳过 source）
        sandbox_script = scripts / "gateway-patrol.sh"
        sandbox_script.write_text(PATROL_SCRIPT.read_text(encoding="utf-8"))
        sandbox_script.chmod(0o755)

        result = subprocess.run(
            ["bash", str(sandbox_script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=env["_SANDBOX_CWD"],
        )
        log = _combined(base, result)

        assert marker.exists(), f"lib 缺失不得拦派发:\n{log[-1500:]}"
        assert "budget/ledger metering disabled this invocation" in log
        assert read_events(base) == [], "lib 缺失路径不得有账本写入"

    @_requires_darwin
    def test_ok_path_no_ledger_side_effects(self, tmp_path):
        """无告警（ok）与抑制（suppress）路径零账本副作用（接线只在 dispatch 分支）。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir)

        result = _run_patrol(env)
        log = _combined(base, result)

        assert not marker.exists(), "ok 路径不得派发"
        assert "ok: no alarm" in log
        assert read_events(base) == [], "ok 路径不得有账本写入"
        assert not (base / "status" / "ledger").is_dir()


# ============================================================================
# 阈值评估复活（评审线程 P1 修复，2026-10-10 用户裁定「修复后过门」）
#
# 生产副本固有 printf | python3 - <<'PY' 形态中 heredoc 覆盖 fd 0（SC2259），
# sys.stdin.read() 恒空 → ALARM 恒为 {"alarm": false}，真实告警永不派发。
# 入仓版改单进程 argv/管道混合形态，评估块真正读到探测 JSON。本类不用
# GATEWAY_PATROL_FORCE（那会绕过评估块），靠真实越线探测 JSON 走全链路。
# ============================================================================


class TestThresholdDispatchBehavior:
    @staticmethod
    def _probe_stub(scripts: Path, probe_json: str, exit_code: int = 0) -> None:
        write_stub(
            scripts / "gateway-patrol-probe.sh",
            f"#!/bin/bash\nprintf '%s' '{probe_json}'\nexit {exit_code}\n",
        )

    def _expect_threshold_dispatch(self, tmp_path: Path, probe_json: str, fingerprint: str):
        home, base, scripts = _sandbox_home(tmp_path)
        self._probe_stub(scripts, probe_json)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir)

        result = _run_patrol(env)
        log = _combined(base, result)

        assert marker.exists(), f"越线形态必须触发派发:\n{log[-1500:]}"
        assert fingerprint in log, f"派发指纹不符:\n{log[-1500:]}"
        records = _wired_dispatch_records(base)
        assert records, f"阈值派发必须留账本记录:\n{log[-1500:]}"
        assert records[0]["source"] == BUDGET_SOURCE
        dispatched = json.loads(_dispatched_line(base))
        assert dispatched["status"] == "dispatched"
        return base, log

    @_requires_darwin
    def test_threshold_dispatch_5xx_spike_real_alarm(self, tmp_path):
        """越线形态一（5xx 尖峰 ≥15 → critical）：不用 FORCE，评估块真实判定
        派发——SC2259 修复的直接实证。"""
        probe = (
            '{"containers":{"kong":"Up 2 hours","kong-database":"Up 2 hours"},'
            '"http_5xx_30m":20,"error_lines_30m":0,"disk_root_pct":42,'
            '"cert_days_left":90,"smoke":{"ok":true},"upstreams":{}}'
        )
        fp = hashlib.sha1(b"5xx_spike").hexdigest()[:12]
        base, log = self._expect_threshold_dispatch(tmp_path, probe, fp)
        events = read_events(base)
        assert [e["event"] for e in events] == ["dispatch_attempted"]
        assert events[0]["ref"] == fp, "5xx 告警指纹未入账本事件流"

    @_requires_darwin
    def test_threshold_dispatch_disk_critical_real_alarm(self, tmp_path):
        """越线形态二（根盘 ≥92% → critical）：第二类独立越线派发实证。"""
        probe = (
            '{"containers":{"kong":"Up 2 hours","kong-database":"Up 2 hours"},'
            '"http_5xx_30m":0,"error_lines_30m":0,"disk_root_pct":95,'
            '"cert_days_left":90,"smoke":{"ok":true},"upstreams":{}}'
        )
        fp = hashlib.sha1(b"disk_critical").hexdigest()[:12]
        self._expect_threshold_dispatch(tmp_path, probe, fp)

    @_requires_darwin
    def test_threshold_quiet_when_below_threshold(self, tmp_path):
        """未越线（含单条 warn 升高 5xx=7 < 2 条告警线）：静默 ok，零派发
        零账本写入——评估块复活后的「不该响不响」面。"""
        home, base, scripts = _sandbox_home(tmp_path)
        self._probe_stub(
            scripts,
            '{"containers":{"kong":"Up 2 hours","kong-database":"Up 2 hours"},'
            '"http_5xx_30m":7,"error_lines_30m":1,"disk_root_pct":42,'
            '"cert_days_left":90,"smoke":{"ok":true},"upstreams":{}}',
        )
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(tmp_path, home, base, bin_dir)

        result = _run_patrol(env)
        log = _combined(base, result)

        assert not marker.exists(), "未越线不得派发"
        assert "ok: no alarm" in log
        assert read_events(base) == [], "未越线不得有账本写入"

    @_requires_darwin
    def test_decision_error_lands_on_default_branch(self, tmp_path):
        """评审线程 P2：决策步 python3 失效（DECISION 捕获为空 → ACTION 空）
        必须落 default 兜底（decision_error 留痕），禁止静默 exit 0；零派发
        零账本写入。阈值评估步经 PATROL_EVAL_MODE 标记放行（真 python3），
        其余裸 python3 调用走 127 桩（bin_dir 先于系统 PATH 遮蔽）。"""
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        real_python3 = shlex.quote(sys.executable)
        write_stub(
            bin_dir / "python3",
            "#!/bin/bash\n"
            'if [ -n "${PATROL_EVAL_MODE:-}" ]; then exec '
            f'{real_python3} "$@"; fi\n'
            "exit 127\n",
        )
        env = _sandbox_env(tmp_path, home, base, bin_dir)

        result = _run_patrol(env)
        log = _combined(base, result)

        assert not marker.exists(), "决策异常路径不得派发"
        day_logs = sorted((base / "logs" / "gateway-patrol").glob("patrol-*.jsonl"))
        assert day_logs, "day log 缺失：default 分支未落 rec 留痕"
        day_text = "".join(p.read_text(encoding="utf-8") for p in day_logs)
        assert '"status":"decision_error"' in day_text, (
            f"decision_error 留痕缺失:\n{day_text[-800:]}"
        )
        assert "unknown/empty decision" in log
        assert read_events(base) == [], "决策异常路径不得有账本写入"


class TestBudgetOkTelemetry:
    """budget_ok 正面覆盖（评审线程 P3 测试质量）：正常计量留痕的发射与
    DRY_RUN 抑制面——回归只能落在 events.jsonl 审计或 posthog stdout 之一，
    两侧都断言。"""

    def _force_dispatch_sandbox(self, tmp_path: Path, *, dry_run: bool):
        home, base, scripts = _sandbox_home(tmp_path)
        _install_probe_stub(scripts)
        marker = tmp_path / "droid-marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        extra = {"GATEWAY_PATROL_FORCE": "1"}
        if not dry_run:
            # _sandbox_env 基线钉 POSTHOG_DRY_RUN=1（沙盒隔离面）；本用例要
            # 走审计落盘分支，显式反向覆盖（沙盒 HOME 独立，无生产接触）。
            extra["POSTHOG_DRY_RUN"] = "0"
        env = _sandbox_env(tmp_path, home, base, bin_dir, extra)
        result = _run_patrol(env)
        return base, _combined(base, result)

    def test_budget_ok_emitted_on_under_limit_dispatch(self, tmp_path):
        """未超限派发必须发 budget_ok 留痕（VAL-BUD-002 对账面正向覆盖）。
        沙盒 POSTHOG_DRY_RUN=1 下审计静默，以 posthog stdout 打印为观测面。"""
        base, log = self._force_dispatch_sandbox(tmp_path, dry_run=True)
        assert "[POSTHOG_DRY_RUN]" in log, "dry-run 观测面缺失（posthog.sh 守卫未走）"
        assert "error_type=budget_ok" in log, "未超限派发必须产生 budget_ok 遥测打印"
        ok_lines = [ln for ln in log.splitlines() if "error_type=budget_ok" in ln]
        assert len(ok_lines) == 1, f"budget_ok 应恰一次，got {len(ok_lines)}"
        assert "source=gateway-patrol" in ok_lines[0]
        assert "count=0" in ok_lines[0], "空窗计数必须为 0"
        assert "limit=50" in ok_lines[0], "无覆盖配置时缺省上限 50"

    def test_budget_ok_suppressed_under_dry_run_in_audit(self, tmp_path):
        """POSTHOG_DRY_RUN=1：budget_ok 不得落 events.jsonl 审计（与
        over_limit/unavailable 同一 DRY_RUN 契约）；告警事件保持零发射。"""
        base, log = self._force_dispatch_sandbox(tmp_path, dry_run=True)
        budget_events = [
            e for e in read_events(base) if str(e.get("event", "")).startswith("budget")
        ]
        assert budget_events == [], "DRY_RUN=1 下 budget 面不得落审计事件"

    def test_budget_ok_lands_in_audit_without_dry_run(self, tmp_path):
        """无 DRY_RUN（无 API key，PostHog 发送自跳过）：budget_ok 落
        events.jsonl 审计恰一次，且无告警事件混入。"""
        base, log = self._force_dispatch_sandbox(tmp_path, dry_run=False)
        budget_events = [
            e for e in read_events(base) if str(e.get("event", "")).startswith("budget")
        ]
        assert len(budget_events) == 1, f"budget 遥测应恰一条 budget_ok: {budget_events}"
        ev = budget_events[0]
        assert ev["event"] == "budget_ok"
        assert f"source={BUDGET_SOURCE}" in ev["detail"]
        assert "budget_over_limit" not in log
        assert "budget_unavailable" not in log
