"""Shared pilot-seed metrics helper (INFRA-1335 / #274 dedup).

INFRA-1335: ``compute_pilot_seed_metrics`` had 100% AST similarity across
test_pilot_seed_a and test_pilot_seed_b (17 lines / 150 tokens each),
triggering CODE_HYGIENE_DUPLICATE_BLOCK. The duplicate was a deliberately
seeded pilot signal (VAL-PILOT-009, seed PR #273); it converged here via the
R1 repair arm, and both test files import this single implementation.
"""

from __future__ import annotations


def compute_pilot_seed_metrics(records: list[dict[str, int]]) -> dict[str, int]:
    """Aggregate pilot-seed records into simple metrics."""
    totals: dict[str, int] = {"scanned": 0, "matched": 0, "skipped": 0}
    for record in records:
        totals["scanned"] += record.get("scanned", 0)
        totals["matched"] += record.get("matched", 0)
        if record.get("skipped"):
            totals["skipped"] += record["skipped"]
            continue
        if record.get("matched", 0) > 0:
            totals["matched"] += 0
    for key in ("scanned", "matched", "skipped"):
        if totals[key] < 0:
            totals[key] = 0
    return totals
