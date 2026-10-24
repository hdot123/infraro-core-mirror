"""Suppress drift guard for bundled-copy duplicate-block suppressions (INFRA-691).

抑制条目的 location 含行号与 md5 截断哈希——INFRA-670 判定其结构性弱点是
「engine 侧行漂移即失效」。本守卫从**实时源码**推导期望的抑制 key（与生产
管线同路径：hygiene 的 FuncInfo 行号 + evolution_adapters 的
sanitize_structured_field 归一化），与 .evolution/suppress.json 实际登记对账：

- 函数行号漂移 / 改名 / 删除 → 推导 key 与登记 key 失配 → 测试失败，
  强制同步更新 suppress.json（而非静默失效、下个 tick 重新滴灌 issue）
- 反向对账：登记的 DUPLICATE_BLOCK 条目不得超出实时推导集（防失效残留）

NF-1（VAL-FINAL-002 终判修复，2026-10-15 owner 批复）：DUPLICATE 的去重键
归一化为排序文件对（无行号）——行漂移与 pair 顺序互换不再产生新键（#305←#291
等 4 例重铸根因）。抑制登记同步改为文件对形态（12 条行号级条目 → 3 条文件对
条目）；推导侧与生产管线同构：文件对排序 + _normalize_dedup_location 折叠，
行漂移不再让守卫误报失配。

M3（VAL-MIRROR-001/002）边界划分：publish_findings 镜像对的 30 条抑制条目已
迁出 suppress.json，改由 .evolution/mirrors.toml 声明式豁免承担（hygiene
check_duplicates 对清单内字节一致文件对跳过检测，漂移即恢复）。manifest 豁免
行为由 tests/test_hygiene_mirrors.py 锁定；本守卫额外断言镜像对条目**不得**
回流 suppress.json（抑制与豁免双轨重复登记会让清单失效检测被静默吞掉）。
governance 与 test file 对是真重复（判定等价/同源复刻，非字节一致，清单罩不
住），继续以 expires 2027-06-30 + reason 留案。

双副本背景：composite action 在 caller 仓库运行无法 import src/，副本必须
物理存在（自包含分发）；bytes-equal 契约见 TestBundledPublishCopy（publish_findings）
与 TestActionScriptEquivalence（governance_check，判定等价而非字节一致，
两文件行号不同：action L35 / engine L42）。
"""

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parent.parent
_engine_dir = REPO_ROOT / "src" / "infra_core" / "engine"
sys.path.insert(0, str(_engine_dir))

from evolution_adapters import sanitize_structured_field  # noqa: E402
from evolution_scanner import load_suppressions  # noqa: E402

RULE_ID = "CODE_HYGIENE_DUPLICATE_BLOCK"

# M3: publish_findings 文件对已迁移至 .evolution/mirrors.toml（不再走 suppress）。
# 保留对账所需标识：迁移前的 suppress location 前缀形态（sanitize 截断+md5）。
MIRROR_PAIR_FILES = (
    "src/infra_core/engine/droid_review/publish_findings.py",
    "actions/droid-review-aggregate/publish_findings.py",
)

# governance 文件对只有一个超阈值的同名函数 _match_any（98% 相似）
GOVERNANCE_FUNCTIONS = ("_match_any",)

# (engine 相对路径, action 相对路径, 函数名元组)
# M3: 仅剩真重复对——governance 判定等价对 + 两个同源复刻测试文件对
BUNDLED_PAIRS = (
    (
        "src/infra_core/governance.py",
        "actions/governance-check/governance_check.py",
        GOVERNANCE_FUNCTIONS,
    ),
    # Test file pairs (同源复刻对，共享测试辅助函数)
    # INFRA-691: test_extracted_guard_adversarial_matrix 是 class method，需遍历 ast.walk 覆盖
    (
        "tests/test_sweep_pipeline_fix.py",
        "tests/test_real_shell_adversarial_matrix.py",
        (
            "_extract_pipeline",
            "_extract_case_guard",
            "_run_extracted_guard",
            "test_extracted_guard_adversarial_matrix",
        ),
    ),
    (
        "tests/test_trigger_release_contract.py",
        "tests/test_trigger_ci_droid_routing.py",
        ("_make_env",),
    ),
)


def _function_def_lines(source: str, names: tuple[str, ...]) -> dict[str, int]:
    """Parse source and return {function_name: def line} for top-level and class methods.

    使用 ast.walk 遍历全部节点（包栝 class methods），与生产扫描器同构。
    """
    tree = ast.parse(source)
    lines = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            lines[node.name] = node.lineno
    return lines


