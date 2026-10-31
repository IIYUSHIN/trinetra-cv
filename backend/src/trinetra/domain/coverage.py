"""CoverageStatus and AssuranceSufficiency enums (IMP-0038).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class CoverageStatus(WireEnum):
    RESOLVED = "RESOLVED"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AssuranceSufficiency(WireEnum):
    SUFFICIENT = "SUFFICIENT"
    LIMITED = "LIMITED"
    INSUFFICIENT = "INSUFFICIENT"


__all__ = ["AssuranceSufficiency", "CoverageStatus"]
