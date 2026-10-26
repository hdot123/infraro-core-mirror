"""Gate 1 fail-closed 契约测试（audit-defense-hardening）

Gate 1（`substrate/gates/gate1_interface.py`）的两个静默放行面改显式失败后，
本文件锁定 fail-closed 语义与其回归边界：

* 声明模板不可得（无 sibling checkout 且 gh api 失败）→ 显式错误 + 非零退出，
  不再有 LOCAL-ONE 软通过分支；
* 存在待验证的 ``vX.Y.Z`` ref 而引擎 tags 不可得（``git ls-remote`` 失败返回空集）
  → 显式错误 + 非零退出，死引用检测不可静默跳过；
* tags 可得时死引用检测保持（DEAD reference → 非零）；
* `gate0-exemptions.md` ``## Gate 1`` 节注册 token 命中的 error 仍归 owned，
  全部 owned → exit 0（存量豁免回归不变）。

全部用例走 mock（patch 模块级 name import 的 ``fetch_decl_templates`` /
``remote_tags``），零网络依赖，不调用 ``gh``。
"""

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = REPO_ROOT / "substrate" / "gates"

if str(GATES_DIR) not in sys.path:
    sys.path.insert(0, str(GATES_DIR))

import gate1_interface  # noqa: E402

pytestmark = pytest.mark.business_policy

# setup-labels.yml 暴露 workflow_call 且无 required-without-default 输入/secret，
# 是最干净的目标面：fixture 只引入被测语义（ref/tags）带来的 error，不夹带
# per-key 噪音。
ENGINE_WORKFLOW = "hdot123/infraro-core/.github/workflows/setup-labels.yml"

# gate0-exemptions.md `## Gate 1` 节登记的存量 break（watchdog.yml per-key），
# 用于锁定「registered token 命中 → owned → 全部 owned exit 0」回归。
WATCHDOG_REGISTERED_BREAK = """name: watchdog
on:
  workflow_dispatch:
jobs:
  watchdog:
    uses: hdot123/infraro-core/.github/workflows/droid-review-watchdog-handlers.yml@v0.18.8
    with:
      engine_ref: v0.18.8
    secrets:
      dispatch_token: ${{ secrets.DISPATCH_TOKEN }}
"""

