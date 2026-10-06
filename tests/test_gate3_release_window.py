"""Gate 3 版本四源「发布在途窗口」契约测试（release-please PR 必红修复）。

背景（2026-09-30 PR #166 release 0.21.0 CI 红）：release-please PR 合并前，
pyproject.toml / .release-please-manifest.json / src/infra_core/__init__.py
已 bump 到 N+1，而 v(N+1) tag 要等 PR 合并后才打——四源校验在该窗口期必然
判 VERSION DRIFT，阻断所有正常发布 PR（结构性误伤；0.20.0 当时绿灯是
tag 先于 PR 存在的历史巧合，不是机制保证）。

本文件锁定修复后的语义：

* 三文件一致且恰好领先 tag 一个 semver 位（patch/minor/major 三种单步 bump）
  → 放行并打印 ``release in flight`` 登记行（可审计，非静默）；
* 四源完全一致 → 放行（原语义不变）；
* 三文件互相不一致（历史 0.18.3 ``__init__`` 漂移类）→ 仍红；
* 三文件一致但领先超过一个版本 / 落后于 tag / 非 semver → 仍红；
* 任一源不可读（含 tags 拉取失败返回空）→ 仍红（fail-closed，对齐
  test_gate1_fail_closed.py 的语义基线）。

全部用例走 mock（patch 模块级 ``pyproject_version`` / ``manifest_version`` /
``init_version`` / ``remote_tags``），零网络依赖。
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

import gate3_exposure  # noqa: E402

pytestmark = pytest.mark.business_policy


def _check(
    *,
    pyproject: str,
    manifest: str,
    init: str,
    tags: set[str],
) -> tuple[list[str], str]:
    """运行 ``check_version_four_source``，返回 (errors, stdout)。"""
    import io
    from contextlib import redirect_stdout

    errors: list[str] = []
    buffer = io.StringIO()
    with (
        redirect_stdout(buffer),
        patch.object(gate3_exposure, "pyproject_version", return_value=pyproject),
        patch.object(gate3_exposure, "manifest_version", return_value=manifest),
        patch.object(gate3_exposure, "init_version", return_value=init),
        patch.object(gate3_exposure, "remote_tags", return_value=set(tags)),
    ):
        gate3_exposure.check_version_four_source(errors)
    return errors, buffer.getvalue()


class TestReleaseInFlightWindow:
    """发布在途窗口：三文件一致且恰好领先 tag 一个 semver 位 → 放行。"""

    def test_minor_bump_ahead_passes_with_audit_line(self) -> None:
        """PR #166 实况：文件 0.21.0 / tag v0.20.0 → 放行 + release in flight 行。"""
        errors, out = _check(
            pyproject="0.21.0",
            manifest="0.21.0",
            init="0.21.0",
            tags={"v0.20.0", "v0.19.0"},
        )

        assert errors == [], f"发布在途窗口不得报错；实际 errors={errors!r}"
        assert "release in flight" in out, f"必须打印登记行（可审计，非静默）；实际输出：{out!r}"

    def test_patch_bump_ahead_passes(self) -> None:
        errors, _ = _check(
            pyproject="0.20.1",
            manifest="0.20.1",
            init="0.20.1",
            tags={"v0.20.0"},
        )

        assert errors == []

    def test_major_bump_ahead_passes(self) -> None:
        errors, _ = _check(
            pyproject="1.0.0",
            manifest="1.0.0",
            init="1.0.0",
            tags={"v0.20.0"},
        )

        assert errors == []

    def test_all_four_consistent_still_passes(self) -> None:
        """四源完全一致（tag 已打）→ 原放行语义不变，且无 in-flight 行。"""
        errors, out = _check(
            pyproject="0.20.0",
            manifest="0.20.0",
            init="0.20.0",
            tags={"v0.20.0"},
        )

        assert errors == []
        assert "release in flight" not in out


class TestDriftStillFails:
    """真实漂移类照旧红（gate 原始使命：0.18.3 ``__init__`` 漂移）。"""

    def test_files_disagree_among_themselves_fails(self) -> None:
        errors, out = _check(
            pyproject="0.21.0",
            manifest="0.20.0",
            init="0.21.0",
            tags={"v0.20.0"},
        )

        assert errors, "三文件互相不一致必须仍红"
        assert any("VERSION DRIFT" in err for err in errors)
        assert "release in flight" not in out

    def test_two_versions_ahead_fails(self) -> None:
        """领先两个版本不是合法发布窗口（跳版=漂移）。"""
        errors, _ = _check(
            pyproject="0.22.0",
            manifest="0.22.0",
            init="0.22.0",
            tags={"v0.20.0"},
        )

        assert any("VERSION DRIFT" in err for err in errors)

    def test_files_behind_tag_fails(self) -> None:
        errors, _ = _check(
            pyproject="0.19.0",
            manifest="0.19.0",
            init="0.19.0",
            tags={"v0.20.0"},
        )

        assert any("VERSION DRIFT" in err for err in errors)

    def test_non_semver_version_fails(self) -> None:
        errors, _ = _check(
            pyproject="0.21.0rc1",
            manifest="0.21.0rc1",
            init="0.21.0rc1",
            tags={"v0.20.0"},
        )

        assert any("VERSION DRIFT" in err for err in errors)


class TestFailClosedPreserved:
    """源不可读 → 仍红（fail-closed 语义不因窗口放行而弱化）。"""

    def test_tags_unreadable_fails(self) -> None:
        """remote_tags 拉取失败返回空集 → tag 源为 None → 仍红。"""
        errors, _ = _check(
            pyproject="0.21.0",
            manifest="0.21.0",
            init="0.21.0",
            tags=set(),
        )

        assert any("version sources unreadable" in err for err in errors)

    def test_file_source_none_fails(self) -> None:
        errors, _ = _check(
            pyproject="0.21.0",
            manifest=None,  # type: ignore[arg-type]
            init="0.21.0",
            tags={"v0.20.0"},
        )

        assert any("version sources unreadable" in err for err in errors)
