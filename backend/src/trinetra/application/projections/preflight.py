"""PreflightProjection and its builder (IMP-0094).

Source contract (EIB-04): the honest answer to "what can this build do for this Case?" - the Case context,
capabilities in groups with their state, reason and needs, the obligations that cannot be met, the resources
and references required, and the actions. `planned_tests` stays empty: no dummy tests are invented, because
no test exists to plan. "That output is a PASS. Fake scientific readiness is a failure."

The projection cannot lie by omission: every capability that is not AVAILABLE must appear as an unavailable
obligation with the same state and reason, and nothing AVAILABLE may appear as one. It only describes the
snapshot it is given; it calculates no capability state itself.

PROPOSED (ERR-CL-022): the field `state` of the source's capability item is `capability_state` (a bare
`state` field is forbidden by the domain conventions); grouping is identity / inputs / integrity /
reasoning, with any capability not in the table shown under `other` so none can vanish; `runtime_state`
from the later Wave GR contract is not carried until a runtime exists.
"""

from __future__ import annotations

from collections.abc import Collection
from types import MappingProxyType
from typing import Annotated, Final, Self

from pydantic import Field, model_validator

from trinetra.application.preflight import catalog
from trinetra.application.projections.actions import available_actions
from trinetra.application.projections.case_context import CaseContextProjection, build_case_context
from trinetra.application.projections.limitations import critical_limitations
from trinetra.application.projections.shared import (
    ActionAvailability,
    ProjectionInconsistentError,
    ProjectionMeta,
)
from trinetra.domain.asset_record import AssetRecord
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.base import DomainModel
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import (
    AccessList,
    AccessName,
    CapabilityAssessment,
    CapabilityReasonCode,
    CapabilitySnapshot,
    ReasonText,
)
from trinetra.domain.entity import EntityRef
from trinetra.domain.enums import WireEnum
from trinetra.domain.limitations import Limitation
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken

MAX_ITEMS: Final = 64
MAX_PLANNED_TESTS: Final = 10_000


class ReferenceReadiness(WireEnum):
    NOT_REQUIRED = "not_required"
    SATISFIED = "satisfied"
    MISSING = "missing"


class CapabilityGroupName(CanonicalToken):
    KIND = "capability_group_name"
    __slots__ = ()


GROUP_IDENTITY: Final = CapabilityGroupName("identity")
GROUP_INPUTS: Final = CapabilityGroupName("inputs")
GROUP_INTEGRITY: Final = CapabilityGroupName("integrity")
GROUP_REASONING: Final = CapabilityGroupName("reasoning")
GROUP_OTHER: Final = CapabilityGroupName("other")
GROUP_ORDER: Final = (GROUP_IDENTITY, GROUP_INPUTS, GROUP_INTEGRITY, GROUP_REASONING, GROUP_OTHER)
GROUP_OF: Final = MappingProxyType({
    catalog.ASSET_IDENTITY: GROUP_IDENTITY,
    catalog.DATASET_SEMANTIC_ADAPTER: GROUP_INPUTS,
    catalog.MODEL_RUNTIME: GROUP_INPUTS,
    catalog.DATA_INTEGRITY: GROUP_INTEGRITY,
    catalog.MODEL_INTEGRITY: GROUP_INTEGRITY,
    catalog.DRIFT_ASSESSMENT: GROUP_INTEGRITY,
    catalog.TIIR_VERIFICATION: GROUP_INTEGRITY,
    catalog.ASSURANCE_REASONING: GROUP_REASONING,
})


class CapabilityAssessmentProjection(DomainModel):
    capability: CapabilityName
    capability_state: CapabilityState
    reason_code: CapabilityReasonCode
    reason_text: ReasonText
    required_access: AccessList
    available_access: AccessList
    reference_state: ReferenceReadiness
    planned_test_refs: Annotated[tuple[EntityRef, ...], Field(max_length=MAX_PLANNED_TESTS)] = ()


class CapabilityGroup(DomainModel):
    group: CapabilityGroupName
    capabilities: Annotated[
        tuple[CapabilityAssessmentProjection, ...], Field(min_length=1, max_length=MAX_ITEMS)
    ]


class UnavailableObligation(DomainModel):
    capability: CapabilityName
    capability_state: CapabilityState
    reason_code: CapabilityReasonCode
    reason_text: ReasonText


