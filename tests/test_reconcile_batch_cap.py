#!/usr/bin/env python3
"""TDD tests for GAP-A compensation batch cap (VAL-LEDGER-004 缺口补齐).

背景（mission gap-a-batch-cap / ledger-shadow-week handoff 移交）：
补偿创建循环需要单次运行批次上限（默认 2，与 R1_DAILY_BUDGET 对齐，env 可调），
防止「孤儿积压 + 新去重键面解锁」场景下 Issue.create 突发（若 Mac 路径未封会被
执行——影子周切换前置检查的三项之一，owner 在切强制检查点裁定终值）。

语义：
- 单次运行补偿创建数 ≤ GAP_A_BATCH_CAP（默认 2；非负整数 env 覆盖，非法值回退默认）。
- 超限孤儿本轮不创建：日志留痕（batch cap reached + deferred）+ 批次计数可见
  （batch summary：cap/compensated/deferred/total）。
- 孤儿扫描改为全候选枚举（SKIP_* 不再遮蔽后续孤儿行）：cap 语义要求先枚举全
  部真孤儿再限量消费，否则 cap 只能对「第一个可见孤儿」生效（补偿饥饿的旧
  break 语义回潮）。
- 下个 tick 键面重新判定：deferred ≠ 丢弃（无墓碑语义，卡保持 open）。
- cap 合法域 0–100：超界/巨值回退默认（纯位数校验会放行超 int 上限值，使
  后续 [ -ge ] 比较静默失效 = fail-open）。
- bash 侧复核的注册表命中行必须移出补偿集（#281 双保险语义：只留痕不移除
  = 已锚定镜像照常补偿，幽灵卡工厂回归）。

沙盒基建复用 test_reconcile_dedup_fix（urllib 劫持 + PATH stub，零真实网络）。
默认沙盒恰有两个无锚点孤儿（#900004、#900010），是 cap 边界测试的自然夹具。
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reconcile_dedup_fix import (  # noqa: E402
    REPO_SCRIPT,
    _api_log,
    _build_sandbox,
    _latest_log,
    _run_tick,
)

# 沙盒锚点提取 stub（矛盾构造版）：利用两阶段 env 差异制造 python/bash 键面
# 分裂——python 扫描以 `LINEAR_TERMINAL_TITLES_TMP=... python3 -c` 命令域调用，
# stub 子进程继承该 env；bash 复核裸调用无此 env。#900010 在扫描阶段无锚点
# （孤儿），复核阶段返还 INFRA-900010（∈ LINEAR_REFS live 注册表，模拟 linkback
# 在两阶段间落键）→ 命中路径。#900010 不在 4b 终态关闭循环的候选集
# （INFRA-900001~900004）中，判别无串扰。
EXTRACT_ANCHOR_DISAGREE_STUB = """#!/usr/bin/env python3
import os, re, sys

number = sys.argv[2]
if number == '900010' and 'LINEAR_TERMINAL_TITLES_TMP' not in os.environ:
    print('INFRA-900010')
    sys.exit(0)
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

# ============================================================================
# Part 1 — 静态语义断言（真源脚本源码，可执行语义锚定）
# ============================================================================


