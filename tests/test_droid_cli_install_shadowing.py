"""Droid CLI 安装防遮蔽契约测试（INFRA-1053）。

回归保护：下载式 Droid CLI 安装步骤必须无条件做 GITHUB_PATH 前置接线并
校验生效二进制，禁止 ``if ! command -v droid`` 存在性守卫。

背景（run 34678442362，2026-09-12）：pve runner 预装 /usr/local/bin/droid
（Layer 1 离线化旧版 0.208.1）。旧安装逻辑 ``if ! command -v droid`` 在宿主
PATH 命中时跳过接线循环，GITHUB_PATH 从未写入安装目录，后续步骤静默解析到
被遮蔽的旧版二进制——安装器装 0.218.1、实际执行 0.208.1，droid exec 以
num_turns=0 / "Exec failed" 崩溃，droid-task.yml 兜底创建失败 Issue
（INFRA-1053）。9/14 run 34816858939 证明遮蔽持续存在（成功也是侥幸）。

2026-10-04 迁移裁定后仓库内仍残留旧模式的下载式载体（droid-task.yml 与
setup-byom-go-github 已换 D1-c 宿主候选择优，不受本契约约束）：
- droid-autofix.yml（自愈执行器；安装逻辑源头
  droid-runner-pilot.yml 已按 BYOK 裁定移除，治理记录归档于
  docs/governance/droid-runner-pilot-byok-record.md）

droid-review*.yml 与 droid-task.yml 的 host-first / 候选择优是相反契约
（宿主优先、版本门限，见 test_droid_cli_host_first.py 与
test_droid_task_executor.py），不在本契约覆盖范围。
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.schema

REPO_ROOT = Path(__file__).resolve().parent.parent

AUTOFIX = REPO_ROOT / ".github" / "workflows" / "droid-autofix.yml"

INSTALL_STEP_NAME = "Install Droid CLI"


def _workflow_install_script(path: Path, job_name: str) -> str:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for step in data["jobs"][job_name].get("steps", []):
        if step.get("name") == INSTALL_STEP_NAME:
            return str(step["run"])
    pytest.fail(f"{path.name}: {job_name} job must have a {INSTALL_STEP_NAME!r} step")


@pytest.fixture(
    params=[
        _workflow_install_script(AUTOFIX, "autofix"),
    ],
    ids=["autofix"],
)
def install_script(request: pytest.FixtureRequest) -> str:
    """下载式安装载体的 Install Droid CLI 步骤脚本。"""
    return str(request.param)


class TestDroidCliInstallShadowingGuard:
    """INFRA-1053：安装产物必须前置接线并校验生效二进制（防宿主旧版遮蔽）。"""

    def test_no_existence_guard_around_path_wiring(self, install_script: str) -> None:
        """禁止 ``if ! command -v droid`` 存在性守卫包住接线循环。

        宿主预装 droid 时该守卫使接线被跳过，GITHUB_PATH 永不写入——
        这正是 run 34678442362 的失败根因。
        """
        assert "if ! command -v droid" not in install_script, (
            "禁止用 command -v 存在性守卫包住 PATH 接线（宿主预装旧版时会静默跳过接线）"
        )

    def test_probes_install_destination_unconditionally(self, install_script: str) -> None:
        """安装后必须无条件探测安装产物路径（~/.factory/bin、~/.local/bin、/usr/local/bin）。"""
        assert '"$HOME/.factory/bin"' in install_script, "缺少 ~/.factory/bin 探测"
        assert '"$HOME/.local/bin"' in install_script, "缺少 ~/.local/bin 探测"

    def test_wires_github_path_after_install(self, install_script: str) -> None:
        """安装产物目录必须无条件写入 GITHUB_PATH（后续步骤解析新版）。"""
        assert '>> "$GITHUB_PATH"' in install_script, "缺少 GITHUB_PATH 接线"
        wiring_at = install_script.index('>> "$GITHUB_PATH"')
        download_at = install_script.index("app.factory.ai/cli")
        assert download_at < wiring_at, "GITHUB_PATH 接线必须发生在安装之后"

    def test_exports_path_for_current_step(self, install_script: str) -> None:
        """当前步骤内必须 export PATH（GITHUB_PATH 只对后续步骤生效）。

        不 export 时本步内的遮蔽校验会拿宿主旧版误报/漏报。
        """
        assert "export PATH=" in install_script, (
            "缺少步骤内 export PATH（GITHUB_PATH 不影响当前步骤的 PATH 解析）"
        )

    def test_effective_binary_must_match_installed(self, install_script: str) -> None:
        """生效二进制与安装产物不一致时必须显式报错（禁止静默用旧版）。"""
        assert "::error::PATH shadowing" in install_script, (
            "缺少遮蔽显式报错（生效二进制 ≠ 安装产物时应 fail-fast）"
        )

    def test_missing_binary_fails_explicitly(self, install_script: str) -> None:
        """探测不到安装产物时必须显式报错（不允许静默落到宿主二进制）。"""
        assert "::error::droid binary not found" in install_script, "缺少安装产物缺失的显式报错"

    def test_effective_version_logged(self, install_script: str) -> None:
        """安装产物与生效二进制路径都必须输出（CI 日志可审计遮蔽）。"""
        assert "droid installed at:" in install_script, "缺少安装产物路径输出"
        assert "droid effective at:" in install_script, "缺少生效二进制路径输出"
        assert "droid --version" in install_script, "缺少生效版本输出"

    def test_probes_fresh_install_only(self, install_script: str) -> None:
        """探测必须带 mtime 新鲜度门禁（防宿主预装旧版冒充安装产物）。

        droid-review R2（2026-10-04）：无新鲜度判定时，宿主 stale
        /usr/local/bin/droid（INFRA-1053 的 0.208.1）会被探测循环选中，
        EFFECTIVE == DROID_BIN 自我确认后静默跑旧版——探测必须证明候选是
        本轮安装产物（mtime >= 安装前时刻），证明不了就拒绝。
        """
        assert "PRE_INSTALL_TS=$(date +%s)" in install_script, "缺少安装前时间戳基准"
        assert '[ "$bin_ts" -ge "$PRE_INSTALL_TS" ]' in install_script, (
            "缺少 mtime >= 安装前时刻的新鲜度判定"
        )
        assert "refusing host-preinstalled binaries" in install_script, (
            "无新装产物时必须显式拒绝宿主预装二进制（fail-closed，不回落）"
        )

    def test_host_resolution_audited_before_wiring(self, install_script: str) -> None:
        """接线前必须记录宿主解析结果（遮蔽审计）。

        droid-review R2：export PATH 后 EFFECTIVE 恒等于 DROID_BIN，路径
        比较无法探测遮蔽；接线前的 command -v 结果才是后续步骤（GITHUB_PATH
        生效前）会命中的二进制，必须进日志供审计。
        """
        assert "HOST_SHADOW=$(command -v droid || true)" in install_script, "缺少接线前宿主解析审计"
        wiring = install_script.index("export PATH=")
        audit = install_script.index("HOST_SHADOW=$(command -v droid || true)")
        assert audit < wiring, "宿主解析审计必须发生在 export PATH 接线之前"


class TestShadowingGuardScope:
    """契约范围：唯一在役下载式载体（autofix）安装步必须持续守契约。"""

    def test_all_carriers_present(self) -> None:
        """载体文件存在且含安装步。"""
        assert AUTOFIX.exists(), "droid-autofix.yml 缺失"
        assert "app.factory.ai/cli" in _workflow_install_script(AUTOFIX, "autofix"), (
            "安装步必须保留官方安装源"
        )

    def test_droid_review_host_first_contract_untouched(self) -> None:
        """host-first 宿主优先契约不应被本修复移除（与本契约相反方向）。

        见 test_droid_cli_host_first.py：review 链路刻意宿主优先（Layer 1
        离线化预装 + 版本 ≥0.200 门限），与下载式载体的 fresh-install-first
        是两个相反契约，互不覆盖。
        """
        for path in (
            REPO_ROOT / ".github" / "workflows" / "droid-review.yml",
            REPO_ROOT / ".github" / "workflows" / "droid-review-shards.yml",
        ):
            raw = path.read_text(encoding="utf-8")
            assert "command -v droid" in raw, f"{path.name} host-first 宿主探测不应被本修复移除"
            assert "-ge 200" in raw, f"{path.name} 宿主版本门限不应被移除"
