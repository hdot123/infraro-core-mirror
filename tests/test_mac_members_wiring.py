#!/usr/bin/env python3
"""Mac 成员接线行为契约（VAL-BUD-002，a′ 收门 mac-members-wiring）。

五脚本（trigger-release / wiki-refresh / ci-failed / ci-timeout-watchdog /
poll-releases）派发点接线（architecture §3.2）：
- 派发点前守卫式 source lib/dispatch-ledger.sh（lib 缺失=127 降级，零副作用）
- ledger_budget_check（告警不拦，rc 恒 0；取 stderr WARN 不取返回拦）
- ledger_claim + ledger_append 记账（uuid 入参、kind 归属成员通道）
- trigger-droid.sh 只补 budget check，既有账本集成不动（Part B 保持绿）

预算三态断言（observe-first 裁定，任何拒发返回码都违规）：
- 窗口内正常 → budget_ok 事件 + 派发照常
- 超预算 → budget_over_limit WARN/遥测 + 派发照常（不拒发）
- 基建不可达（WEBHOOK_BASE/status 变普通文件）→ budget_unavailable WARN +
  派发照常 = fail-open 实证（hooks.json 型 trigger-release + launchd 型
  ci-timeout-watchdog 两代表面各验一遍）

沙盒模式（对齐 test_dispatch_ledger.py Part B / test_trigger_release_contract）：
临时 WEBHOOK_BASE + stub droid bin（marker 观测 droid exec 到达面）+
POSTHOG_DRY_RUN=1 隔离 + PYTHON_BIN=py sys.executable 跨平台，零生产接触
（禁写生产 ~/.factory/webhook/status/ledger/）。行为沙箱跑真源脚本全链路，
macOS 专属路径（/opt/homebrew/bin/{flock,python3}）与既有套件同口径门控。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.ledger_check_helpers import (
    read_events,
    read_record,
    run_ledger_fn,
)
from tests.trigger_sandbox_helpers import (
    darwin_skip_reason,
    wait_for,
    write_stub,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_DIR = REPO_ROOT / "webhook-scripts"
MANIFEST_PATH = WEBHOOK_DIR / "MANIFEST.sh"
TRIGGER_DROID = WEBHOOK_DIR / "trigger-droid.sh"

# 成员脚本 → 账本 source 身份（与接线一致，一一对应）
WIRING_MEMBERS = {
    "trigger-release.sh": "trigger-release",
    "wiki-refresh.sh": "wiki-refresh",
    "ci-failed.sh": "ci-failed",
    "ci-timeout-watchdog.sh": "ci-fallback",
    "poll-releases.sh": "poll-releases",
}

UUID_A = "11111111-aaaa-bbbb-cccc-00000000000a"


def _requires_darwin(func):
    return pytest.mark.skipif(darwin_skip_reason() is not None, reason=darwin_skip_reason() or "")(
        func
    )


# ============================================================================
# 沙盒原语
# ============================================================================


def _sandbox_webhook(tmp_path: Path) -> Path:
    """WEBHOOK_BASE 沙盒（locks/logs 就绪，ledger 目录留给脚本懒创建）。"""
    base = tmp_path / "webhook"
    (base / "locks").mkdir(parents=True)
    (base / "logs").mkdir(parents=True)
    return base


def _block_ledger(base: Path) -> None:
    """预算不可达注入：status 变普通文件 → mkdir -p status/ledger 必败
    （budget_unavailable 分支 + ledger_claim rc=2 fail-open 分支同源）。"""
    (base / "status").write_text("not-a-dir")


def _stub_droid_bin(tmp_path: Path, marker: Path, exit_code: int = 0) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    write_stub(
        bin_dir / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho "STUB-DROID-LAUNCHED" >&2\nexit {exit_code}\n',
    )
    return bin_dir


def _run_script(script: Path, args: list[str], env: dict, timeout: int = 60):
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        capture_output=True,
        timeout=timeout,
        cwd=str(env.get("_SANDBOX_CWD", str(REPO_ROOT))),
    )


def _sandbox_env(
    tmp_path: Path,
    base: Path,
    *,
    path_prepend: list[Path] | None = None,
    extra: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    env["WEBHOOK_BASE"] = str(base)
    env["LOCKS_DIR"] = str(base / "locks")
    env["LOG_DIR"] = str(base / "logs")
    env["PYTHON_BIN"] = sys.executable
    env["POSTHOG_DRY_RUN"] = "1"
    # macOS bash 3.2 在 zh_CN.UTF-8 ctype 下解析「"$var，"（CJK 邻接）」会把
    # 多字节首字节当变量名终止符 → tag\xef unbound（宿主既有脚本固有特性，
    # 与接线无关）。沙盒钉 LC_ALL=C 归一化字节语义（生产 launchd 环境本就
    # 无该 locale 注入），保持对宿主脚本零改动。
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    parts = [str(p) for p in (path_prepend or [])] + [env.get("PATH", "")]
    env["PATH"] = ":".join(parts)
    if extra:
        env.update(extra)
    env["_SANDBOX_CWD"] = str(tmp_path)
    return env


def _combined(base: Path, result) -> str:
    logs = base / "logs"
    parts = [result.stdout, result.stderr]
    if logs.exists():
        for p in sorted(logs.glob("*.log")):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(
        x.decode("utf-8", errors="replace") if isinstance(x, bytes) else x for x in parts
    )


def _member_log(base: Path, name: str) -> str:
    """按文件名读单个沙盒日志（wiki 日志带时间戳，用通配前缀匹配）。"""
    logs = base / "logs"
    exact = logs / name
    if exact.exists():
        return exact.read_text(encoding="utf-8", errors="replace")
    stem = name.split(".")[0]
    parts = []
    if logs.exists():
        for p in sorted(logs.glob(f"{stem}*.log")):
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n".join(parts)


def _seed_budget_claims(
    base: Path, source: str, count: int = 1, *, kind: str = "linear-gateway"
) -> None:
    """向沙盒账本预置当日 N 条 source 记录（走真 ledger_claim + LEDGER_BASE_TS）。

    接线方 budget check 带 kind（成员通道归属），seed 必须同 kind 才会计入窗口。"""
    lines = []
    for seq in range(1, count + 1):
        uuid = f"w1r1n6c5-7777-4c0e-9f1a-{seq:012d}"
        lines.append(
            f'LEDGER_BASE_TS="$(date -u +%Y-%m-%d)T00:00:00Z" ledger_claim "{uuid}" "{source}" '
            f'"INFRA-{9000 + seq}" "seed" "INFRA" "{kind}"'
        )
    r = run_ledger_fn("\n".join(lines) + '\necho "seeded"', base)
    assert "seeded" in r.stdout, f"seed failed: {r.stdout} {r.stderr}"


def _create_fixture_repo_with_origin(tmp_path: Path, name: str):
    """bare remote + clone 仓（git pull origin main 可用；对齐既有 helper 口径）。"""
    import subprocess as sp

    remote = tmp_path / f"{name}_remote.git"
    remote.mkdir()
    sp.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
    sp.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=remote,
        capture_output=True,
        check=True,
    )
    repo = tmp_path / name
    sp.run(["git", "clone", str(remote), str(repo)], capture_output=True, check=True)
    sp.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    sp.run(["git", "config", "user.name", "Test User"], cwd=repo, capture_output=True, check=True)
    (repo / "README.md").write_text("# fixture\n")
    sp.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True, check=True)
    sp.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, check=True)
    sp.run(
        ["git", "branch", "--set-upstream-to=origin/main", "main"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


def _repositories_yml(tmp_path: Path, consumers: list[dict]) -> Path:
    import yaml

    config = {"teams": {"infra": {"repos": consumers}}}
    config_path = tmp_path / "repositories.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


def _assert_passes_and_claimed(
    base: Path, source: str, kind: str, marker: Path, log: str, ctx: str
) -> None:
    """接线正路径三断言：droid 到达 + 记录落账本 + dispatch_attempted 留痕。

    budget_ok 观测面说明：lib 在窗口内正常计数时**静默**（无 WARN 无事件）；
    预算遥测在 POSTHOG_DRY_RUN=1 下只走 posthog.sh stdout 打印面（budget_ok
    字样出现在 result.stdout，_combined 已并入）。events.jsonl 的
    dispatch_attempted + 记录本体是接线正路径的持久观测面。
    """
    assert marker.exists(), f"预算接线不得拦派发（放行到 droid exec）: {ctx}\n{log[-1500:]}"
    assert "budget_ok" in log, f"dry-run 打印面必须可见 budget_ok 计量: {ctx}"
    events = [e["event"] for e in read_events(base)]
    assert "dispatch_attempted" in events, f"派发放行必须留 dispatch_attempted 事件: {ctx}"
    records = [
        read_record(base, e["uuid"])
        for e in read_events(base)
        if e["event"] == "dispatch_attempted"
    ]
    records = [r for r in records if r]
    assert records, f"dispatch_attempted 必须对应账本记录: {ctx}"
    assert records[0]["source"] == source
    assert records[0]["kind"] == kind


# ============================================================================
# 结构不变量（全平台执行）
# ============================================================================


class TestWiringStructuralInvariants:
    def test_five_members_registered_in_manifest(self):
        manifest = MANIFEST_PATH.read_text(encoding="utf-8")
        for name in WIRING_MEMBERS:
            assert f'"{name}"' in manifest, f"{name} 必须在 MANIFEST.sh 受管（随 sync 部署）"

    def test_each_member_sources_ledger_lib_defensively(self):
        for name in WIRING_MEMBERS:
            text = (WEBHOOK_DIR / name).read_text(encoding="utf-8")
            assert "lib/dispatch-ledger.sh" in text, f"{name} 缺 source lib"
            # 守卫式 source：lib 缺失（sync 漂移窗口）必须 fail-open，不得 set -e 裸 source
            assert re.search(
                r"if\s+\[\s+-f\s+\"?\$?\w*ledger_lib\"?\s*\]\s*;\s*then", text, re.IGNORECASE
            ) or re.search(r"if\s+\[\s+-f\s+\"\$_LEDGER_LIB\"\s*\]\s*;\s*then", text), (
                f"{name} 的 source 必须有文件存在性守卫（fail-open）"
            )

    def test_each_member_budget_source_unique_and_exact(self):
        for name, source in WIRING_MEMBERS.items():
            text = (WEBHOOK_DIR / name).read_text(encoding="utf-8")
            calls = re.findall(r'ledger_budget_check\s+"([^"]+)"', text)
            assert calls == [source], f"{name} budget source 必须恰为 {source}，got {calls}"

    def test_each_member_claims_and_appends(self):
        for name in WIRING_MEMBERS:
            text = (WEBHOOK_DIR / name).read_text(encoding="utf-8")
            assert "ledger_claim" in text, f"{name} 缺 ledger_claim 记账"
            assert 'ledger_append "$DISPATCH_UUID"' in text or (
                "ledger_append" in text and "dispatch_attempted" in text
            ), f"{name} 缺 dispatch_attempted 留痕"

    def test_trigger_droid_adds_budget_check_without_touching_existing_integration(self):
        text = TRIGGER_DROID.read_text(encoding="utf-8")
        # 只补一处调用（guard 行 declare -F ledger_budget_check 不算调用面）
        calls = [ln for ln in text.splitlines() if re.match(r"\s*ledger_budget_check\s", ln)]
        assert len(calls) == 1, f"trigger-droid 只补 budget check 调用，got {calls}"
        assert 'ledger_budget_check "trigger-droid"' in text
        # 既有集成面锚点抽查（Part B 行为断言由 test_dispatch_ledger.py 全量守护）
        assert "ledger_dispatch_done" in text
        assert "SKIP: dispatch ledger hit" in text
        assert "WARN: dispatch ledger unavailable" in text
        assert "dispatch ledger lib unavailable" in text
        # budget check 补在既有 create 守卫块内（lib 缺失=整块降级，不新增裸调用面）
        guard_pos = text.find("declare -F ledger_claim >/dev/null 2>&1; then")
        budget_pos = text.find("ledger_budget_check")
        assert guard_pos != -1 and budget_pos != -1 and guard_pos < budget_pos

    def test_ci_failed_claim_key_is_minted_not_borrowed(self):
        """ci-failed 的派发事实是「发了评论」，claim 键必须每次调用铸造独立
        uuid4（不得借 Linear 卡 UUID——那是 trigger-droid 的去重键空间）。"""
        text = (WEBHOOK_DIR / "ci-failed.sh").read_text(encoding="utf-8")
        assert "DISPATCH_UUID=" in text, "缺 claim 键铸造面"
        dispatch_line = next(
            ln for ln in text.splitlines() if ln.strip().startswith("DISPATCH_UUID=")
        )
        assert "uuid" in dispatch_line.lower(), (
            f"claim 键必须每次调用铸造 uuid4，got: {dispatch_line.strip()}"
        )
        # 键位永不得取自 ISSUE_ID（含 legacy 硬编码 UUID 的间接借用）
        assert "ISSUE_ID" not in dispatch_line, f"键位不得借用卡 UUID: {dispatch_line.strip()}"
        assert not re.search(r"f023acdf-71d9-4121-8edf-a9ed8c7c05f7\"\s*$", dispatch_line)
        # ref 语义保持：ISSUE_ID 仍进 ledger_claim 的 ref 位（取调用面，非 declare -F 守卫行）
        claim_line = next(
            ln
            for ln in text.splitlines()
            if ln.strip().startswith("ledger_claim") and "declare" not in ln
        )
        assert "$ISSUE_ID" in claim_line, f"ref 语义=ISSUE_ID 进 linearRef: {claim_line.strip()}"

    def test_wiring_members_bash_syntax(self):
        from tests.trigger_sandbox_helpers import bash_syntax_ok

        ok, err = bash_syntax_ok(*[WEBHOOK_DIR / n for n in WIRING_MEMBERS], TRIGGER_DROID)
        assert ok, err

    def test_wiring_members_shellcheck_clean(self):
        if not __import__("shutil").which("shellcheck"):
            pytest.skip("shellcheck not installed")
        for name in [*WIRING_MEMBERS, "trigger-droid.sh"]:
            r = subprocess.run(
                ["shellcheck", "-x", "--severity=error", str(WEBHOOK_DIR / name)],
                capture_output=True,
                text=True,
            )
            assert r.returncode == 0, f"{name} shellcheck: {r.stdout} {r.stderr}"


# ============================================================================
# trigger-release（hooks.json 型代表面）
# ============================================================================


class TestTriggerReleaseWiring:
    def _setup(self, tmp_path: Path):
        base = _sandbox_webhook(tmp_path)
        marker = tmp_path / "droid-launched.marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        repo = _create_fixture_repo_with_origin(tmp_path, "consumer")
        config = _repositories_yml(
            tmp_path,
            [{"repoKey": "consumer", "repoPath": str(repo), "engineConsumer": True}],
        )
        env = _sandbox_env(
            tmp_path, base, path_prepend=[bin_dir], extra={"REPO_CONFIG": str(config)}
        )
        return base, marker, env

    def _run(self, env: dict, tag: str = "v9.9.9"):
        return _run_script(
            WEBHOOK_DIR / "trigger-release.sh",
            [tag, "https://example.com/rel", "hdot123/infraro-core"],
            env,
        )

    def _member_log_path(self, base: Path) -> Path:
        return base / "logs" / "trigger-release.log"

    @_requires_darwin
    def test_wired_dispatch_claims_ledger_then_launches(self, tmp_path):
        base, marker, env = self._setup(tmp_path)
        r = self._run(env)
        assert wait_for(lambda: marker.exists(), timeout=60), (
            f"droid 未到达: {_member_log(base, 'trigger-release.log')[-1500:]}"
        )
        # 后台子 shell 异步追加：以账本记录/事件落盘为完成信号再读日志
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            evts = [e["event"] for e in read_events(base)]
            if "dispatch_attempted" in evts:
                break
            time.sleep(0.5)
        log = _combined(base, r)
        _assert_passes_and_claimed(
            base, "trigger-release", "release-announce", marker, log, "trigger-release"
        )

    @_requires_darwin
    def test_budget_unreachable_still_dispatches_fail_open(self, tmp_path):
        """预算基建不可达 → budget_unavailable WARN + 照常派发（hooks.json 型 fail-open 实证）。"""
        base, marker, env = self._setup(tmp_path)
        _block_ledger(base)
        self._run(env)
        assert wait_for(lambda: marker.exists(), timeout=60), "fail-open：账本不可达不得瘫痪派发链"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if "budget_unavailable" in _member_log(base, "trigger-release.log"):
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _member_log(base, "trigger-release.log")
        assert "budget_unavailable" in log, f"不可达必须留 WARN: {log[-1500:]}"
        assert read_record(base, UUID_A) is None
        assert not (base / "status" / "ledger" / "events.jsonl").exists(), "不可达下零账本残留"

    @_requires_darwin
    def test_over_limit_alerts_and_still_dispatches(self, tmp_path):
        """超预算 → budget_over_limit WARN + 照常派发（observe-only，绝不拒发）。"""
        base, marker, env = self._setup(tmp_path)
        _seed_budget_claims(base, "trigger-release", 1, kind="release-announce")
        env["DISPATCH_DAILY_BUDGET_TRIGGER_RELEASE"] = "1"
        self._run(env)
        assert wait_for(lambda: marker.exists(), timeout=60), "超预算必须放行（拒发即违规）"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if "budget_over_limit" in _member_log(base, "trigger-release.log"):
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _member_log(base, "trigger-release.log")
        assert "budget_over_limit" in log, f"超限必须留告警: {log[-1500:]}"
        # DRY_RUN=1 下 budget 遥测只走打印面（lib 契约：events.jsonl 审计一并静默）；
        # events.jsonl 持久面只有接线自己的 dispatch_attempted 留痕
        assert "budget_over_limit" in log and "[POSTHOG_DRY_RUN]" in log
        events = [e["event"] for e in read_events(base)]
        assert "dispatch_attempted" in events

    @_requires_darwin
    def test_idempotent_replay_still_skips_without_ledger_side_effects(self, tmp_path):
        """幂等锁命中：接线不得绕过既有跳过路径，账本零副作用。"""
        base, marker, env = self._setup(tmp_path)
        (base / "locks" / "release-announce-v9.9.9.json").write_text('{"bootstrap": true}')
        r = self._run(env)
        assert r.returncode == 0
        time.sleep(1)
        assert not marker.exists(), "幂等命中不得放行派发"
        assert not (base / "status" / "ledger").exists() or not any(
            (base / "status" / "ledger").iterdir()
        ), "跳过路径不得写账本"


# ============================================================================
# ci-timeout-watchdog（launchd 型代表面）
# ============================================================================


class TestCiTimeoutWatchdogWiring:
    def _setup(self, tmp_path: Path, pr: int = 4242):
        base = _sandbox_webhook(tmp_path)
        marker = tmp_path / "droid-launched.marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        env = _sandbox_env(
            tmp_path,
            base,
            path_prepend=[bin_dir],
            extra={
                "P0A_DRY_RUN": "1",
                "ECHO_DROID": "1",
                "DROID_BIN": str(bin_dir / "droid"),
            },
        )
        stale = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            - __import__("datetime").timedelta(minutes=35)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        (base / "locks" / f"pending-ci-{pr}.json").write_text(
            json.dumps({"pr_number": str(pr), "created_at": stale, "source": "session", "cwd": ""})
        )
        return base, marker, env, pr

    def _setup_phase_b(self, tmp_path: Path, pr: int = 4242, merged: bool = False):
        """Phase B 样本：注入时间超 45min（injected 未消费面）。"""
        base, marker, env, pr = self._setup(tmp_path, pr=pr)
        injected = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            - __import__("datetime").timedelta(minutes=50)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        pending = base / "locks" / f"pending-ci-{pr}.json"
        # cwd 需是真实 git 仓（watchdog 由 cwd 推导 owner/repo 查询 PR 状态）
        repo = _create_fixture_repo_with_origin(tmp_path, "phaseb_repo")
        pending.write_text(
            json.dumps(
                {
                    "pr_number": str(pr),
                    "created_at": injected,
                    "injected_at": injected,
                    "source": "session",
                    "cwd": str(repo),
                }
            )
        )
        if merged:
            # merged 判定走 gh stub 面：watchdog 调 gh pr view --json state → MERGED
            gh_stub = Path(env["PATH"].split(":")[0]) / "gh"
            write_stub(
                gh_stub,
                '#!/bin/bash\ncase "$*" in *"--json state"*) echo \'{"state":"MERGED"}\' ;; *) echo "{}" ;; esac\n',
            )
        return base, marker, env, pr

    def _run(self, env: dict):
        return _run_script(WEBHOOK_DIR / "ci-timeout-watchdog.sh", [], env)

    @_requires_darwin
    def test_fallback_spawn_claims_ledger(self, tmp_path):
        base, marker, env, pr = self._setup(tmp_path)
        r = self._run(env)
        out = _combined(base, r)
        assert "spawning fallback" in out.lower(), f"Phase A 必须 spawn fallback: {out[-1500:]}"
        record = None
        for e in read_events(base):
            if e["event"] == "dispatch_attempted":
                record = read_record(base, e["uuid"])
        assert record and record["source"] == "ci-fallback" and record["kind"] == "ci-fallback"
        assert record["linearRef"] == f"INFRA-{pr}"
        # budget_ok 在 DRY_RUN 打印面（stdout）；events.jsonl 只有 claim 留痕
        assert "budget_ok" in out
        assert "dispatch_attempted" in [e["event"] for e in read_events(base)]

    @_requires_darwin
    def test_budget_unreachable_still_dispatches_fail_open(self, tmp_path):
        """预算基建不可达 → WARN + 照常 spawn fallback（launchd 型 fail-open 实证）。"""
        base, marker, env, pr = self._setup(tmp_path)
        _block_ledger(base)
        r = self._run(env)
        out = _combined(base, r)
        assert "budget_unavailable" in out, f"不可达必须留 WARN: {out[-1500:]}"
        assert "spawning fallback" in out.lower(), "fail-open：预算不可达不得瘫痪 fallback 派发"

    @_requires_darwin
    def test_merged_pr_cleans_without_ledger_side_effects(self, tmp_path):
        """Phase B merged 终态：清理 pending 文件、不 spawn、账本零副作用。"""
        base, marker, env, pr = self._setup_phase_b(tmp_path, merged=True)
        r = self._run(env)
        out = _combined(base, r)
        assert "already merged" in out.lower(), out[-1500:]
        assert not (base / "locks" / f"pending-ci-{pr}.json").exists()
        assert not (base / "status" / "ledger").exists() or not any(
            (base / "status" / "ledger").iterdir()
        ), "非派发终态路径不得写账本"


# ============================================================================
# wiki-refresh（hooks.json 型）
# ============================================================================


class TestWikiRefreshWiring:
    def _setup(self, tmp_path: Path, *, bare: bool = False):
        base = _sandbox_webhook(tmp_path)
        marker = tmp_path / "droid-launched.marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        # wiki-refresh.sh 头部自行 PREPEND ~/.local/bin:/opt/homebrew/bin（生产 homebrew
        # 工具链），沙盒 bin 前插不赢 → 钉 DROID_BIN 直指 stub（脚本原生支持），隔离真
        # droid 面。ci-timeout-watchdog 的 droid 解析同理经 DROID_BIN。
        if bare:
            repo = tmp_path / "wiki_bare"
            repo.mkdir()
        else:
            repo = _create_fixture_repo_with_origin(tmp_path, "wiki_repo")
        config = _repositories_yml(
            tmp_path,
            [{"repoKey": "wiki-key", "githubRepo": "hdot123/wiki-repo", "repoPath": str(repo)}],
        )
        env = _sandbox_env(
            tmp_path,
            base,
            path_prepend=[bin_dir],
            extra={"REPO_CONFIG": str(config), "DROID_BIN": str(bin_dir / "droid")},
        )
        return base, marker, env, repo

    def _run(self, env: dict):
        return _run_script(
            WEBHOOK_DIR / "wiki-refresh.sh",
            ["", "hdot123/wiki-repo", "refs/heads/main", "abc1234"],
            env,
        )

    @_requires_darwin
    def test_wired_dispatch_claims_ledger_then_launches(self, tmp_path):
        base, marker, env, _ = self._setup(tmp_path)
        r = self._run(env)
        assert wait_for(lambda: marker.exists(), timeout=60), (
            f"droid 未到达: {_member_log(base, 'wiki-refresh.log')[-1500:]}"
        )
        # 后台子 shell 异步追加（flock fd/tee 竞态）：以账本事件落盘为完成信号再汇总日志
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            evts = [e["event"] for e in read_events(base)]
            if "dispatch_attempted" in evts:
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _combined(base, r)
        _assert_passes_and_claimed(
            base, "wiki-refresh", "wiki-refresh", marker, log, "wiki-refresh"
        )

    @_requires_darwin
    def test_budget_unreachable_still_dispatches_fail_open(self, tmp_path):
        base, marker, env, _ = self._setup(tmp_path)
        _block_ledger(base)
        r = self._run(env)
        assert wait_for(lambda: marker.exists(), timeout=60), (
            "fail-open：预算不可达不得拦 wiki 派发"
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if "budget_unavailable" in _member_log(base, "wiki-refresh.log"):
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _combined(base, r)
        assert "budget_unavailable" in log

    @_requires_darwin
    def test_git_pull_failure_short_circuits_before_ledger(self, tmp_path):
        """git pull 失败既有短路：不进派发点 → 账本零副作用（接线不得前移记账面）。"""
        base, marker, env, _ = self._setup(tmp_path, bare=True)
        r = self._run(env)
        log = _combined(base, r)
        assert r.returncode == 1
        assert "git pull origin main failed" in log
        assert not (base / "status" / "ledger").exists() or not any(
            (base / "status" / "ledger").iterdir()
        ), "pull 失败短路路径不得写账本"
        assert not marker.exists()


# ============================================================================
# trigger-droid 全链路沙盒（test_dispatch_ledger Part B 同构本地副本——不跨测试
# 模块 import；供跨源碰撞回归使用）
# ============================================================================


def _make_trigger_sandbox(tmp_path: Path) -> dict:
    sandbox = tmp_path / "webhook"
    repo = tmp_path / "target-repo"
    logs = sandbox / "logs"
    logs.mkdir(parents=True)
    (sandbox / "locks").mkdir(parents=True)
    (sandbox / "status").mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    marker = tmp_path / "droid-launched.marker"
    (tmp_path / "bin").mkdir()
    write_stub(
        tmp_path / "bin" / "droid",
        f'#!/bin/bash\ntouch "{marker}"\necho "STUB-DROID-LAUNCHED" >&2\nexit 127\n',
    )
    write_stub(
        tmp_path / "bin" / "gh",
        '#!/bin/bash\necho "gh-stub: $*" >> "GH_LOG"\nexit 0\n'.replace(
            "GH_LOG", str(tmp_path / "gh-calls.log")
        ),
    )
    config = tmp_path / "repositories.yml"
    config.write_text(
        json.dumps(
            {
                "teams": {
                    "infra": {
                        "teamKey": "INFRA",
                        "repos": [{"repoPath": str(repo), "default": True}],
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sandbox_lib = sandbox / "scripts" / "lib"
    sandbox_lib.mkdir(parents=True)
    write_stub(
        sandbox_lib / "linear-queue.sh",
        "linear_queue_init() { return 1; }\n"
        "linear_move_to_in_progress() { return 1; }\n"
        "linear_query_next_queued() { return 1; }\n"
        "linear_get_issue_team_key() { return 1; }\n",
    )
    write_stub(sandbox_lib / "p0a-guard.sh", "# test stub: guard disabled\n")
    prod_scripts = sandbox / "scripts"
    shutil.copy2(TRIGGER_DROID, prod_scripts / "trigger-droid.sh")
    prod_lib = prod_scripts / "lib"
    prod_lib.mkdir(exist_ok=True)
    shutil.copy2(WEBHOOK_DIR / "lib" / "dispatch-ledger.sh", prod_lib / "dispatch-ledger.sh")
    shutil.copy2(WEBHOOK_DIR / "lib" / "evolution-channel.sh", prod_lib / "evolution-channel.sh")
    shutil.copy2(WEBHOOK_DIR / "lib" / "posthog.sh", prod_lib / "posthog.sh")
    write_stub(
        prod_lib / "op-mcp.sh",
        'OP_VAULT_SEVER="test-vault-id"\nop_get_field() { return 1; }\n',
    )
    return {"sandbox": sandbox, "logs": logs, "marker": marker, "tmp": tmp_path}


def _run_trigger(
    sandbox: dict, ref: str = "INFRA-3301", uuid: str = UUID_A
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "WEBHOOK_BASE": str(sandbox["sandbox"]),
            "REPO_CONFIG": str(sandbox["tmp"] / "repositories.yml"),
            "PATH": f"{sandbox['tmp'] / 'bin'}:{env.get('PATH', '')}",
            "POSTHOG_DRY_RUN": "1",
            "LINEAR_API_KEY": "",
        }
    )
    env.pop("LINEAR_LABELS", None)
    env.pop("RESUME_SESSION_ID", None)
    return subprocess.run(
        [
            "bash",
            str(sandbox["sandbox"] / "scripts" / "trigger-droid.sh"),
            "create",
            "Issue",
            ref,
            uuid,
            "INFRA",
            "normal dispatch title",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(sandbox["tmp"]),
    )


def _td_combined(sandbox: dict, result: subprocess.CompletedProcess) -> str:
    parts = [result.stdout, result.stderr]
    if sandbox["logs"].exists():
        for p in sorted(sandbox["logs"].glob("*.log")):
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _td_wait_marker(sandbox: dict, timeout: float = 30.0) -> bool:
    """droid exec 在后台子 shell 异步启动：轮询等 marker（同 Part B 语义）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sandbox["marker"].exists():
            return True
        time.sleep(0.5)
    return sandbox["marker"].exists()


