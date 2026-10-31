"""PreflightPlanner: the one place that turns declared capabilities into a Case's CapabilitySnapshot.

A source declares what each capability could do; the planner then checks the declaration against the
facts of this Case: every access a capability needs must be available and every reference purpose it needs
must be backed by an approved reference. A capability that fails either check becomes UNAVAILABLE with a
reason that names what is missing. The planner never raises a capability's state and never invents one -
it can only keep or lower what the source declared. Routes and projections never calculate this.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Final, Protocol

from trinetra.application.ports.clock import ClockPort
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.capability import CapabilityState
from trinetra.domain.capability_snapshot import (
    CapabilityAssessment,
    CapabilityReasonCode,
    CapabilitySnapshot,
)
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.tokens import CanonicalToken
from trinetra.errors import TrinetraError

ACCESS_MISSING: Final = CapabilityReasonCode("required_access_missing")
REFERENCE_NOT_APPROVED: Final = CapabilityReasonCode("required_reference_not_approved")
_NAMED_LIMIT: Final = 8


class PreflightPlanError(TrinetraError):
    code = "PREFLIGHT_PLAN_INVALID"
    default_message = "the declared capabilities cannot be planned"


class CapabilitySourcePort(Protocol):
    def declared(self) -> Sequence[CapabilityAssessment]: ...


def _named(tokens: Sequence[CanonicalToken]) -> str:
    names = [t.text for t in tokens]
    shown = ", ".join(names[:_NAMED_LIMIT])
    extra = len(names) - _NAMED_LIMIT
    return f"{shown} and {extra} more" if extra > 0 else shown


def _lowered(item: CapabilityAssessment, code: CapabilityReasonCode, text: str) -> CapabilityAssessment:
    return item.evolve(capability_state=CapabilityState.UNAVAILABLE, reason_code=code, reason_text=text)


class PreflightPlanner:
    def __init__(self, source: CapabilitySourcePort, clock: ClockPort) -> None:
        self._source = source
        self._clock = clock

    def plan(
        self, case: AssuranceCase, approved_purposes: Collection[ReferencePurpose]
    ) -> CapabilitySnapshot:
        declared = tuple(self._source.declared())
        names = [d.capability.text for d in declared]
        if len(set(names)) != len(names):
            raise PreflightPlanError("a capability is declared more than once")
        approved = set(approved_purposes)
        planned = [self._check(item, approved) for item in declared]
        return CapabilitySnapshot.of(
            captured_at=self._clock.now(), case_id=case.case_id,
            case_state_revision=case.case_state_revision, items=planned,
        )

    @staticmethod
    def _check(item: CapabilityAssessment, approved: set[ReferencePurpose]) -> CapabilityAssessment:
        if item.capability_state is CapabilityState.UNAVAILABLE:
            return item
        no_access = [a for a in item.required_access if a not in item.available_access]
        if no_access:
            return _lowered(item, ACCESS_MISSING, f"Required access is not available: {_named(no_access)}.")
        no_reference = [p for p in item.required_reference_purposes if p not in approved]
        if no_reference:
            return _lowered(item, REFERENCE_NOT_APPROVED,
                            f"No approved reference exists for: {_named(no_reference)}.")
        return item


__all__ = [
    "ACCESS_MISSING", "REFERENCE_NOT_APPROVED", "CapabilitySourcePort", "PreflightPlanError",
    "PreflightPlanner",
]
