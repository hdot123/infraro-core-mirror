"""M3 声明式镜像清单（.evolution/mirrors.toml）测试（VAL-MIRROR-001）。

契约：
- 清单内文件对且当前字节一致 → check_duplicates 跳过 DUPLICATE 检测
  （自包含分发副本的合法重复，suppress.json 不再登记）
- guard 失效（任一副本漂移）→ 字节比较失败 → 豁免失效，检测自动恢复
  （「带保险的豁免」：清单不是无条件白名单）
- 清单缺失/损坏/条目非法 → fail-open 回到全量检测（绝不静默扩大豁免面）
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.security

from infra_core.packs.memory.hygiene import (
    MirrorPair,
    check_duplicates,
    extract_functions_for_duplicate_check,
    load_mirrors,
    scan_directory,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 与既有 test_duplicate_cross_file_detection 同构的样本函数体（≥10 行，AST 一致）
SAMPLE_FUNC = """
def process_data(data):
    result = []
    for item in data:
        if item > 0:
            result.append(item * 2)
        elif item < 0:
            result.append(item * -1)
        else:
            result.append(0)
    return result
"""

MIRROR_MANIFEST = """\
[[mirrors]]
path_a = "pkg/a.py"
path_b = "pkg/b.py"
guard_test = "tests/test_some_guard.py::test_copy_matches_engine_copy"
"""


def _seed_mirror_tree(tmp_path: Path) -> None:
    """构造镜像对 + 清单的最小仓库树。"""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(SAMPLE_FUNC, encoding="utf-8")
    (tmp_path / "pkg" / "b.py").write_text(SAMPLE_FUNC, encoding="utf-8")
    (tmp_path / ".evolution").mkdir()
    (tmp_path / ".evolution" / "mirrors.toml").write_text(MIRROR_MANIFEST, encoding="utf-8")


def _duplicate_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    return [f for f in findings if f["rule_id"] == "CODE_HYGIENE_DUPLICATE_BLOCK"]


class TestLoadMirrors:
    def test_missing_manifest_returns_empty(self, tmp_path: Path) -> None:
        assert load_mirrors(tmp_path) == []

    def test_malformed_toml_returns_empty(self, tmp_path: Path) -> None:
        """清单损坏 → fail-open 返回空（回到全量检测，不 raise）。"""
        (tmp_path / ".evolution").mkdir()
        (tmp_path / ".evolution" / "mirrors.toml").write_text("not [ valid toml", encoding="utf-8")
        assert load_mirrors(tmp_path) == []

    def test_schema_parses_paths_and_guard(self, tmp_path: Path) -> None:
        _seed_mirror_tree(tmp_path)
        mirrors = load_mirrors(tmp_path)
        assert mirrors == [
            MirrorPair(
                path_a="pkg/a.py",
                path_b="pkg/b.py",
                guard_test="tests/test_some_guard.py::test_copy_matches_engine_copy",
            )
        ]


class TestMirrorSkipAndGuardRestore:
    def test_manifest_hit_identical_pair_skipped(self, tmp_path: Path) -> None:
        """清单命中 + 字节一致 → 不再产生 DUPLICATE finding。"""
        _seed_mirror_tree(tmp_path)
        findings = scan_directory(tmp_path, tmp_path)
        assert _duplicate_findings(findings) == []

    def test_no_manifest_still_detects(self, tmp_path: Path) -> None:
        """无清单 → 同一对照旧检出（豁免必须由清单显式声明）。"""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text(SAMPLE_FUNC, encoding="utf-8")
        (tmp_path / "pkg" / "b.py").write_text(SAMPLE_FUNC, encoding="utf-8")
        findings = scan_directory(tmp_path, tmp_path)
        dup = _duplicate_findings(findings)
        assert len(dup) == 1
        assert "pkg/a.py" in dup[0]["location"]
        assert "pkg/b.py" in dup[0]["location"]

    def test_guard_drift_restores_detection(self, tmp_path: Path) -> None:
        """guard 失效（副本漂移）→ 豁免失效，检测自动恢复。"""
        _seed_mirror_tree(tmp_path)
        assert _duplicate_findings(scan_directory(tmp_path, tmp_path)) == []
        drifted = SAMPLE_FUNC.replace("result.append(item * 2)", "result.append(item * 3)")
        (tmp_path / "pkg" / "b.py").write_text(drifted, encoding="utf-8")
        dup = _duplicate_findings(scan_directory(tmp_path, tmp_path))
        assert len(dup) == 1  # 相似度仍 ≥ 阈值 → 检测恢复
        assert "pkg/a.py" in dup[0]["location"]

    def test_manifest_entry_with_missing_files_no_crash(self, tmp_path: Path) -> None:
        """清单条目指向不存在的文件 → 不误伤其他真实重复对、不 crash。"""
        (tmp_path / ".evolution").mkdir()
        (tmp_path / ".evolution" / "mirrors.toml").write_text(
            '[[mirrors]]\npath_a = "ghost/a.py"\npath_b = "ghost/b.py"\n',
            encoding="utf-8",
        )
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text(SAMPLE_FUNC, encoding="utf-8")
        (tmp_path / "pkg" / "b.py").write_text(SAMPLE_FUNC, encoding="utf-8")
        dup = _duplicate_findings(scan_directory(tmp_path, tmp_path))
        assert len(dup) == 1


class TestCheckDuplicatesMirrorParam:
    def test_direct_call_with_mirrors_skips_identical(self, tmp_path: Path) -> None:
        """check_duplicates 直调：带清单跳过、不带清单保持旧语义。"""
        _seed_mirror_tree(tmp_path)
        funcs = extract_functions_for_duplicate_check(
            tmp_path / "pkg" / "a.py", tmp_path
        ) + extract_functions_for_duplicate_check(tmp_path / "pkg" / "b.py", tmp_path)
        assert check_duplicates(funcs, repo_root=tmp_path, mirrors=load_mirrors(tmp_path)) == []
        assert len(check_duplicates(funcs)) == 1

    def test_mirrors_without_repo_root_rejected(self, tmp_path: Path) -> None:
        """mirrors 依赖 repo_root 做字节比较——缺参即配置错误，显式拒绝。"""
        _seed_mirror_tree(tmp_path)
        funcs = extract_functions_for_duplicate_check(
            tmp_path / "pkg" / "a.py", tmp_path
        ) + extract_functions_for_duplicate_check(tmp_path / "pkg" / "b.py", tmp_path)
        with pytest.raises(ValueError, match="repo_root"):
            check_duplicates(funcs, mirrors=load_mirrors(tmp_path))


class TestRealRepoManifest:
    def test_publish_findings_pair_declared(self) -> None:
        """真仓：mirrors.toml 已声明 publish_findings 镜像对且带 guard_test。"""
        mirrors = load_mirrors(REPO_ROOT)
        pairs = {frozenset((m.path_a, m.path_b)) for m in mirrors}
        assert (
            frozenset(
                (
                    "src/infra_core/engine/droid_review/publish_findings.py",
                    "actions/droid-review-aggregate/publish_findings.py",
                )
            )
            in pairs
        )
        for m in mirrors:
            assert m.guard_test, f"镜像对 {m.path_a} <-> {m.path_b} 缺 guard_test"

    def test_real_pair_detection_skipped(self) -> None:
        """真仓：publish_findings 两副本的全部同名函数对经清单豁免。"""
        funcs = extract_functions_for_duplicate_check(
            REPO_ROOT / "src/infra_core/engine/droid_review/publish_findings.py", REPO_ROOT
        ) + extract_functions_for_duplicate_check(
            REPO_ROOT / "actions/droid-review-aggregate/publish_findings.py", REPO_ROOT
        )
        assert len(funcs) > 0
        assert check_duplicates(funcs, repo_root=REPO_ROOT, mirrors=load_mirrors(REPO_ROOT)) == []

    def test_real_pair_via_scan_directory(self) -> None:
        """真仓：scan_directory 生产路径下镜像对零 DUPLICATE finding。"""
        findings = scan_directory(REPO_ROOT / "src/infra_core/engine", REPO_ROOT)
        dup = _duplicate_findings(findings)
        assert dup == [], f"镜像豁免失效，残留 {len(dup)} 条: {dup[:2]}"
