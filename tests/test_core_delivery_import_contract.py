"""Core↛Delivery 反向 import AST 契约锁（audit-defense-hardening）。

红线（权威：`docs/architecture.md` §1.1「单向依赖原则」+ `src/infra_core/engine/__init__.py`
docstring）：``Delivery → Evolution Core`` 允许，**严禁 Core 反向依赖 Delivery**。
物理寄居 ``engine/`` 的三个 Delivery 件——``droid_review/``（含子模块）、
``anchor_gate.py``、``extract_anchor.py``——不得成为 Evolution Core 模块的 import 目标。

此前该红线只有文档声明（架构文档 + 包 docstring）、零测试强制：寄居件是搬不走的
（会撕裂 suppress 指纹 / gate0 豁免 / 消费锚定 / 字节锁），但「Core 不许反向依赖」
这一条一旦被某次改动静默破坏，没有任何 CI 面会红。本文件用真 AST 把它升级为测试强制：

* 7 个 Evolution Core 文件（``engine/__init__.py`` + 5 个 ``evolution_*`` + ``version_sync``）
  扫描真实源码，断言零违例（当前树基线）；
* 同一 checker 喂 3 目标 × 2 import 形式（bare / from，各含点号子模块前缀与同包相对形式）
  的合成源码，断言全部检出且报出对应模块名——锁有牙齿，不是恒真空集。

实现取真 AST 而非正则：``ast.parse`` 直接读源码文本，不 import 被测模块（engine 走
sys.path 裸名 import，import 会引入消费仓同名模块碰撞面），也不引入 deptry 面。
先例：``tests/test_daily_audit_facade_contract.py``（import 映射）、
``tests/test_suppress_drift_guard.py``（``ast.walk`` 全节点遍历）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.business_policy

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "src" / "infra_core" / "engine"

# Evolution Core 方（禁止反向 import Delivery 的 7 个文件）：engine/__init__.py 包面 +
# evolution_* 核心模块 + version_sync 版本同步面（消费仓 M3 缝合调用，同属 Core）。
CORE_MODULE_FILES: tuple[str, ...] = (
    "__init__.py",
    "evolution_scanner.py",
    "evolution_heartbeat.py",
    "evolution_self_audit.py",
    "evolution_utils.py",
    "evolution_adapters.py",
    "version_sync.py",
)

# Delivery 方（物理寄居 engine/ 的交付件）：禁止成为 Core 的 import 目标。
FORBIDDEN_DELIVERY_TARGETS: tuple[str, ...] = (
    "droid_review",
    "anchor_gate",
    "extract_anchor",
)

# 合成违例源码的 import 形式矩阵：bare ``import X`` / ``import X.y`` 与
# ``from X import …`` / ``from X.y import …`` 两种节点全覆盖；另加同包相对形式
# （``from . import X`` / ``from .X.y import …``）——engine 内部就是裸名 import 风格，
# 相对形式同样构成反向依赖。
IMPORT_FORM_MATRIX: dict[str, str] = {
    "bare-import": "import {target}\n",
    "bare-import-submodule": "import {target}.submodule\n",
    "from-import": "from {target} import thing\n",
    "from-import-submodule": "from {target}.submodule import thing\n",
    "relative-import": "from . import {target}\n",
    "relative-import-submodule": "from .{target}.submodule import thing\n",
}

SYNTHETIC_VIOLATION_CASES = tuple(
    pytest.param(target, form, template.format(target=target), id=f"{target}-{form}")
    for target in FORBIDDEN_DELIVERY_TARGETS
    for form, template in IMPORT_FORM_MATRIX.items()
)


def _delivery_target(module: str | None) -> str | None:
    """模块名（可含点号子模块路径）的根段命中 Delivery 目标时返回该目标名。

    按点号段边界匹配（``droid_review.shards`` 命中、``my_droid_review`` 不命中），
    避免子串匹配把无关模块误判成寄居件。
    """
    root = (module or "").split(".", 1)[0]

    return root if root in FORBIDDEN_DELIVERY_TARGETS else None


def _import_violations(source: str, *, filename: str = "<synthetic>") -> list[str]:
    """解析源码文本，返回 Core↛Delivery 违例描述列表（空列表 = 合规）。

    覆盖两种节点、四种写法：
    * ``ast.Import``：``import X`` / ``import X.y``；
    * ``ast.ImportFrom``：``from X import …`` / ``from X.y import …``，以及同包相对形式
      ``from . import X`` / ``from .X.y import …``（engine 内部即裸名 import 风格，
      相对形式同样构成反向依赖）。

    ``ast.walk`` 全节点遍历：函数体内的惰性 import 同样计入，不放过延迟依赖。
    违例串形如 ``<文件>:<行号>: import droid_review.shards``，便于失败信息直接定位。
    """
    violations: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _delivery_target(alias.name):
                    violations.append(f"{filename}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _delivery_target(module):
                imported = ", ".join(alias.name for alias in node.names)
                violations.append(f"{filename}:{node.lineno}: from {module} import {imported}")
            elif node.level and not node.module:
                for alias in node.names:
                    if _delivery_target(alias.name):
                        violations.append(f"{filename}:{node.lineno}: from . import {alias.name}")

    return violations


def _file_violations(name: str) -> list[str]:
    """扫描 engine/ 下真实文件（相对仓库根打印路径，便于定位）。"""
    path = ENGINE_DIR / name
    return _import_violations(
        path.read_text(encoding="utf-8"),
        filename=f"src/infra_core/engine/{name}",
    )


class TestRealTreeCompliance:
    """真文件扫描：当前树 7 个 Core 文件零违例（VAL-IMPORT-001）。"""

    def test_core_module_inventory_is_complete(self) -> None:
        """7 个 Core 文件必须全部在册，且 engine/ 下无未登记的同族核心模块。"""
        missing = [name for name in CORE_MODULE_FILES if not (ENGINE_DIR / name).is_file()]

        assert missing == [], f"Evolution Core 文件缺失（扫描面缩水，红线失守无人知）：{missing}"

        unregistered = sorted(
            path.name
            for path in ENGINE_DIR.glob("evolution_*.py")
            if path.name not in CORE_MODULE_FILES
        )

        assert unregistered == [], (
            f"engine/ 下未登记的 evolution_* 核心模块（红线扫描面漏项，须登记进本文件）："
            f"{unregistered}"
        )

    def test_core_modules_have_zero_delivery_imports(self) -> None:
        """全部 Core 文件对 Delivery 目标零 import（当前树基线，红线上锁）。"""
        violations = {
            name: found for name in CORE_MODULE_FILES if (found := _file_violations(name))
        }

        assert violations == {}, (
            "Evolution Core 反向依赖 Delivery（只允许 Delivery → Core）：\n"
            + "\n".join(line for found in violations.values() for line in found)
        )


class TestCheckerTeeth:
    """锁有牙齿：合成违例 3 目标 × 2 import 形式全覆盖（VAL-IMPORT-002）。"""

    @pytest.mark.parametrize(("target", "form", "source"), SYNTHETIC_VIOLATION_CASES)
    def test_synthetic_violation_is_detected(self, target: str, form: str, source: str) -> None:
        """每种形式的目标 import 都必须被检出，且检出信息报出对应模块名。"""
        violations = _import_violations(source, filename=f"synthetic-{form}.py")

        assert violations, f"checker 漏检（{form}）：{source!r}"
        assert any(target in line for line in violations), (
            f"检出信息未报出模块名 {target}：{violations}"
        )

    def test_function_body_import_is_detected(self) -> None:
        """函数体内 import（engine 惰性 import 风格）同样计入，行号可定位。"""
        source = "def lazy():\n    from anchor_gate import thing\n    return thing\n"

        violations = _import_violations(source)

        assert violations, "函数体内 import 必须被 ast.walk 覆盖"
        assert "anchor_gate" in violations[0]
        assert ":2:" in violations[0], f"违例行号应指向 import 行：{violations[0]}"


class TestCheckerBoundaries:
    """边界：锁只咬 Delivery 目标，不误伤合法 import 与文档字面名。"""

    @pytest.mark.parametrize(
        "source",
        [
            "from evolution_utils import gh_repo_args\n",
            "from evolution_adapters import ADAPTER_MAP\n",
            "import evolution_scanner\nimport version_sync\n",
            "from importlib import import_module\nimport os\nfrom pathlib import Path\n",
        ],
    )
    def test_legitimate_imports_are_not_flagged(self, source: str) -> None:
        assert _import_violations(source) == []

    def test_prefix_match_is_segment_wise_not_substring(self) -> None:
        """前缀匹配按点号段边界：``my_droid_review`` / ``anchor_gate_helpers`` 不是目标。"""
        source = (
            "import my_droid_review\n"
            "from anchor_gate_helpers import thing\n"
            "from extract_anchors import other\n"
        )

        assert _import_violations(source) == []

    def test_docstring_mention_is_not_an_import(self) -> None:
        """docstring 字面名（真实 ``engine/__init__.py`` 就有）不是 import，不得误报。"""
        source = (
            '"""寄居件：droid_review/、anchor_gate.py、extract_anchor.py 逻辑归 Delivery。"""\n'
        )

        assert _import_violations(source) == []