# 无 uses 调用的模板：tags 不可得时也没有任何待验证 ref（阴性对照）。
NO_CALL_TEMPLATE = """name: fixture
on:
  workflow_dispatch:
jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


def _call_template(uses: str) -> str:
    """A declaration template with a single engine call step."""
    return f"name: fixture\non:\n  workflow_dispatch:\njobs:\n  call-engine:\n    uses: {uses}\n"


def _synthetic_tarball(members: dict[str, str]) -> bytes:
    """In-memory tar.gz mirroring the declaration repo's member layout."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in members.items():
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class _StubResponse:
    """Minimal ``urlopen()`` response stub."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _offline(*args: object, **kwargs: object) -> None:
    raise OSError("offline")


def _run_main(*, templates: dict[str, str], tags: set[str]) -> int:
    """Run ``gate1_interface.main()`` with the declaration face and tags mocked.

    ``REPO_ROOT`` 指向空临时目录：docs 死引用扫描面随版本 bump 变动（docs 内
    既有 engine tag 引用），不应耦合本文件的断言面——本文件只锁模板面与 tags
    面的 fail-closed 语义。存量登记表解析走 ``registry_entries`` 的默认参数
    （定义期绑定），不受本 patch 影响，仍读真实 `gate0-exemptions.md`。
    """
    with (
        tempfile.TemporaryDirectory() as tmp,
        patch.object(gate1_interface, "fetch_decl_templates", return_value=templates),
        patch.object(gate1_interface, "remote_tags", return_value=set(tags)),
        patch.object(gate1_interface, "REPO_ROOT", Path(tmp)),
    ):
        return int(gate1_interface.main())


class TestTemplatesUnavailable:
    """模板不可得 → fail-closed（VAL-GATE1-001）。"""

    def test_templates_unavailable_fails_closed(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _run_main(templates={}, tags={"v0.18.8"})
        out = capsys.readouterr().out

        assert code != 0, "模板不可得必须非零退出（不得软通过）"
        assert "FAIL" in out
        assert "templates unavailable" in out, f"必须打印显式模板错误；实际输出：{out!r}"

    def test_no_local_one_soft_pass_remains(self) -> None:
        source = (GATES_DIR / "gate1_interface.py").read_text(encoding="utf-8")

        assert "LOCAL-ONE" not in source, "LOCAL-ONE 软通过分支必须彻底移除"
        assert "WARN: declaration templates unavailable" not in source


class TestTagsUnavailable:
    """tags 不可得而存在待验证 v-ref → fail-closed（VAL-GATE1-002）。"""

    def test_tags_unavailable_with_pinned_v_ref_fails_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        templates = {"fresh-template.yml": _call_template(f"{ENGINE_WORKFLOW}@v9.9.9")}

        code = _run_main(templates=templates, tags=set())
        out = capsys.readouterr().out

        assert code != 0, "tags 不可得时死引用检测不可静默跳过"
        assert "FAIL" in out
        assert "tags unavailable" in out, f"必须打印显式 tags 错误；实际输出：{out!r}"

    def test_tags_unavailable_without_pinned_refs_stays_green(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        templates = {"noop-template.yml": NO_CALL_TEMPLATE}

        code = _run_main(templates=templates, tags=set())
        out = capsys.readouterr().out

        assert code == 0, f"无待验证 v-ref 时 tags 空集不应误报；实际输出：{out!r}"
        assert "PASS" in out


class TestDeadReferenceSweep:
    """tags 可得时死引用检测与存量豁免回归（VAL-GATE1-003）。"""

    def test_dead_ref_reported_when_tags_available(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        templates = {"fresh-template.yml": _call_template(f"{ENGINE_WORKFLOW}@v9.9.9")}

        code = _run_main(templates=templates, tags={"v0.18.8"})
        out = capsys.readouterr().out

        assert code != 0
        assert "DEAD reference" in out
        assert "v9.9.9" in out

    def test_registered_stock_token_keeps_owned_exit_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        templates = {"watchdog.yml": WATCHDOG_REGISTERED_BREAK}

        code = _run_main(templates=templates, tags={"v0.18.8"})
        out = capsys.readouterr().out

        assert code == 0, f"全部 owned 必须 exit 0；实际输出：{out!r}"
        assert "REGISTERED STOCK" in out
        assert "new breaks: 0" in out
        assert "PASS" in out

    def test_multiple_registered_templates_all_owned(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        templates = {
            "watchdog.yml": WATCHDOG_REGISTERED_BREAK,
            "heartbeat.yml": _call_template(f"{ENGINE_WORKFLOW}@v9.9.9"),
        }

        code = _run_main(templates=templates, tags={"v0.18.8"})
        out = capsys.readouterr().out

        assert code == 0, f"多份存量登记模板全部 owned 时 exit 0；实际输出：{out!r}"
        assert "new breaks: 0" in out


class TestTemplateResolution:
    """模板解析面：真实 stack 布局与无凭证 tarball 兜底（VAL-GATE1-004）。"""

    def test_local_sibling_stack_layout_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        templates_root = tmp_path / "templates"
        (templates_root / "python" / ".github" / "workflows").mkdir(parents=True)
        (templates_root / "typescript").mkdir(parents=True)
        (templates_root / "legacy.yml").write_text("flat\n", encoding="utf-8")
        (templates_root / "python" / "watchdog.yml").write_text("python\n", encoding="utf-8")
        (templates_root / "typescript" / "watchdog.yml").write_text(
            "typescript\n", encoding="utf-8"
        )
        (templates_root / "python" / ".github" / "workflows" / "ci.yml").write_text(
            "nested\n", encoding="utf-8"
        )
        monkeypatch.setattr(gate1_interface, "decl_dir", lambda: tmp_path)

        templates = gate1_interface.fetch_decl_templates()

        assert set(templates) == {"legacy.yml", "watchdog.yml"}
        assert templates["watchdog.yml"] == "python\n"

    def test_public_tarball_fallback_needs_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _synthetic_tarball(
            {
                "infraro-main/templates/python/watchdog.yml": "python\n",
                "infraro-main/templates/typescript/watchdog.yml": "typescript\n",
                "infraro-main/templates/python/.github/workflows/ci.yml": "nested\n",
                "infraro-main/docs/readme.md": "# doc\n",
            }
        )
        monkeypatch.setattr(gate1_interface, "decl_dir", lambda: None)
        monkeypatch.setattr(gate1_interface, "gh_api", lambda endpoint: None)
        monkeypatch.setattr(
            gate1_interface,
            "urllib",
            SimpleNamespace(
                request=SimpleNamespace(urlopen=lambda *a, **k: _StubResponse(payload))
            ),
        )

        templates = gate1_interface.fetch_decl_templates()

        assert set(templates) == {"watchdog.yml"}
        assert templates["watchdog.yml"] == "python\n"

    def test_unverifiable_sources_yield_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gate1_interface, "decl_dir", lambda: None)
        monkeypatch.setattr(gate1_interface, "gh_api", lambda endpoint: None)
        monkeypatch.setattr(
            gate1_interface,
            "urllib",
            SimpleNamespace(request=SimpleNamespace(urlopen=_offline)),
        )

        assert gate1_interface.fetch_decl_templates() == {}
