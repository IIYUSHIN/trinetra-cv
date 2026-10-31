"""Capability requirement evaluation (IMP-0087).

Decides, for a list of `CapabilityRequirement`s and one `CapabilitySnapshot`, which requirements hold.
The rules are deliberately unforgiving: a capability missing from the snapshot counts as UNAVAILABLE
(and is reported as missing, not guessed); LIMITED never satisfies a requirement for AVAILABLE; the
result lists every requirement in the order given, so nobody has to recompute it. This is the only
place that calculates "is the capability enough"; routes and UI never do.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Final, Self

from pydantic import Field, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.capability import CapabilityName, CapabilityRequirement, CapabilityState
from trinetra.domain.capability_snapshot import CapabilitySnapshot

MAX_REQUIREMENTS: Final = 256


class RequirementOutcome(DomainModel):
    capability: CapabilityName
    minimum: CapabilityState
    observed_capability_state: CapabilityState  # UNAVAILABLE when the snapshot does not list it
    listed_in_snapshot: bool
    satisfied: bool

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if not self.listed_in_snapshot and self.observed_capability_state is not CapabilityState.UNAVAILABLE:
            msg = "an unlisted capability is observed as UNAVAILABLE"
            raise ValueError(msg)
        expected = CapabilityRequirement(capability=self.capability, minimum=self.minimum).satisfied_by(
            self.observed_capability_state)
        if self.satisfied is not expected:
            msg = "satisfied disagrees with the requirement"
            raise ValueError(msg)
        return self


class RequirementEvaluation(DomainModel):
    outcomes: Annotated[tuple[RequirementOutcome, ...], Field(max_length=MAX_REQUIREMENTS)]

    @property
    def all_satisfied(self) -> bool:
        return all(o.satisfied for o in self.outcomes)

    @property
    def unmet(self) -> tuple[RequirementOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.satisfied)


def evaluate_requirements(
    requirements: Iterable[CapabilityRequirement], snapshot: CapabilitySnapshot
) -> RequirementEvaluation:
    outcomes: list[RequirementOutcome] = []
    for requirement in requirements:
        item = snapshot.get(requirement.capability)
        observed = item.capability_state if item is not None else CapabilityState.UNAVAILABLE
        outcomes.append(RequirementOutcome(
            capability=requirement.capability, minimum=requirement.minimum,
            observed_capability_state=observed, listed_in_snapshot=item is not None,
            satisfied=requirement.satisfied_by(observed),
        ))
    return RequirementEvaluation(outcomes=tuple(outcomes))


__all__ = ["MAX_REQUIREMENTS", "RequirementEvaluation", "RequirementOutcome", "evaluate_requirements"]