class TestBatchCapSemantics:
    """批次上限常量 + 计数留痕的源码锚定。"""

    def test_batch_cap_default_2_and_env_overridable(self):
        """默认 2（与 R1_DAILY_BUDGET 对齐）且经 env 可配置（${VAR:-2} 形态）。"""
        content = REPO_SCRIPT.read_text()
        assert 'GAP_A_BATCH_CAP="${GAP_A_BATCH_CAP:-2}"' in content, (
            "批次上限必须默认 2 且支持 env 覆盖"
        )

    def test_batch_cap_invalid_value_falls_back_to_default(self):
        """非负整数校验：非法值（空串/负号/非数字）回退默认 2（fail-safe）。"""
        content = REPO_SCRIPT.read_text()
        assert re.search(r'case "\$GAP_A_BATCH_CAP" in', content), "缺非负整数校验"
        assert "*[!0-9]*" in content, "缺非数字模式匹配（非法值回退）"

    def test_batch_cap_has_upper_bound_check(self):
        """cap 合法域必须有上界：超界值回退默认，而非放行（fail-open 防护）。

        纯 digit-ness 校验放行超 int 上限值（如 20 位数字），后续
        [ "$GAP_A_COMPENSATED" -ge "$GAP_A_BATCH_CAP" ] 对巨值返回 rc=2，
        被 if 视为 false → cap 永不触发、全部孤儿单轮创建（静默 fail-open）。
        """
        content = REPO_SCRIPT.read_text()
        assert re.search(r"GAP_A_BATCH_CAP=.*out of range", content, re.S), (
            "缺上界校验分支（超界回退默认）"
        )
        assert re.search(r"-gt 3", content) or re.search(r"-gt 100", content), (
            "缺数值上界（巨值必须在校验段回退，不得流入比较表达式）"
        )

    def test_orphan_scan_enumerates_all_candidates(self):
        """孤儿扫描全候选枚举：无锚点孤儿行输出后不得 break（SKIP_* 不遮蔽后续孤儿）。

        cap 是「枚举全集后的限量消费」；若扫描在第一个孤儿处 break，
        cap 只对第一个可见孤儿生效，与批次上限语义不符。
        """
        content = REPO_SCRIPT.read_text()
        orphan_line = r"print(f'{number}\\t{title}')"
        assert orphan_line in content, "孤儿行输出缺失（扫描输出契约被破坏）"
        assert re.search(re.escape(orphan_line) + r"\n\s*break", content) is None, (
            "孤儿行输出后禁止 break（全候选枚举是批次上限的前提）"
        )

    def test_batch_summary_logged_per_run(self):
        """单次运行的批次计数必须可观测：summary 行含 cap/compensated/deferred/total。"""
        content = REPO_SCRIPT.read_text()
        assert re.search(r"log \"GAP-A: batch summary — ", content), "缺批次 summary 日志"
        assert "batch summary — cap=" in content
        assert "compensated=" in content
        assert "deferred=" in content
        assert "total_orphans=" in content


# ============================================================================
# Part 2 — 沙盒行为测试（stub 网络；观测锚定沙盒日志/创建记录）
# ============================================================================


