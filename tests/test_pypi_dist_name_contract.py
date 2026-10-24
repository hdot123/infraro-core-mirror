"""PyPI 分发名契约测试（pypi-channel f3 首发排障产出，2026-09-30）

背景：v0.22.0 首发被 PyPI 拒 403「OIDC scoped token is not valid for
project 'infra-core'」——仓库打包名（infra-core，历史改名产物）与 PyPI
trusted publisher 绑定名（infraro-core，pypi-f1 在案）不一致，且 infra-core
已被第三方项目（pj-ms/infra-core）占用，改名路线物理不可行。

实现注记（#187 第四轮结构化收敛）：本测试曾以正则逐行扫描 workflow 原文，
四轮评审发现（多行绕过/注释误拾/守卫形态脆弱/uv.lock 子串匹配脆弱）全部
源于该结构缺陷。现改为语义解析——yaml.safe_load 取 jobs.*.steps[].run
代码（剥整行注释后匹配探针），uv.lock 用 tomllib 加载——上述失败模式在
结构上不再可能，而非逐条打补丁。

契约三条防回归线：

1. 分发名唯一且 = infraro-core（pyproject [project].name 与 uv.lock 根包
   两个仓内侧真源一致）；
2. evolution 双 workflow 的 distribution() 探针集合必须含当前真源名
   （pyproject 读出）；旧名探针仅允许出现在同一 run 块内含
   PackageNotFoundError 守卫的 step（契约钉「裸崩溃」失败模式，不钉
   try/except 文本形态）；版本打印标签必须严格等于真源名；
3. trusted publisher 绑定名（docs 在案）与分发名一致性由本契约钉住
   仓内侧真源；PyPI 侧绑定面在 pypi-f1 证据（mission library）。
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.business_policy]

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_DIST_NAME = "infraro-core"
LEGACY_DIST_NAME = "infra-core"

EVOLUTION_WORKFLOWS = (
    ".github/workflows/evolution-scan.yml",
    ".github/workflows/evolution-heartbeat.yml",
)

# 以下匹配只作用于 yaml 解析出的 run 代码（非 workflow 原文）；
# \s 天然覆盖多行书写形态。
_DIST_CALL_RE = re.compile(r'distribution\(\s*["\']([^"\']+)["\']\s*\)')
_PRINT_LABEL_RE = re.compile(r'print\(\s*["\']([^"\']+)["\']\s*,\s*\w+\.version\s*\)')
_GUARD_RE = re.compile(r"except\s+(?:m\.)?PackageNotFoundError(?:\s+as\s+\w+)?\s*:")


def _pyproject_name() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    name = data["project"]["name"]
    assert isinstance(name, str) and name, "pyproject [project].name 必须为非空字符串"
    return name


def _uvlock_root_name() -> str | None:
    """tomllib 语义解析 uv.lock：取 source.editable == "." 的根包名。

    对序列化格式变化（空格/字段序/新增字段）天然鲁棒，不做原文子串
    匹配（#187 第四轮线程 4）。
    """
    data = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    for package in data.get("package", []):
        if (package.get("source") or {}).get("editable") == ".":
            return str(package["name"])
    return None


def _step_run_codes(wf_rel: str) -> list[str]:
    """yaml 解析 workflow，返回各 step 剥整行注释后的 run 代码列表。

    语义解析替代原文扫描（#187 第四轮收敛）：探针/守卫/标签只在 run
    代码内匹配，workflow 其余文本（name/env/shell 行）天然不参与；整行
    注释先剥，被注释掉的探针不会误拾（第四轮线程 3）。
    """
    wf = yaml.safe_load((REPO_ROOT / wf_rel).read_text(encoding="utf-8"))
    codes: list[str] = []
    for job in wf.get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run")
            if isinstance(run, str):
                codes.append(
                    "\n".join(ln for ln in run.splitlines() if not ln.strip().startswith("#"))
                )
    return codes


class TestDistNameConsistency:
    """契约 1：分发名仓内侧真源一致且唯一为 infraro-core。"""

    def test_pyproject_dist_name_is_infraro_core(self):
        assert _pyproject_name() == EXPECTED_DIST_NAME, (
            "分发名必须为 infraro-core（PyPI trusted publisher 绑定名；"
            "infra-core 已被第三方项目占用）。若确需改名，须同步重绑 "
            "PyPI trusted publisher 并更新本契约。"
        )

    def test_uvlock_root_package_matches_pyproject(self):
        root = _uvlock_root_name()
        assert root is not None, "uv.lock 未找到 editable 根包条目（uv lock 漂移？）"
        assert root == _pyproject_name(), (
            f"uv.lock 根包名 {root!r} != pyproject 分发名 {_pyproject_name()!r}"
            "（relock 缺失：env -u UV_CACHE_DIR uv lock）"
        )

    def test_import_name_unchanged(self):
        """import 名 infra_core 与分发名解耦：src 布局目录不得随分发名漂移。"""
        assert (REPO_ROOT / "src" / "infra_core").is_dir(), (
            "src/infra_core 目录缺失——import 名 infra_core 不得随分发名漂移"
        )


class TestEvolutionWorkflowDistProbe:
    """契约 2：evolution 双 workflow 的 distribution() 探针用动态真源名。"""

    @pytest.mark.parametrize("wf_rel", EVOLUTION_WORKFLOWS)
    def test_probes_cover_current_name_and_legacy_is_guarded(self, wf_rel: str) -> None:
        """真源名必须在场 + 旧名仅限守卫内 + 拒绝未知名。

        真源名在场是独立断言（#187 第四轮线程 1）：全部探针退化为旧名时，
        旧名的守卫豁免会让逐条校验空转——主探针缺席本身就是失败模式。
        守卫判定取语义级（第四轮线程 2）：旧名探针所在 run 块内存在
        except ... PackageNotFoundError（任意 as 别名/任意位置/多行）即免
        裸崩——契约钉「裸崩溃」失败模式，不钉 try/except 相邻文本形态。
        """
        expected = _pyproject_name()
        probe_codes = [c for c in _step_run_codes(wf_rel) if _DIST_CALL_RE.search(c)]
        all_args = [a for code in probe_codes for a in _DIST_CALL_RE.findall(code)]
        assert expected in all_args, (
            f"{wf_rel} distribution() 探针集合 {sorted(set(all_args))} 不含当前真源名"
            f" {expected!r}（无探针或全部退化为旧名都是主探针缺席）——"
            "主探针缺席即 provenance 断言红"
        )
        for code in probe_codes:
            args = _DIST_CALL_RE.findall(code)
            stale = [a for a in args if a not in (expected, LEGACY_DIST_NAME)]
            assert not stale, (
                f"{wf_rel} distribution() 探针使用了未知分发名：{stale}"
                f"（合法集合 = 当前真源 {expected!r} + 守卫内旧名 {LEGACY_DIST_NAME!r}）"
            )
            if LEGACY_DIST_NAME in args:
                assert _GUARD_RE.search(code), (
                    f"{wf_rel} 旧名兜底探测 {LEGACY_DIST_NAME!r} 所在 run 块缺少 "
                    "except PackageNotFoundError 守卫——改名后环境裸崩 provenance 步"
                )

    @pytest.mark.parametrize("wf_rel", EVOLUTION_WORKFLOWS)
    def test_version_print_label_is_current_name(self, wf_rel: str) -> None:
        """版本打印标签必须严格等于真源名——标签无守卫语境，不享旧名豁免。"""
        expected = _pyproject_name()
        labels = [lb for code in _step_run_codes(wf_rel) for lb in _PRINT_LABEL_RE.findall(code)]
        # 标签列表非空先断言：打印步若被重构成变量/f-string，收集器零命中
        # 会让下方标签契约空转成 no-op。
        assert labels, (
            f"{wf_rel} 未找到 print('<name>', d.version) 版本打印步——"
            "打印步重构时须同步本测试的标签匹配"
        )
        stale = [lb for lb in labels if lb != expected]
        assert not stale, (
            f"{wf_rel} 版本打印标签使用了过期分发名：{stale}（真源 pyproject "
            f"name={expected!r}）——标签必须打印当前真源名"
        )