class ResourceRequirement(DomainModel):
    access: AccessName
    needed_by: Annotated[tuple[CapabilityName, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    available: bool


class ReferenceRequirement(DomainModel):
    purpose: ReferencePurpose
    needed_by: Annotated[tuple[CapabilityName, ...], Field(min_length=1, max_length=MAX_ITEMS)]
    approved: bool


class PreflightProjection(DomainModel):
    meta: ProjectionMeta
    case_context: CaseContextProjection
    capability_groups: Annotated[tuple[CapabilityGroup, ...], Field(max_length=MAX_ITEMS)]
    planned_tests: Annotated[tuple[EntityRef, ...], Field(max_length=MAX_PLANNED_TESTS)] = ()
    unavailable_obligations: Annotated[tuple[UnavailableObligation, ...], Field(max_length=MAX_ITEMS)]
    resource_requirements: Annotated[tuple[ResourceRequirement, ...], Field(max_length=MAX_ITEMS)]
    reference_requirements: Annotated[tuple[ReferenceRequirement, ...], Field(max_length=MAX_ITEMS)]
    critical_limitations: Annotated[tuple[Limitation, ...], Field(max_length=MAX_ITEMS)]
    available_actions: Annotated[tuple[ActionAvailability, ...], Field(max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def _nothing_hidden(self) -> Self:
        shown = [c for g in self.capability_groups for c in g.capabilities]
        names = [c.capability.text for c in shown]
        if len(set(names)) != len(names):
            msg = "a capability appears in more than one place"
            raise ValueError(msg)
        owed = {
            c.capability: (c.capability_state, c.reason_code)
            for c in shown if c.capability_state is not CapabilityState.AVAILABLE
        }
        listed = {o.capability: (o.capability_state, o.reason_code) for o in self.unavailable_obligations}
        if len(listed) != len(self.unavailable_obligations) or listed != owed:
            msg = "unavailable obligations must be exactly the capabilities that are not AVAILABLE"
            raise ValueError(msg)
        return self


def _readiness(item: CapabilityAssessment, approved: Collection[ReferencePurpose]) -> ReferenceReadiness:
    if not item.required_reference_purposes:
        return ReferenceReadiness.NOT_REQUIRED
    if all(p in approved for p in item.required_reference_purposes):
        return ReferenceReadiness.SATISFIED
    return ReferenceReadiness.MISSING


def _groups(
    snapshot: CapabilitySnapshot, approved: Collection[ReferencePurpose]
) -> tuple[CapabilityGroup, ...]:
    by_group: dict[CapabilityGroupName, list[CapabilityAssessmentProjection]] = {}
    for item in snapshot.items:
        by_group.setdefault(GROUP_OF.get(item.capability, GROUP_OTHER), []).append(
            CapabilityAssessmentProjection(
                capability=item.capability, capability_state=item.capability_state,
                reason_code=item.reason_code, reason_text=item.reason_text,
                required_access=item.required_access, available_access=item.available_access,
                reference_state=_readiness(item, approved),
            ))
    return tuple(
        CapabilityGroup(group=g, capabilities=tuple(by_group[g])) for g in GROUP_ORDER if g in by_group
    )


def _obligations(snapshot: CapabilitySnapshot) -> tuple[UnavailableObligation, ...]:
    return tuple(
        UnavailableObligation(capability=i.capability, capability_state=i.capability_state,
                              reason_code=i.reason_code, reason_text=i.reason_text)
        for i in snapshot.items if i.capability_state is not CapabilityState.AVAILABLE
    )


def _resources(snapshot: CapabilitySnapshot) -> tuple[ResourceRequirement, ...]:
    needers: dict[str, list[CapabilityAssessment]] = {}
    for item in snapshot.items:
        for access in item.required_access:
            needers.setdefault(access.text, []).append(item)
    return tuple(
        ResourceRequirement(
            access=AccessName(name), needed_by=tuple(i.capability for i in items),
            available=all(AccessName(name) in i.available_access for i in items),
        )
        for name, items in sorted(needers.items())
    )


def _references(
    snapshot: CapabilitySnapshot, approved: Collection[ReferencePurpose]
) -> tuple[ReferenceRequirement, ...]:
    needers: dict[str, list[CapabilityAssessment]] = {}
    for item in snapshot.items:
        for purpose in item.required_reference_purposes:
            needers.setdefault(purpose.text, []).append(item)
    return tuple(
        ReferenceRequirement(purpose=ReferencePurpose(name), needed_by=tuple(i.capability for i in items),
                             approved=ReferencePurpose(name) in approved)
        for name, items in sorted(needers.items())
    )


def build_preflight(
    case: AssuranceCase, dataset: AssetRecord, model: AssetRecord, snapshot: CapabilitySnapshot,
    *, approved_purposes: Collection[ReferencePurpose], generated_at: UtcTimestamp,
) -> PreflightProjection:
    context = build_case_context(case, dataset, model, snapshot, generated_at)
    return PreflightProjection(
        meta=ProjectionMeta.at(generated_at), case_context=context,
        capability_groups=_groups(snapshot, approved_purposes),
        unavailable_obligations=_obligations(snapshot),
        resource_requirements=_resources(snapshot),
        reference_requirements=_references(snapshot, approved_purposes),
        critical_limitations=critical_limitations(case, snapshot),
        available_actions=available_actions(case, snapshot),
    )


__all__ = [
    "GROUP_IDENTITY",
    "GROUP_INPUTS",
    "GROUP_INTEGRITY",
    "GROUP_OF",
    "GROUP_ORDER",
    "GROUP_OTHER",
    "GROUP_REASONING",
    "CapabilityAssessmentProjection",
    "CapabilityGroup",
    "CapabilityGroupName",
    "PreflightProjection",
    "ProjectionInconsistentError",
    "ReferenceReadiness",
    "ReferenceRequirement",
    "ResourceRequirement",
    "UnavailableObligation",
    "build_preflight",
]
