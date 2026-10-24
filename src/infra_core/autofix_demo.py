"""Droid autofix demo fixture: healthy reference scenario.

This module is the stable positive sample for the droid-autofix closed
loop. The unit test (tests/test_autofix_demo.py) and this implementation
describe and satisfy the same correct behavior of count_enabled.

Historical note: earlier iterations injected an intentional single-token
bug here for autofix drills. That bug has since been fixed on main; the
docstring below documents the current, healthy state so future drill
agents do not "repair" a bug that no longer exists.

Fix policy for future drills: re-inject a deliberate bug into this
implementation only, and keep tests/test_autofix_demo.py asserting the
correct behavior.
"""


def count_enabled(flags: list[bool]) -> int:
    """Return the number of enabled (True) flags."""
    total = 0
    for flag in flags:
        if flag:
            total += 1
    return total