# ============================================================================
# ci-failed（hooks.json 型，Linear comment → trigger-droid 链）
# ============================================================================


class TestCiFailedWiring:
    """ci-failed 派发事实=「Linear comment 写入」，非「本机执行了这张卡」。

    claim 键因此必须每次调用铸造独立 uuid4（与其他四成员同范式）：
    - ref 语义：ISSUE_ID 进 linearRef（人查/对账面），永不进键位；
    - 键空间隔离：trigger-droid 以 Linear 卡 UUID 为键持有「本机执行过」的
      去重凭证（ledger_dispatch_done 只认 source=trigger-droid + completed，
      dispatch-ledger first-writer-wins）。ci-failed 若借卡 UUID 作键先记账，
      trigger-droid 的 claim 永远 rc=1 → dedup 保护被永久禁用（#302 P1）。
    - 预算失真：稳定键 + 按记录体计数 = 每卡终生只计 1 次（#302 P2）；
      每调用铸造后窗口计数=派发次数。
    - legacy 回退（ISSUE_ID 未设，生产 hooks.json 实际路由）同走每次铸造。
    """

    def _setup(self, tmp_path: Path):
        base = _sandbox_webhook(tmp_path)
        gh_log = tmp_path / "gh-calls.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        write_stub(
            bin_dir / "gh",
            '#!/bin/bash\necho "gh-stub: $*" >> "' + str(gh_log) + '"\nexit 0\n',
        )
        # commentCreate 走 curl（Linear API 面）：stub 收集调用，证明派发动作发生
        curl_log = tmp_path / "curl-calls.log"
        write_stub(
            bin_dir / "curl",
            '#!/bin/bash\necho "curl: $*" >> "'
            + str(curl_log)
            + '"\necho \'{\\"data\\":{\\"commentCreate\\":{\\"success\\":true}}}\'\nexit 0\n',
        )
        # op CLI 面（ci-failed 经 lib/op-mcp.sh 取 Linear key；1Password 沙盒替身）
        write_stub(bin_dir / "op", '#!/bin/bash\necho "stub-linear-key"\nexit 0\n')
        env = _sandbox_env(
            tmp_path,
            base,
            path_prepend=[bin_dir],
            extra={
                "ISSUE_ID": UUID_A,
                "CI_PIPELINE_ID": "9001",
                "CI_BRANCH": "main",
                "CI_SHA": "abc1234",
            },
        )
        return base, gh_log, curl_log, env

    def _run(self, env: dict):
        return _run_script(WEBHOOK_DIR / "ci-failed.sh", [], env)

    def _assert_comment_dispatched(self, curl_log: Path, ctx: str) -> None:
        assert curl_log.exists() and "commentCreate" in curl_log.read_text(), (
            f"Linear comment 派发必须发生: {ctx}"
        )

    def _attempted_records(self, base: Path) -> list[dict]:
        records = []
        for e in read_events(base):
            if e["event"] == "dispatch_attempted":
                rec = read_record(base, e["uuid"])
                if rec:
                    records.append(rec)
        return records

    @_requires_darwin
    def test_comment_dispatch_claims_ledger(self, tmp_path):
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        r = self._run(env)
        log = _combined(base, r)
        assert r.returncode == 0, log
        self._assert_comment_dispatched(curl_log, log)
        records = self._attempted_records(base)
        assert len(records) == 1, f"恰好一条派发记录: {records}"
        assert records[0]["source"] == "ci-failed" and records[0]["kind"] == "ci-failed"
        assert records[0]["linearRef"] == UUID_A, (
            "ref 语义=ISSUE_ID 进 linearRef（键位不借卡 UUID）"
        )
        # budget_ok 在 DRY_RUN 打印面；events.jsonl 有 dispatch_attempted 留痕
        assert "budget_ok" in log
        assert "dispatch_attempted" in [e["event"] for e in read_events(base)]

    @_requires_darwin
    def test_budget_unreachable_still_dispatches_fail_open(self, tmp_path):
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        _block_ledger(base)
        r = self._run(env)
        log = _combined(base, r)
        assert r.returncode == 0
        assert "budget_unavailable" in log, f"不可达必须留 WARN: {log[-1500:]}"
        self._assert_comment_dispatched(curl_log, log)
        assert (base / "status" / "ledger").exists() is False or not any(
            (base / "status" / "ledger").iterdir()
        )

    @_requires_darwin
    def test_claim_key_is_per_call_uuid_not_card_uuid(self, tmp_path):
        """P1 键位修复：卡 UUID 不得作为 claim 键（不落 <uuid>.json / by-ref）。"""
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        r = self._run(env)
        log = _combined(base, r)
        assert r.returncode == 0, log
        self._assert_comment_dispatched(curl_log, log)
        ledger = base / "status" / "ledger"
        assert not (ledger / f"{UUID_A}.json").exists(), (
            "卡 UUID 不得作为 claim 键（trigger-droid 键空间必须保持独立）"
        )
        records = self._attempted_records(base)
        assert len(records) == 1
        key = records[0]["linearUuid"]
        assert key != UUID_A, "claim 键必须与卡 UUID 不同（每次调用独立铸造）"
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", key
        ), f"claim 键必须是 uuid4 形态: {key}"

    @_requires_darwin
    def test_repeat_dispatch_mints_new_key_each_call(self, tmp_path):
        """同卡两次派发 = 两条独立记录（键不跨调用复用）；linearRef 均指回该卡。"""
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        r1 = self._run(env)
        assert r1.returncode == 0, _combined(base, r1)
        r2 = self._run(env)
        log = _combined(base, r2)
        assert r2.returncode == 0, log
        records = self._attempted_records(base)
        assert len(records) == 2, f"两次调用须两记录: {records}"
        keys = {rec["linearUuid"] for rec in records}
        assert len(keys) == 2, f"每次调用必须独立铸造键: {keys}"
        assert UUID_A not in keys
        assert all(rec["linearRef"] == UUID_A for rec in records)

    @_requires_darwin
    def test_budget_counts_each_dispatch_not_one_per_card(self, tmp_path):
        """P2 预算修复：同卡多次派发窗口计数随派发次数增长（非按卡终生一次）。

        判别口径：limit=2 下三次派发，第三次预算探针见 count=2 达限 → 告警放行。
        旧稳定键形态下记录终生一条，计数饱和于 1，恒不达限（失真实证）。"""
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        env["DISPATCH_DAILY_BUDGET_CI_FAILED"] = "2"
        results = [self._run(env) for _ in range(3)]
        log = "".join(_combined(base, r) for r in results)
        for r in results:
            assert r.returncode == 0, log
        assert "budget_over_limit" in log, f"达限须告警: {log[-2000:]}"
        self._assert_comment_dispatched(curl_log, log)
        assert len(self._attempted_records(base)) == 3, "预算告警不得拦派发"

    @_requires_darwin
    def test_legacy_fallback_mints_per_call_uuid_too(self, tmp_path):
        """legacy 回退（ISSUE_ID 未设，生产 hooks.json 路由）同样每次铸造独立键。"""
        base, gh_log, curl_log, env = self._setup(tmp_path)
        env["CI_PROJECT"] = "fixture-project"
        env.pop("ISSUE_ID")
        r1 = self._run(env)
        assert r1.returncode == 0, _combined(base, r1)
        r2 = self._run(env)
        log = _combined(base, r2)
        assert r2.returncode == 0, log
        legacy_uuid = "f023acdf-71d9-4121-8edf-a9ed8c7c05f7"
        ledger = base / "status" / "ledger"
        assert not (ledger / f"{legacy_uuid}.json").exists(), (
            "legacy 硬编码 UUID 不得作为 claim 键（生产 hooks.json 实际路由）"
        )
        records = self._attempted_records(base)
        assert len(records) == 2, f"两次调用须两记录: {records}"
        assert len({rec["linearUuid"] for rec in records}) == 2
        for rec in records:
            assert rec["linearRef"] == legacy_uuid, "legacy 回退 ref 语义=回退卡 UUID 进 linearRef"

    @_requires_darwin
    def test_cross_source_collision_regression_trigger_droid_dedup_intact(self, tmp_path):
        """跨源碰撞回归（#302 P1 实证场景全链）：ci-failed 先记 → trigger-droid
        同卡再派，去重语义不得被 ci-failed 记账破坏——

        旧缺陷形态：ci-failed 借卡 UUID 先 claim（source=ci-failed）→
        trigger-droid 同键 claim first-writer-wins 恒 rc=1，dedup 面被
        外来记录永久废掉。修复后 ci-failed 用独立键，trigger-droid 完整
        走 claim→completed→重投递被 SKIP 的生命周期。"""
        # --- 第 1 步：ci-failed 沙盒先记（真实脚本全链）---
        ci_tmp = tmp_path / "ci"
        ci_tmp.mkdir()
        base, gh_log, curl_log, env = self._setup(ci_tmp)
        env["CI_PROJECT"] = "fixture-project"
        r = self._run(env)
        log = _combined(base, r)
        assert r.returncode == 0, log
        self._assert_comment_dispatched(curl_log, log)
        ci_records = self._attempted_records(base)
        assert len(ci_records) == 1 and ci_records[0]["source"] == "ci-failed"
        ci_key = ci_records[0]["linearUuid"]
        assert ci_key != UUID_A, "前置：ci-failed 键必须已与卡 UUID 隔离"

        # --- 第 2 步：同卡经 trigger-droid 派发（Part B 同构沙盒）---
        sandbox = _make_trigger_sandbox(tmp_path / "td")
        td_base = sandbox["sandbox"]
        # ci-failed 沙盒账本态整体平移（同一账本宿主的两个生命周期阶段）
        shutil.copytree(base / "status", td_base / "status", dirs_exist_ok=True)
        r_td = _run_trigger(sandbox, ref="INFRA-3301", uuid=UUID_A)
        td_log = _td_combined(sandbox, r_td)
        assert _td_wait_marker(sandbox), f"trigger-droid 首派必须放行: {td_log[-2000:]}"
        td_record = read_record(td_base, UUID_A)
        assert td_record and td_record["source"] == "trigger-droid", (
            f"trigger-droid 必须能以卡 UUID 首写记录（不被 ci-failed 记账顶掉）: {td_record}"
        )

        # --- 第 3 步：完成态后同卡重投递 → 账本去重命中（SKIP，零 droid exec）---
        sandbox["marker"].unlink()
        (td_base / "status" / "INFRA-3301.json").write_text(
            json.dumps({"status": "completed", "exitCode": 0})
        )
        r_td2 = _run_trigger(sandbox, ref="INFRA-3301", uuid=UUID_A)
        td_log2 = _td_combined(sandbox, r_td2)
        assert "SKIP: dispatch ledger hit" in td_log2, (
            f"同卡重投递必须被 trigger-droid 自有记录去重命中: {td_log2[-2000:]}"
        )
        assert not sandbox["marker"].exists(), "去重命中必须零 droid exec"
        # ci-failed 的记录未被触碰（append-only 隔离面完整）
        ci_after = read_record(td_base, ci_key)
        assert ci_after and ci_after["source"] == "ci-failed"


