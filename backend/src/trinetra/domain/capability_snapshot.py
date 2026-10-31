"""CapabilityAssessment and CapabilitySnapshot (IMP-0086).

A snapshot is what the build can honestly do for one Case at one revision, per named capability:
AVAILABLE, LIMITED or UNAVAILABLE with a machine reason code and a human reason. It is computed for
Preflight and carries no Claim semantics, no score and no verdict. Items are in capability-token order
with no duplicates, so the same facts always give the same wire bytes.

Bounds (PROPOSED, ERR-CL-022): at most 64 capabilities per snapshot and 64 access / reference-purpose
tokens per assessment; reason text 1..2048 code points per the source. Access and reference-purpose
lists are sorted and duplicate-free for the same determinism reason.
"""

from __future__ import annotations

from typing import Annotated, Final, Self

from pydantic import AfterValidator, Field, Strict, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.text import text_validator
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken

MAX_CAPABILITIES: Final = 64
MAX_TOKENS: Final = 64
MAX_REASON_TEXT: Final = 2048


class CapabilityReasonCode(CanonicalToken):
    KIND = "capability_reason_code"
    __slots__ = ()


class AccessName(CanonicalToken):
    """A named access a capability needs or has (for example a runtime or a hardware path)."""

    KIND = "access_name"
    __slots__ = ()


ReasonText = Annotated[str, Strict(), text_validator("reason_text", 1, MAX_REASON_TEXT)]


def _sorted_unique(values: tuple[CanonicalToken, ...]) -> tuple[CanonicalToken, ...]:
    texts = [v.text for v in values]
    if texts != sorted(set(texts)):
        msg = "values must be unique and in ascending order"
        raise ValueError(msg)
    return values


AccessList = Annotated[
    tuple[AccessName, ...], Field(max_length=MAX_TOKENS), AfterValidator(_sorted_unique)
]
PurposeList = Annotated[
    tuple[ReferencePurpose, ...], Field(max_length=MAX_TOKENS), AfterValidator(_sorted_unique)
]


class CapabilityAssessment(DomainModel):
    capability: CapabilityName
    capability_state: CapabilityState
    reason_code: CapabilityReasonCode
    reason_text: ReasonText
    required_access: AccessList
    available_access: AccessList
    required_reference_purposes: PurposeList


class CapabilitySnapshot(DomainModel):
    captured_at: UtcTimestamp
    case_id: CaseId
    case_state_revision: CaseStateRevision
    items: Annotated[tuple[CapabilityAssessment, ...], Field(max_length=MAX_CAPABILITIES)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        names = [item.capability.text for item in self.items]
        if names != sorted(set(names)):
            msg = "items must be unique and in ascending capability order"
            raise ValueError(msg)
        return self

    def get(self, capability: CapabilityName) -> CapabilityAssessment | None:
        return next((item for item in self.items if item.capability == capability), None)

    @classmethod
    def of(
        cls, *, captured_at: UtcTimestamp, case_id: CaseId, case_state_revision: CaseStateRevision,
        items: tuple[CapabilityAssessment, ...] | list[CapabilityAssessment],
    ) -> Self:
        """Build a snapshot from items in any order (they are put in canonical order)."""
        ordered = tuple(sorted(items, key=lambda item: item.capability.text))
        return cls(captured_at=captured_at, case_id=case_id, case_state_revision=case_state_revision,
                   items=ordered)


__all__ = [
    "MAX_CAPABILITIES", "MAX_REASON_TEXT", "MAX_TOKENS", "AccessName", "CapabilityAssessment",
    "CapabilityReasonCode", "CapabilitySnapshot", "ReasonText",
]
