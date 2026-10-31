"""Persisted Case lifecycle position (IMP-0081).

Member names are the canonical v1 names; the wire form is lowercase (like `AssetKind`). There is no
`READY` member (readiness is derived preflight output, never persisted) and no `SAFE`/`HIGH_RISK`-style
member (lifecycle is not an assurance verdict). Only creation into PREFLIGHT exists in EIB-04.
"""

from __future__ import annotations

from trinetra.domain.enums import WireEnum


class CaseLifecycleState(WireEnum):
    PREFLIGHT = "preflight"
    ASSESSING = "assessing"
    RESULTS_AVAILABLE = "results_available"
    INVESTIGATING = "investigating"
    DECISION_READY = "decision_ready"
    DECIDED = "decided"
    ATTESTED = "attested"


__all__ = ["CaseLifecycleState"]