# ============================================================================
# poll-releases（launchd 型，链式头：本体分支 + 真源 trigger-release 全链）
# ============================================================================


class TestPollReleasesWiring:
    RELEASES = [{"tag_name": "v9.8.8", "draft": False, "html_url": "https://example.com/v9.8.8"}]

    def _setup(self, tmp_path: Path, *, stub_trigger: Path | None = None):
        base = _sandbox_webhook(tmp_path)
        marker = tmp_path / "droid-launched.marker"
        bin_dir = _stub_droid_bin(tmp_path, marker)
        write_stub(
            bin_dir / "gh",
            "#!/bin/bash\ncat <<'EOF'\n" + json.dumps(self.RELEASES) + "\nEOF\n",
        )
        trigger_ref = stub_trigger or (WEBHOOK_DIR / "trigger-release.sh")
        env = _sandbox_env(
            tmp_path,
            base,
            path_prepend=[bin_dir],
            extra={"TRIGGER_SCRIPT": str(trigger_ref)},
        )
        (base / "locks" / ".poll-bootstrap-done").write_text("")
        return base, marker, env

    def _run(self, env: dict):
        return _run_script(WEBHOOK_DIR / "poll-releases.sh", [], env)

    def _stub_trigger(self, tmp_path: Path, capture: Path) -> Path:
        stub = tmp_path / "trigger-release-stub.sh"
        write_stub(
            stub,
            f'#!/bin/bash\necho "$1|$2|$3" >> "{capture}"\necho "stub-trigger ok" >> '
            f'"{tmp_path / "webhook" / "logs" / "poll-releases.log"}"\n',
        )
        return stub

    @_requires_darwin
    def test_branch_invocation_claims_own_ledger_record(self, tmp_path):
        """poll-releases 本体调用面：stub 下游 → 自身 claim（source=poll-releases）+ 派发照常。"""
        base, marker, env = self._setup(
            tmp_path,
            stub_trigger=self._stub_trigger(tmp_path, tmp_path / "trigger-capture.log"),
        )
        r = self._run(env)
        capture = tmp_path / "trigger-capture.log"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if capture.exists() and "v9.8.8" in capture.read_text():
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _combined(base, r)
        assert capture.exists() and "v9.8.8" in capture.read_text(), (
            f"下游必须被调用: {log[-1500:]}"
        )
        events = read_events(base)
        assert "budget_ok" in log, "DRY_RUN 打印面必须可见 poll 本体 budget_ok"
        attempted = [e for e in events if e["event"] == "dispatch_attempted"]
        assert attempted, "分支调用必须留 dispatch_attempted"
        record = read_record(base, attempted[0]["uuid"])
        assert record and record["source"] == "poll-releases" and record["kind"] == "poll-releases"

    @_requires_darwin
    def test_budget_unreachable_branch_still_invokes(self, tmp_path):
        base, marker, env = self._setup(
            tmp_path,
            stub_trigger=self._stub_trigger(tmp_path, tmp_path / "trigger-capture.log"),
        )
        _block_ledger(base)
        r = self._run(env)
        capture = tmp_path / "trigger-capture.log"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if capture.exists():
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _combined(base, r)
        assert "budget_unavailable" in log
        assert capture.exists() and "v9.8.8" in capture.read_text(), (
            "fail-open：预算不可达不得拦分支调用"
        )

    @_requires_darwin
    def test_over_limit_alerts_and_still_invokes(self, tmp_path):
        base, marker, env = self._setup(
            tmp_path,
            stub_trigger=self._stub_trigger(tmp_path, tmp_path / "trigger-capture.log"),
        )
        _seed_budget_claims(base, "poll-releases", 1, kind="poll-releases")
        env["DISPATCH_DAILY_BUDGET_POLL_RELEASES"] = "1"
        r = self._run(env)
        capture = tmp_path / "trigger-capture.log"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if capture.exists():
                break
            time.sleep(0.5)
        time.sleep(0.5)
        log = _combined(base, r)
        assert "budget_over_limit" in log, f"超限必须留告警: {log[-1500:]}"
        # DRY_RUN=1 下超限遥测只走打印面（lib 契约），下游 capture 是放行实证
        assert capture.exists() and "v9.8.8" in capture.read_text(), "超预算必须放行（拒发即违规）"

    @_requires_darwin
    def test_real_chain_end_to_end_claims_then_launches(self, tmp_path):
        """链式真源全链（沙盒）：poll 本体 + 真源 trigger-release 双 claim + 派发面照常。

        链尾 droid 用宿主 trigger-release-stub（macOS bash 3.2 中文邻接变量展开
        的固有解析怪癖与本次接线无关，stub 化隔离；正路径全链在
        TestTriggerReleaseWiring 已验）。"""
        base, marker, env = self._setup(tmp_path)
        stub = tmp_path / "trigger-release-host-stub.sh"
        write_stub(
            stub,
            "#!/bin/bash\n"
            f'echo "host-stub trigger-release $1" >> "{base / "logs" / "poll-releases.log"}"\n'
            f'touch "{marker}"\n',
        )
        env["TRIGGER_SCRIPT"] = str(stub)
        r = self._run(env)
        log = _combined(base, r)
        assert wait_for(lambda: marker.exists(), timeout=90), f"链尾未到达: {log[-2000:]}"
        events = read_events(base)
        sources = set()
        for e in events:
            if e["event"] == "dispatch_attempted":
                rec = read_record(base, e["uuid"])
                if rec:
                    sources.add(rec["source"])
        assert "poll-releases" in sources, f"poll 本体 claim 缺失: {sources}\n{log[-1500:]}"
        assert "host-stub trigger-release v9.8.8" in log, "真源下游必须被调用"
