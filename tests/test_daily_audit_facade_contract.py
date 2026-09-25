"""daily_audit 门面契约测试（v3 步骤 5 拆分，收尾 feature 常驻锁定）。

背景：daily_audit.py 原为 2280 行单体，已拆为 5 个平铺私有模块 + 门面，门面只做
显式 re-export。拆分的等价性证据（顶层项逐字节一致、两 CLI 入口 --help md5 一致）
由一次性校验脚本产出，仓内无常驻锁——今后若再动 packs/memory 结构，门面漏名或重新
长回逻辑不会被 CI 直接拦截。本文件把该证据常驻化：

1. ``__all__`` 全集恰为 38 名且全部在门面命名空间可解析（``import *`` 语义锚点）；
2. 下划线别名 ``_now_iso_local`` / ``_sha256_file`` / ``_run_ssh`` 全名可达且可调用
   （``import *`` 拿不到，既有测试与调用方依赖直接属性访问）；
3. 门面文件 AST 中无 ``def`` / ``class`` 节点——纯 re-export，逻辑一律下沉；
4. 门面顶层语句仅限 docstring / import / ``__all__`` 赋值 / ``__main__`` guard，
   导入来源恰为 5 个私有兄弟模块，且导入名与来源模块是同一对象（无中间包装）。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from infra_core.packs.memory import daily_audit

pytestmark = [pytest.mark.schema, pytest.mark.business_policy]

PACKAGE = "infra_core.packs.memory"
FACADE_SOURCE_PATH = Path(daily_audit.__file__)

# 拆前门面 __all__ 全集（公开契约）：只允许有意变更，并同步本清单与迁移记录。
EXPECTED_ALL: tuple[str, ...] = (
    # 常量
    "SYSTEM_DIR",
    "MANIFEST_FILENAME",
    "MANIFEST_PATH_REL",
    "KB_UNSIGNED_WHITELIST",
    "GLOBAL_KB_DOMAINS",
    "GLOBAL_KB_SKIP",
    "LARGE_SQL_THRESHOLD",
    "DATABASE_FILE_SUFFIXES",
    "LARK_NOTIFY_ENV",
    "LARK_NOTIFY_TIMEOUT",
    "SSH_TIMEOUT",
    "SSH_CONNECT_TIMEOUT",
    "TCP_TIMEOUT",
    "HTTP_TIMEOUT",
    # 工具函数
    "now_iso",
    "sha256_file",
    "_strip_frontmatter",
    "_normalize_for_compare",
    "_make_violation",
    "_shell_quote",
    # 项目解析 / 指纹
    "load_registered_projects",
    "build_global_kb_fingerprints",
    "is_memory_core_source_repo",
    # 检查 1-5
    "check_manifest_integrity",
    "check_unsigned_files",
    "check_global_residue",
    "check_large_or_db_files",
    "check_version_consistency",
    # 基础设施
    "check_infrastructure",
    "check_server",
    "check_database",
    "check_ssh_reachable",
    "check_disk_space",
    # 报告 / 通知
    "build_report",
    "write_report",
    "notify_via_lark",
    # 编排 / CLI
    "audit_project",
    "main",
)

# 5 个私有兄弟模块：门面的唯一导入来源（依赖方向 base ← checks/infra ← report ← cli）。
PRIVATE_MODULES = {
    "_daily_base",
    "_daily_checks",
    "_daily_cli",
    "_daily_infra",
    "_daily_report",
}

# 不在 __all__ 中的向后兼容下划线别名（拆分时按原名保留）。
UNDERSCORE_ALIASES = ("_now_iso_local", "_sha256_file", "_run_ssh")

# 门面顶层允许的语句类型：docstring / import / __all__ 赋值 / __main__ guard。
ALLOWED_TOP_LEVEL_NODES = (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign, ast.If)


def _facade_tree() -> ast.Module:
    """解析门面文件 AST（锁源码形态，不依赖运行时属性）。"""
    return ast.parse(FACADE_SOURCE_PATH.read_text(encoding="utf-8"))


def _facade_import_map() -> dict[str, str]:
    """门面顶层 re-export 映射：导出名 → 来源私有模块名（跳过 __future__）。"""
    mapping: dict[str, str] = {}
    for node in _facade_tree().body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if module == "__future__":
            continue
        for alias in node.names:
            mapping[alias.asname or alias.name] = module
    return mapping


class TestFacadeExportSurface:
    """导出面契约：__all__ 38 名全集 + 下划线别名全名可达。"""

    def test_all_is_frozen_public_contract(self):
        """__all__ 逐名等于拆前全集（38 名，无重复、无增删）。"""
        assert tuple(daily_audit.__all__) == EXPECTED_ALL, (
            f"__all__ 漂移：新增 {sorted(set(daily_audit.__all__) - set(EXPECTED_ALL))}，"
            f"缺失 {sorted(set(EXPECTED_ALL) - set(daily_audit.__all__))}"
        )
        assert len(set(daily_audit.__all__)) == len(EXPECTED_ALL), "__all__ 存在重复名"

    def test_every_all_name_resolves_in_facade_namespace(self):
        """每个 __all__ 名都能从门面解析（import * 与 hasattr 依赖的锚点）。"""
        missing = [name for name in EXPECTED_ALL if not hasattr(daily_audit, name)]
        assert missing == [], f"门面缺失 __all__ 名：{missing}"

    def test_underscore_aliases_present_and_callable(self):
        """3 个下划线别名全名可达且可调用（import * 拿不到，须显式 re-export）。"""
        missing = [name for name in UNDERSCORE_ALIASES if not hasattr(daily_audit, name)]
        assert missing == [], f"门面缺失下划线别名：{missing}"
        not_callable = [
            name for name in UNDERSCORE_ALIASES if not callable(getattr(daily_audit, name))
        ]
        assert not_callable == [], f"下划线别名不可调用：{not_callable}"

    def test_underscore_aliases_not_in_all(self):
        """向后兼容别名不进 __all__（避免改变 import * 的既有语义）。"""
        leaked = sorted(set(UNDERSCORE_ALIASES) & set(daily_audit.__all__))
        assert leaked == [], f"下划线别名不得进 __all__：{leaked}"


class TestFacadeIsPureReexport:
    """结构契约：门面只 re-export，逻辑一律下沉到 5 个私有模块。"""

    def test_no_function_or_class_definitions_in_facade(self):
        """门面文件 AST 内无 def/class（纯 re-export 的结构底线）。"""
        offenders = [
            f"{type(node).__name__} {node.name}"
            for node in ast.walk(_facade_tree())
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        assert offenders == [], f"门面出现 def/class（应下沉到 _daily_* 模块）：{offenders}"

    def test_top_level_statements_are_reexport_only(self):
        """顶层语句仅限 docstring / import / __all__ 赋值 / __main__ guard。"""
        unexpected = [
            f"{type(node).__name__}@L{node.lineno}"
            for node in _facade_tree().body
            if not isinstance(node, ALLOWED_TOP_LEVEL_NODES)
        ]
        assert unexpected == [], f"门面顶层出现非 re-export 语句：{unexpected}"

    def test_imports_come_only_from_private_sibling_modules(self):
        """门面导入来源恰为 5 个私有兄弟模块（每个至少贡献一个名字）。"""
        sources = set(_facade_import_map().values())
        assert sources == PRIVATE_MODULES, (
            f"多出来源 {sorted(sources - PRIVATE_MODULES)}，"
            f"未贡献任何名字 {sorted(PRIVATE_MODULES - sources)}"
        )

    def test_reexported_names_are_same_objects_as_source(self):
        """导入名与来源模块是同一对象（无中间包装/重绑定，patch 目标可预测）。"""
        mismatched: list[str] = []
        for name, module in _facade_import_map().items():
            source = importlib.import_module(f"{PACKAGE}.{module}")
            if getattr(daily_audit, name, None) is not getattr(source, name, None):
                mismatched.append(f"{name} (来源 {module})")
        assert mismatched == [], f"门面名与来源模块非同一对象：{mismatched}"

    def test_main_guard_preserved(self):
        """保留 __main__ guard（python -m 入口兼容，接缝契约依赖）。"""
        guards = [node for node in _facade_tree().body if isinstance(node, ast.If)]
        assert len(guards) == 1, f"__main__ guard 数量异常：{len(guards)}"
        test = guards[0].test
        assert isinstance(test, ast.Compare), "guard 条件形态异常"
        assert isinstance(test.left, ast.Name) and test.left.id == "__name__"
        assert isinstance(test.comparators[0], ast.Constant)
        assert test.comparators[0].value == "__main__"
        assert any(isinstance(node, ast.Call) for node in ast.walk(guards[0])), (
            "guard 内未见 main() 调用"
        )
