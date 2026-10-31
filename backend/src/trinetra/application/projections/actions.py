"""Action availability derived from the Case and its capability snapshot (IMP-0092).

START_ASSESSMENT is the only action in this build stage. It is allowed only for a Case still in PREFLIGHT
whose snapshot lists every catalog capability as AVAILABLE; anything missing counts as unavailable. When it
is blocked the reason says which kind of block it is: `assessment_runtime_not_implemented` when assurance
reasoning is declared not implemented in this build stage, otherwise `required_capability_unavailable`.
The projection only describes; the command handler revalidates (the server is the authority).
"""

from __future__ import annotations

from typing import Final

from trinetra.application.preflight.catalog import ASSURANCE_REASONING, CAPABILITY_CATALOG, NOT_IMPLEMENTED
from trinetra.application.projections.shared import ActionAvailability, ActionName, ActionReasonCode
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.capability import CapabilityRequirement
from trinetra.domain.capability_evaluation import evaluate_requirements
from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.case_lifecycle import CaseLifecycleState

START_ASSESSMENT: Final = ActionName("start_assessment")
NOT_IN_PREFLIGHT: Final = ActionReasonCode("case_not_in_preflight")
RUNTIME_NOT_IMPLEMENTED: Final = ActionReasonCode("assessment_runtime_not_implemented")
CAPABILITY_UNAVAILABLE: Final = ActionReasonCode("required_capability_unavailable")


def _blocked(code: ActionReasonCode, text: str) -> ActionAvailability:
    return ActionAvailability(action=START_ASSESSMENT, allowed=False, reason_code=code, reason_text=text,
                              requires_fresh_revision=True)


def start_assessment_availability(case: AssuranceCase, snapshot: CapabilitySnapshot) -> ActionAvailability:
    if case.lifecycle_state is not CaseLifecycleState.PREFLIGHT:
        return _blocked(NOT_IN_PREFLIGHT, "An assessment can only be started while the case is in preflight.")
    unmet = evaluate_requirements(
        [CapabilityRequirement(capability=name) for name in CAPABILITY_CATALOG], snapshot).unmet
    if not unmet:
        return ActionAvailability(action=START_ASSESSMENT, allowed=True, requires_fresh_revision=True)
    reasoning = snapshot.get(ASSURANCE_REASONING)
    if reasoning is not None and reasoning.reason_code == NOT_IMPLEMENTED:
        return _blocked(RUNTIME_NOT_IMPLEMENTED,
                        "Assessment cannot start: the assessment runtime is not implemented in this build.")
    names = ", ".join(o.capability.text for o in unmet)
    return _blocked(CAPABILITY_UNAVAILABLE, f"Assessment cannot start: not available: {names}.")


def available_actions(case: AssuranceCase, snapshot: CapabilitySnapshot) -> tuple[ActionAvailability, ...]:
    return (start_assessment_availability(case, snapshot),)


__all__ = [
    "CAPABILITY_UNAVAILABLE", "NOT_IN_PREFLIGHT", "RUNTIME_NOT_IMPLEMENTED", "START_ASSESSMENT",
    "available_actions", "start_assessment_availability",
]
