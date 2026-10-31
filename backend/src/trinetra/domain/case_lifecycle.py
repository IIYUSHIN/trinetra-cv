"""Persisted Case lifecycle position (IMP-0081).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class CaseLifecycleState(WireEnum):
    PREFLIGHT = "PREFLIGHT"
    ASSESSING = "ASSESSING"
    RESULTS_AVAILABLE = "RESULTS_AVAILABLE"
    INVESTIGATING = "INVESTIGATING"
    DECISION_READY = "DECISION_READY"
    DECIDED = "DECIDED"
    ATTESTED = "ATTESTED"


__all__ = ["CaseLifecycleState"]
