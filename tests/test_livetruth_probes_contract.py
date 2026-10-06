"""live-truth 探针脚本契约测试（scripts/livetruth_probes.py）。

回归锁：v2-F5 引入的探针脚本承担「文档数字断言唯一真源」的入口职责，
本文件锁定其结构契约，防止后续演进悄悄破坏：

1. 模块可加载（无网络/gh 凭证依赖即 import 成功——T1 探针本身是纯静态
   读取，凭证依赖只允许出现在 T2/T3 执行路径上）；
2. 三态模型常量齐全（PASS / FAIL / PENDING-EXTERNAL）；
3. 三层探针注册表存在且 id/层前缀一致（T1-* 只在 T1_PROBES，以此类推）；
4. 矩阵映射完整：17 行承重断言每行至少被一个探针锚定（#14 明确为
   「不设门」锚文档形态，仍需 T1-14 在板）；
5. 腐速三档常量在板（SLOW/MEDIUM/FAST）；
6. run_suite 退出码契约：零 FAIL → 0，有 FAIL → 1（PENDING-EXTERNAL
   不计入失败）；
7. 探针异常收敛契约：_run_spec 捕获任意异常转 FAIL，不中断套件；
8. CLI 契约：--t1/--t2/--t3/--list 互斥必选（argparse mutually exclusive
   group required=True）。

属性标记：schema（与 workflow/清单契约测试同档，随 -m schema 分组跑）。
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_SCRIPT = REPO_ROOT / "scripts" / "livetruth_probes.py"

MATRIX_ROWS = {f"#{i}" for i in range(1, 18)}  # 矩阵 17 行


def _load_probe_module() -> ModuleType:
    """以独立模块名加载探针脚本。

    必须先注册进 sys.modules：脚本用了 `from __future__ import annotations` +
    `@dataclass(frozen=True)`，dataclass 解析字符串注解（KW_ONLY 检测）时按
    `cls.__module__` 反查 sys.modules，未注册会 AttributeError。
    """
    spec = importlib.util.spec_from_file_location("livetruth_probes_under_test", PROBE_SCRIPT)
    assert spec is not None and spec.loader is not None, f"无法加载探针脚本：{PROBE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture(scope="module")
def probes() -> ModuleType:
    return _load_probe_module()


class TestProbeModuleContract:
    def test_module_loads_without_network(self, probes: ModuleType) -> None:
        """探针模块在无 gh/网络环境下可加载（T1 纯静态、零凭证）。"""
        assert hasattr(probes, "main")

    def test_three_state_constants(self, probes: ModuleType) -> None:
        """三态判定常量在板：PASS / FAIL / PENDING-EXTERNAL。"""
        assert probes.PASS == "PASS"
        assert probes.FAIL == "FAIL"
        assert probes.PEND == "PENDING-EXTERNAL"

    def test_speed_tiers(self, probes: ModuleType) -> None:
        """腐速三档常量在板（慢/中/快 → 重跑周期分档依据）。"""
        assert {probes.SLOW, probes.MEDIUM, probes.FAST} == {"慢", "中", "快"}


class TestProbeRegistryContract:
    def test_three_layers_registered(self, probes: ModuleType) -> None:
        """三层注册表在板且非空：T1 本地 / T2 gh 只读 / T3 Mac 周检。"""
        for name in ("T1_PROBES", "T2_PROBES", "T3_PROBES"):
            registry = getattr(probes, name, None)
            assert isinstance(registry, list) and registry, f"{name} 缺失或为空"

    def test_layer_prefix_consistency(self, probes: ModuleType) -> None:
        """探针 id 前缀与所在注册表层一致（T1-* 只出现在 T1_PROBES）。"""
        for layer in ("T1", "T2", "T3"):
            registry = getattr(probes, f"{layer}_PROBES")
            for spec in registry:
                assert spec.probe_id.startswith(f"{layer}-"), (
                    f"{spec.probe_id} 注册在 {layer}_PROBES 但前缀不符"
                )
                assert spec.layer == layer

    def test_probe_ids_unique(self, probes: ModuleType) -> None:
        """全部探针 id 唯一（三层并集无重复）。"""
        all_ids = [
            spec.probe_id for spec in [*probes.T1_PROBES, *probes.T2_PROBES, *probes.T3_PROBES]
        ]
        assert len(all_ids) == len(set(all_ids)), "探针 id 存在重复"

    def test_matrix_17_rows_all_anchored(self, probes: ModuleType) -> None:
        """矩阵 17 行承重断言每行至少被一个探针锚定（matrix 字段 #N）。"""
        anchored: set[str] = set()
        for spec in [*probes.T1_PROBES, *probes.T2_PROBES, *probes.T3_PROBES]:
            anchored.update(
                tok.strip() for tok in spec.matrix.split("+") if tok.strip().startswith("#")
            )
        missing = MATRIX_ROWS - anchored
        assert not missing, f"矩阵行未被任何探针锚定: {sorted(missing)}"


class TestRunSuiteSemantics:
    def test_exit_code_zero_fail_only(self, probes: ModuleType) -> None:
        """run_suite 契约：零 FAIL → 0；有 FAIL → 1（PENDING 不计失败）。"""
        spec_pass = probes.ProbeSpec(
            "X-99",
            "测试 PASS",
            "X",
            probes.SLOW,
            "-",
            lambda: probes.ProbeResult("X-99", "测试 PASS", probes.PASS, "ok"),
        )
        assert probes.run_suite("UT", [spec_pass]) == 0

        spec_fail = probes.ProbeSpec(
            "X-98",
            "测试 FAIL",
            "X",
            probes.SLOW,
            "-",
            lambda: probes.ProbeResult("X-98", "测试 FAIL", probes.FAIL, "bad"),
        )
        assert probes.run_suite("UT", [spec_fail]) == 1

        spec_pend = probes.ProbeSpec(
            "X-97",
            "测试 PENDING",
            "X",
            probes.SLOW,
            "-",
            lambda: probes.ProbeResult("X-97", "测试 PENDING", probes.PEND, "external"),
        )
        assert probes.run_suite("UT", [spec_pend]) == 0

    def test_exception_converges_to_fail(self, probes: ModuleType) -> None:
        """_run_spec 契约：探针函数抛任意异常 → 收敛为 FAIL 结果，不外抛。"""

        def boom() -> probes.ProbeResult:
            raise RuntimeError("探针内部错误")

        spec = probes.ProbeSpec("X-96", "异常收敛", "X", probes.SLOW, "-", boom)
        result = probes._run_spec(spec)
        assert result.state == probes.FAIL
        assert "RuntimeError" in result.detail


class TestCliContract:
    def test_no_args_is_usage_error(self, probes: ModuleType, capsys) -> None:
        """CLI 契约：--t1/--t2/--t3/--list 互斥必选，缺省 → SystemExit 2。"""
        with pytest.raises(SystemExit) as excinfo:
            probes.main()
        assert excinfo.value.code == 2