def _expected_suppression_keys() -> set[tuple[str, str]]:
    """Derive expected suppression keys from live source (production pipeline shape).

    NF-1 归一化后的生产管线键形态：hygiene 的 location 原文
    ``{a.file}::L{a.line_no} <-> {b.file}::L{b.line_no}``（行漂移/遍历顺序
    敏感），经 _normalize_dedup_location 折叠为排序文件对（无行号）→
    sanitize_structured_field 兜底。推导侧同构，行漂移不再误报失配。
    """
    keys: set[tuple[str, str]] = set()
    for engine_rel, action_rel, names in BUNDLED_PAIRS:
        engine_lines = _function_def_lines(
            (REPO_ROOT / engine_rel).read_text(encoding="utf-8"), names
        )
        action_lines = _function_def_lines(
            (REPO_ROOT / action_rel).read_text(encoding="utf-8"), names
        )
        for name in names:
            # 函数被改名/删除时立即失败（推导不出 = 无法对账）
            assert name in engine_lines, f"{engine_rel} 缺少函数 {name}（改名请同步 suppress.json）"
            assert name in action_lines, f"{action_rel} 缺少函数 {name}（改名请同步 suppress.json）"
            # 行漂移不敏感：键折叠到文件对（与 suppress.json 登记形态一致）
            pair = " <-> ".join(sorted((engine_rel, action_rel)))
            keys.add((RULE_ID, sanitize_structured_field(pair)))
    return keys


class TestBundledCopySuppressionDriftGuard:
    def test_suppressions_cover_live_derived_keys(self) -> None:
        """正向：实时推导的抑制 key 必须全部已登记（行漂移/改名即失败）。"""
        registered = {
            (s.get("rule_id"), s.get("location"))
            for s in load_suppressions(REPO_ROOT)
            if s.get("rule_id") == RULE_ID
        }
        expected = _expected_suppression_keys()
        missing = expected - registered
        assert not missing, (
            "CODE_HYGIENE_DUPLICATE_BLOCK 抑制条目与实时源码失配（函数行号漂移或改名）："
            f"{sorted(missing)}——请同步更新 .evolution/suppress.json 与 "
            "tests/test_evolution_scanner.py 的 expected 集合"
        )

    def test_no_stale_bundled_copy_suppressions(self) -> None:
        """反向：登记的 DUPLICATE_BLOCK 条目不得超出实时推导集（防失效残留）。"""
        registered = {
            (s.get("rule_id"), s.get("location"))
            for s in load_suppressions(REPO_ROOT)
            if s.get("rule_id") == RULE_ID
        }
        expected = _expected_suppression_keys()
        stale = registered - expected
        assert not stale, (
            f"发现失效的 DUPLICATE_BLOCK 抑制条目（源码已变，条目残留）: {sorted(stale)}"
        )

    def test_mirror_pair_not_double_registered(self) -> None:
        """M3 防回潮：镜像对不得同时登记 suppress（豁免已由 mirrors.toml 承担）。

        suppress.json 的 location 是 sanitize 截断+md5 形态，无法直接反推文件
        名，因此反向检查：登记条目总数不得超过实时推导的真重复对数——镜像对
        任何回流条目都会撑破总数。
        """
        registered = [s for s in load_suppressions(REPO_ROOT) if s.get("rule_id") == RULE_ID]
        expected = _expected_suppression_keys()
        assert len(registered) <= len(expected), (
            f"suppress.json DUPLICATE_BLOCK 条目数 {len(registered)} 超过真重复推导集 "
            f"{len(expected)}——publish_findings 镜像对禁止回流 suppress.json "
            "（豁免归 .evolution/mirrors.toml，见 docs/roadmap/bundled-copy-dedup-backlog.md）"
        )

    def test_mirror_manifest_declares_pair_with_guard(self) -> None:
        """M3：mirrors.toml 必须声明 publish_findings 对且带 guard_test。"""
        from infra_core.packs.memory.hygiene import load_mirrors

        mirrors = load_mirrors(REPO_ROOT)
        pairs = {frozenset((m.path_a, m.path_b)) for m in mirrors}
        assert frozenset(MIRROR_PAIR_FILES) in pairs
        for m in mirrors:
            assert m.guard_test, f"镜像对 {m.path_a} <-> {m.path_b} 缺 guard_test"
