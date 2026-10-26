"""Gate 2 跨仓读退避重试契约测试（#319 瞬时误读连红三门的修复）

2026-10-12 PR #319 CI 实证：substrate-gate-suite 的 gate2 把 hdot123/infraro
的 CI 工作流误读为不可读（一次瞬时 API 失败被当成「main 上缺失」），记
NEW FINDING 连红 substrate-gate-suite / ci-ok / quality-gate 三个 gate。
根因 = gate_common.gh_api 单次失败即返回 None，调用方无从区分「瞬时故障」
与「确实缺失」。

本文件锁定退避重试语义：

* 瞬时失败（尝试失败后恢复）→ gh_api 返回结果，调用方不产生 finding；
* 重试耗尽仍失败 → 才降级 None，调用方照旧记 finding / unverifiable；
* 默认重试 ≥2 次（retries=2，共 3 次尝试），尝试之间有退避 sleep；
* 确定性 404 不重试（真实缺失不是瞬时态，立即返回 None）。

全部用例走 mock（patch gate_common 的 subprocess.run 与 time.sleep），
零网络依赖，不调用 gh。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = REPO_ROOT / "substrate" / "gates"

if str(GATES_DIR) not in sys.path:
    sys.path.insert(0, str(GATES_DIR))

import gate2_cross_repo  # noqa: E402
import gate_common  # noqa: E402

pytestmark = pytest.mark.business_policy

DECL_CI_ENDPOINT = (
    f"repos/{gate2_cross_repo.DECL_REPO_SLUG}/contents/.github/workflows/ci.yml?ref=main"
)
TRANSIENT_STDERR = "gh: upstream hiccup (HTTP 502)"
NOT_FOUND_STDERR = "gh: Not Found (HTTP 404)"


class FakeGhApi:
    """subprocess.run 替身：按端点重放脚本化结果，并记录调用与退避 sleep。

    脚本列表从头消费、末项复用：载荷 dict = 成功，str = 失败（作为 gh 的
    stderr 文本抛 CalledProcessError）。未脚本化的端点默认成功（{}），
    与「文件在场」的阴性对照一致。
    """

    def __init__(self) -> None:
        self.scripts: dict[str, list[Any]] = {}
        self.calls: list[str] = []
        self.sleeps: list[float] = []

    def script(self, endpoint: str, outcomes: list[Any]) -> None:
        self.scripts[endpoint] = list(outcomes)

    def run(self, cmd: list[str], **kwargs: Any) -> SimpleNamespace:  # noqa: ANN003
        endpoint = cmd[-1]
        self.calls.append(endpoint)
        outcomes = self.scripts.get(endpoint)
        if outcomes is None:
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, str):
            raise subprocess.CalledProcessError(1, cmd, stderr=outcome)
        return SimpleNamespace(returncode=0, stdout=json.dumps(outcome), stderr="")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gate_common.subprocess, "run", self.run)
        monkeypatch.setattr(time, "sleep", self.sleeps.append)


class TestGhApiRetrySemantics:
    """gh_api 的退避重试语义（gate 套件所有跨仓读的共同防线）。"""

    def test_transient_failure_recovers_within_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGhApi()
        fake.script(
            "repos/example/contents/x",
            [TRANSIENT_STDERR, TRANSIENT_STDERR, {"ok": True}],
        )
        fake.install(monkeypatch)

        assert gate_common.gh_api("repos/example/contents/x") == {"ok": True}
        assert len(fake.calls) == 3  # 初次 + 2 次重试
        assert len(fake.sleeps) == 2  # 每次重试前有退避

    def test_retries_exhausted_degrade_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGhApi()
        fake.script("repos/example/contents/x", [TRANSIENT_STDERR])
        fake.install(monkeypatch)

        assert gate_common.gh_api("repos/example/contents/x") is None
        assert len(fake.calls) == 3  # 重试耗尽（≥2 次重试）才放弃
        assert len(fake.sleeps) == 2

    def test_not_found_short_circuits_without_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGhApi()
        fake.script("repos/example/contents/x", [NOT_FOUND_STDERR])
        fake.install(monkeypatch)

        assert gate_common.gh_api("repos/example/contents/x") is None
        assert len(fake.calls) == 1  # 确定性缺失不烧重试
        assert fake.sleeps == []

    def test_first_attempt_success_skips_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGhApi()
        fake.script("repos/example/contents/x", [{"ok": True}])
        fake.install(monkeypatch)

        assert gate_common.gh_api("repos/example/contents/x") == {"ok": True}
        assert len(fake.calls) == 1
        assert fake.sleeps == []


class TestCheckSubstrateFacesRetry:
    """check_substrate_faces 跨仓 contents 读：瞬时失败不红，耗尽才红（#319 面）。"""

    @pytest.fixture(autouse=True)
    def _force_cross_repo_face(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 无本地声明仓检出 → 强制走 gh api 跨仓读（CI 无注入形态）。
        # /tmp 下可能有其他 worker 的 infraro 临时 clone，必须显式屏蔽。
        monkeypatch.setattr(gate2_cross_repo, "decl_dir", lambda: None)

    def _recovered_decl_ci(self) -> dict[str, str]:
        workflow = "name: ci\njobs:\n  substrate-gate-tests:\n    runs-on: ubuntu-latest\n"
        return {
            "content": base64.b64encode(workflow.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }

    def test_transient_read_failure_leaves_no_finding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeGhApi()
        fake.script(
            DECL_CI_ENDPOINT,
            [TRANSIENT_STDERR, TRANSIENT_STDERR, self._recovered_decl_ci()],
        )
        fake.install(monkeypatch)

        assert gate2_cross_repo.check_substrate_faces() == []
        assert len([call for call in fake.calls if call == DECL_CI_ENDPOINT]) == 3

    def test_exhausted_read_failure_records_finding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeGhApi()
        fake.script(DECL_CI_ENDPOINT, [TRANSIENT_STDERR])
        fake.install(monkeypatch)

        findings = gate2_cross_repo.check_substrate_faces()
        assert (
            gate2_cross_repo.DECL_REPO_SLUG,
            "ci workflow unreadable (missing on main)",
        ) in findings
        assert len([call for call in fake.calls if call == DECL_CI_ENDPOINT]) == 3
