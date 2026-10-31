"""TestVerdict enum (IMP-0036).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class TestVerdict(WireEnum):
    __test__ = False  # not a pytest class

    PASS = "PASS"  # noqa: S105 - verdict spelling, not a credential
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


__all__ = ["TestVerdict"]
