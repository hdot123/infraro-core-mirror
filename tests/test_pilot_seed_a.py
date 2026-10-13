"""Pilot seed file A（owner 裁定 D 消防演习式试点，2026-10-06）。

本文件与 ``tests/test_pilot_seed_b.py`` 原各含一段同构 helper，用于经正常
scanner 管道验证「检出 → 铸卡 → 派发 → 收敛」闭环（VAL-PILOT-009）。
INFRA-1335（#274）已由 R1 修复臂收敛：helper 抽至 ``tests/pilot_seed_helpers.py``。
"""

from __future__ import annotations

from tests.pilot_seed_helpers import compute_pilot_seed_metrics


def test_pilot_seed_metrics_a() -> None:
    records = [
        {"scanned": 3, "matched": 1, "skipped": 0},
        {"scanned": 5, "matched": 0, "skipped": 2},
    ]
    metrics = compute_pilot_seed_metrics(records)
    assert metrics == {"scanned": 8, "matched": 1, "skipped": 2}
