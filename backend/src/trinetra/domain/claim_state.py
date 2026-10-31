"""ClaimState enum (IMP-0037).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class ClaimState(WireEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_ASSESSED = "NOT_ASSESSED"


__all__ = ["ClaimState"]
