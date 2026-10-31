"""FreshnessState enum (IMP-0034).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class FreshnessState(WireEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"


__all__ = ["FreshnessState"]
