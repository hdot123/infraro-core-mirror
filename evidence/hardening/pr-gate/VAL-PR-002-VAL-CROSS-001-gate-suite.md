# VAL-PR-002 & VAL-CROSS-001 Evidence: Full Gate Suite

## 1. pytest (full gate)
```
$ cd ~/infraro-core && .venv/bin/python -m pytest -q -n 8 --dist loadgroup --cov=src/infra_core --cov-fail-under=45 --timeout=600
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: ~/infraro-core
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0, xdist-3.8.0, timeout-2.4.0
timeout: 600.0s
timeout method: signal
timeout func_only: true
created: 8/8 workers
8 workers [2406 items]

Required test coverage of 45% reached. Total coverage: 75.79%
========================= 2401 passed, 5 skipped in 146.40s (0:02:26) =========================
EXIT_CODE=0
```

Run 2 (re-run for stability):
```
2401 passed, 5 skipped in 154.68s (0:02:34)
EXIT_CODE=0
```

Skips (verified as all 5 known):
- tests/test_daily_kb_audit_coverage.py:428 — cross-repo dependency
- tests/test_error_pattern_detector.py:500 — no real error data
- tests/test_error_pattern_detector.py:514 — no real error data
- tests/test_error_pattern_detector.py:1256 — no real error data
- tests/test_error_pattern_detector.py:1296 — no real error data

Note: First run had 1 non-deterministic failure in `test_trigger_release_contract.py::TestDroidCallShape::test_session_id_extracted_to_log` (AssertionError: 日志应含 session_id). Second and third runs both passed cleanly. This appears to be a pre-existing flaky test, not introduced by this PR.

## 2. mypy typecheck
```
$ .venv/bin/python -m mypy --strict src/infra_core
pyproject.toml: note: unused section(s): module = ['anchor_gate', 'evolution_self_audit', 'extract_anchor', 'guard_cli']
Success: no issues found in 29 source files
MYPY_EXIT=0

$ .venv/bin/python -m mypy --strict scripts/
pyproject.toml: note: unused section(s): module = ['anchor_gate', 'evolution_adapters', 'evolution_heartbeat', 'evolution_scanner', 'evolution_self_audit', 'evolution_utils', 'extract_anchor', 'infra_core.engine.*']
Success: no issues found in 7 source files
MYPY_SCRIPTS_EXIT=0
```

## 3. ruff lint + format
```
$ .venv/bin/python -m ruff check .
All checks passed!
RUFF_CHECK_EXIT=0

$ .venv/bin/python -m ruff format --check .
174 files already formatted
RUFF_FORMAT_EXIT=0
```

## Summary
- pytest: 2401 passed (>= 2370 expected), 5 skipped (matches known 5), exit 0
- mypy: clean (both scopes), exit 0
- ruff: clean (check + format), exit 0
- Coverage: 75.79% (>= 45% threshold)