class TestBatchCapSandbox:
    """默认沙盒两个无锚点孤儿（#900004、#900010）：cap 边界的天然夹具。"""

    def test_orphans_within_cap_all_compensated(self, tmp_path):
        """cap=2（默认）恰有两个孤儿：全部补偿（枚举生效的证据）+ summary 2/2。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "orphan detected — GitHub Issue #900004" in log, log
        assert "orphan detected — GitHub Issue #900010" in log, log
        api = _api_log(sandbox)
        assert api.count("issueCreate:") == 2, f"cap=2 内孤儿应全部补偿: {api}"
        assert "created Linear Issue INFRA-900021" in log
        assert "created Linear Issue INFRA-900022" in log
        assert "GAP-A: batch summary — cap=2 compensated=2 deferred=0 total_orphans=2" in log, log

    def test_batch_cap_1_defers_second_orphan(self, tmp_path):
        """cap=1：仅第一个孤儿创建；第二个超限不创建（日志留痕+计数）。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox, {"GAP_A_BATCH_CAP": "1"})
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "orphan detected — GitHub Issue #900004" in log, log
        api = _api_log(sandbox)
        assert api.count("issueCreate:") == 1, f"cap=1 只允许一次补偿创建: {api}"
        assert "created Linear Issue INFRA-900021" in log
        assert "INFRA-900022" not in api, "超限孤儿不得创建"
        # 超限留痕：日志措辞 + 计数可见
        assert "batch cap 1 reached — GitHub Issue #900010 deferred" in log, log
        assert "GAP-A: batch progress 1/1 compensated this run" in log, log
        assert "GAP-A: batch summary — cap=1 compensated=1 deferred=1 total_orphans=2" in log, log

    def test_batch_cap_zero_records_only(self, tmp_path):
        """cap=0：零创建，孤儿全部记录留痕（deferred 计数=全部孤儿）。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox, {"GAP_A_BATCH_CAP": "0"})
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "orphan detected" not in log, "cap=0 不得进入补偿路径"
        api = _api_log(sandbox)
        assert "issueCreate" not in api, "cap=0 不得创建任何 Linear 卡"
        assert "batch cap 0 reached — GitHub Issue #900004 deferred" in log, log
        assert "batch cap 0 reached — GitHub Issue #900010 deferred" in log, log
        assert "GAP-A: batch summary — cap=0 compensated=0 deferred=2 total_orphans=2" in log, log


class TestBatchCapRangeFallback:
    """cap 超界值回退默认（PRRT P3：纯位数校验的 fail-open 防护，行为级）。"""

    def test_batch_cap_huge_value_falls_back_to_2(self, tmp_path):
        """20 位巨值（超 int 上限）：回退默认 2，cap 正常生效，不 fail-open。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox, {"GAP_A_BATCH_CAP": "99999999999999999999"})
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert (
            "WARNING: GAP_A_BATCH_CAP='99999999999999999999' out of range (max 100), falling back to default 2"
            in log
        ), log
        api = _api_log(sandbox)
        # 巨值若流入比较表达式，[ -ge ] rc=2 被 if 视为 false → 两个孤儿全创建。
        # 正确行为：回退 cap=2，恰好两个孤儿全部补偿。
        assert api.count("issueCreate:") == 2, f"应回退默认 cap=2: {api}"
        assert "GAP-A: batch summary — cap=2 compensated=2 deferred=0 total_orphans=2" in log, log

    def test_batch_cap_invalid_value_runtime_fallback(self, tmp_path):
        """非数字值（'abc'）：运行时回退默认 2（不止源码断言，落行为验证）。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox, {"GAP_A_BATCH_CAP": "abc"})
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "WARNING: GAP_A_BATCH_CAP='abc' invalid, falling back to default 2" in log, log
        api = _api_log(sandbox)
        assert api.count("issueCreate:") == 2, f"应回退默认 cap=2: {api}"

    def test_batch_cap_101_falls_back(self, tmp_path):
        """101（越上界最小整数）：回退默认 2。"""
        sandbox = _build_sandbox(tmp_path)
        r = _run_tick(sandbox, {"GAP_A_BATCH_CAP": "101"})
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "out of range (max 100)" in log, log
        assert "GAP-A: batch summary — cap=2 compensated=2 deferred=0 total_orphans=2" in log, log


class TestBashSideRegistryHitRemoval:
    """bash 侧复核的注册表命中行必须移出补偿集（#281 双保险语义回归防护）。

    python 判定 #900010 无锚点（孤儿），bash 复核时锚点已存在且命中 live
    注册表（LINEAR_REFS 含 INFRA-900010）：等价 SKIP——该行必须被移除，否则
    已锚定镜像照常补偿 = 幽灵卡工厂回归。
    """

    def _disagree_sandbox(self, tmp_path):
        """默认沙盒 + 矛盾锚点 stub：#900010 扫描无锚点、复核有锚点。"""
        sandbox = _build_sandbox(tmp_path)
        stub = sandbox / "extract_anchor.py"
        stub.write_text(EXTRACT_ANCHOR_DISAGREE_STUB)
        stub.chmod(0o755)
        return sandbox

    def test_registry_hit_orphan_not_compensated(self, tmp_path):
        """注册表命中的孤儿行被移出补偿集：#900010 零创建，真孤儿 #900004 照常补偿。

        回归态（PRRT P1）：命中分支只留痕不移除 → #900010 照常 issueCreate
        （幽灵卡工厂）。
        """
        sandbox = self._disagree_sandbox(tmp_path)
        r = _run_tick(sandbox)
        assert r.returncode == 0, f"tick failed: {r.stderr[-3000:]}"
        log = _latest_log(sandbox, r)
        assert "bash-side dedup key hit, skip compensation" in log, (
            f"bash 复核必须命中 #900010: {log}"
        )
        api = _api_log(sandbox)
        assert api.count("issueCreate:") == 1, f"仅真孤儿 #900004 补偿: {api}"
        assert "created Linear Issue INFRA-900021" in log, log
        assert "INFRA-900022" not in api, "注册表命中行不得补偿（幽灵卡回归）"
        assert "orphan detected — GitHub Issue #900010" not in log, log
        # 批次账本一致性：被移除的行不计入 total_orphans（PRT OU69 可观测性）
        assert "GAP-A: batch summary — cap=2 compensated=1 deferred=0 total_orphans=1" in log, log

    def test_registry_hit_branch_removes_line(self):
        """静态语义锚定：注册表命中分支必须 continue（行移出补偿集）。"""
        content = REPO_SCRIPT.read_text()
        # 命中分支：log 之后紧跟 continue（移出行，等价 SKIP）
        assert re.search(r'bash-side dedup key hit, skip compensation"\n\s+continue', content), (
            "注册表命中分支必须移出该行（continue），只留痕不移除=幽灵卡回归"
        )
        # GNU-only 后置过滤不得回归：剔除必须逐行重建
        assert "grep -vxF" not in content, (
            "BSD grep 下 -vxF 多行 pattern 静默失配（fail-open），禁止回归"
        )
        assert "GAP_A_ORPHANS_REBUILT" in content, "缺逐行重建剔除路径"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
