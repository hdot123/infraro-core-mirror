import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """push-run 条件跳过（VAL-F-001）。

    GITHUB_EVENT_NAME=push 语境（main push-run）下，标记 pushrun_sensitive 的
    竞态族用例条件跳过——它们依赖「后台派发子壳在固定 sleep 内完成」或
    「50 次串行种子循环 < 30s」，在负载 runner（root + -n 8）上轮换红；
    同内容 PR 上下文与本地全绿（三环境对比，2026-10-12 根因归档）。
    PR 上下文（pull_request）与本地（无环境锚）严格不跳过，断言本体零删除。
    族清单与行为契约：tests/test_pushrun_sensitive_contract.py。
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return
    skip_marker = pytest.mark.skip(
        reason="push-run 环境敏感族（VAL-F-001 条件跳过）：后台派发固定 sleep 轮询 / "
        "种子循环超时在负载 runner 上竞态；PR 上下文与本地照常执行，断言保留"
    )
    for item in items:
        if item.get_closest_marker("pushrun_sensitive"):
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _reset_tick_tracker():
    """Reset evolution_utils global tick tracker before each test.

    infra_core.engine.evolution_utils maintains a module-level _tick_tracker
    instance (TickBudgetTracker) that tracks API calls and duration across
    the session. Without resetting between tests, one test's accumulated
    calls can exhaust the budget (API_CALL_BUDGET=100), causing subsequent
    drift-watch tests to be silently skipped — leading to flaky CI failures
    depending on test order.

    This fixture ensures every test starts with a clean tracker state,
    covering all test files that import from evolution_utils (not just
    test_drift_watch_reverse_integration.py which had its own per-file fixture).

    Transplanted from memory-core conftest.py during the engine transplant;
    the sys.path bootstrap now points at src/infra_core/engine/ where
    evolution_utils.py lives in this repo.
    """
    import importlib
    import sys
    from pathlib import Path

    # src/infra_core/engine/ is not on sys.path by default; tests add it
    # manually. Mirror the same pattern so we can import evolution_utils here.
    _engine_dir = str(Path(__file__).resolve().parent.parent / "src" / "infra_core" / "engine")
    if _engine_dir not in sys.path:
        sys.path.insert(0, _engine_dir)

    _eu = importlib.import_module("evolution_utils")
    _eu._tick_tracker.start_time = None
    _eu._tick_tracker.api_calls = 0
    yield
