"""Gate 2 本地面 fail-open 契约测试（shared-runner HOME 权限污染防御）

2026-10-07 PR #281 CI 实证：自托管 runner（HOME=/home/hatch）上
``~/.factory/config/repositories.yml`` 以不可读权限出现（外进程写入），
``load_repositories_yml()`` 的 ``config_path.exists()`` 探测在 try 块外，
Python 3.12 pathlib 对 EACCES 的 stat 直接抛 PermissionError（os.path.exists
吞掉、pathlib 不吞），gate 2 崩溃 → substrate-gate-suite 红 → ci-ok 红。

本文件锁定降级语义：

* 存在但不可读（stat 抛 PermissionError）→ 返回 None → LOCAL-ONE 软路径，
  与「文件不存在」同待遇，不允许崩进程；
* webhook checkout 候选目录 is_dir() 抛 PermissionError → 跳过该候选，
  降级 LOCAL-ONE，不允许崩进程。

全部用例走 mock（patch 模块级 ``Path.home``），零网络依赖，不调用 ``gh``。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = REPO_ROOT / "substrate" / "gates"

if str(GATES_DIR) not in sys.path:
    sys.path.insert(0, str(GATES_DIR))

import gate2_cross_repo  # noqa: E402

pytestmark = pytest.mark.business_policy


class TestLoadRepositoriesYmlUnreadable:
    """load_repositories_yml 对不可读 HOME 态的降级语义。"""

    def test_stat_permission_error_degrades_to_none(self, tmp_path: Path) -> None:
        """exists() 抛 PermissionError → None（LOCAL-ONE 路径），不崩进程。"""
        config_dir = tmp_path / ".factory" / "config"
        config_dir.mkdir(parents=True)
        config_path = config_dir / "repositories.yml"
        config_path.write_text("teams: []\n", encoding="utf-8")

        with (
            patch.object(gate2_cross_repo.Path, "home", return_value=tmp_path),
            patch.object(
                gate2_cross_repo.Path,
                "exists",
                side_effect=PermissionError(13, "Permission denied"),
            ),
        ):
            assert gate2_cross_repo.load_repositories_yml() is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """阴性对照：文件不存在 → None（既有回归边界不变）。"""
        with patch.object(gate2_cross_repo.Path, "home", return_value=tmp_path):
            assert gate2_cross_repo.load_repositories_yml() is None

    def test_readable_file_returns_config(self, tmp_path: Path) -> None:
        """可读文件正常加载（yaml dict 原样返回）。"""
        config_dir = tmp_path / ".factory" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "repositories.yml").write_text(
            "teams:\n  - repositories:\n      - githubRepo: hdot123/infraro-core\n",
            encoding="utf-8",
        )
        with patch.object(gate2_cross_repo.Path, "home", return_value=tmp_path):
            config = gate2_cross_repo.load_repositories_yml()
        assert config is not None
        assert "hdot123/infraro-core" in gate2_cross_repo.registered_repo_slugs(config)


class TestWorkerRoutingUnreadableCandidates:
    """check_worker_routing 对不可读候选目录的降级语义。"""

    def test_is_dir_permission_error_degrades_to_local_one(self, tmp_path: Path) -> None:
        """is_dir() 抛 PermissionError → 跳过候选 → LOCAL-ONE，不崩进程。"""
        with (
            patch.object(gate2_cross_repo.Path, "home", return_value=tmp_path),
            patch.object(
                gate2_cross_repo.Path,
                "is_dir",
                side_effect=PermissionError(13, "Permission denied"),
            ),
        ):
            findings, local_one = gate2_cross_repo.check_worker_routing(None)

        assert findings == []
        assert len(local_one) == 1
        assert "LOCAL-ONE" in local_one[0] or "不可本地验证" in local_one[0]
