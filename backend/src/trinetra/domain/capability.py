"""CapabilityState enum and capability requirement (IMP-0033).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import AfterValidator

from trinetra.domain.base import DomainModel
from trinetra.domain.enums import WireEnum
from trinetra.domain.tokens import CanonicalToken


class CapabilityState(WireEnum):
    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityName(CanonicalToken):
    KIND = "capability_name"
    __slots__ = ()


_RANK: Final = {
    CapabilityState.UNAVAILABLE: 0,
    CapabilityState.LIMITED: 1,
    CapabilityState.AVAILABLE: 2,
}


def _meaningful(minimum: CapabilityState) -> CapabilityState:
    if minimum is CapabilityState.UNAVAILABLE:
        raise ValueError("a requirement of UNAVAILABLE is satisfied by everything and means nothing")
    return minimum


class CapabilityRequirement(DomainModel):
    """A named capability that must be at least `minimum` for an operation to proceed.

    PROPOSED (ERR-CL-005): the corpus names "capability requirement" (IMP-0033) without fields.
    The AVAILABLE > LIMITED > UNAVAILABLE ordering lives here, deliberately NOT on the enum, so
    CapabilityState stays an unordered dimension. A LIMITED capability is never silently
    treated as AVAILABLE: the default minimum is AVAILABLE.
    """

    capability: CapabilityName
    minimum: Annotated[CapabilityState, AfterValidator(_meaningful)] = CapabilityState.AVAILABLE

    def satisfied_by(self, observed: CapabilityState) -> bool:
        if not isinstance(observed, CapabilityState):
            raise TypeError("observed must be a CapabilityState")
        return _RANK[observed] >= _RANK[self.minimum]


__all__ = ["CapabilityName", "CapabilityRequirement", "CapabilityState"]
