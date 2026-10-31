"""Shared projection pieces: ProjectionMeta and ActionAvailability (IMP-0092).

Source contracts: `ProjectionMeta(schema_version, projection_generated_at)` rides on every projection;
`ActionAvailabilityDto(action, allowed, reason_code?, reason_text?, requires_confirmation,
requires_reason, requires_fresh_revision, affected_scope_ref?)` - "Frontend may disable/display action.
Server revalidates on command." A projection therefore only describes; it never authorises.

PROPOSED (ERR-CL-022): action names are lowercase canonical tokens (the source spells them in capitals);
a disallowed action must say why (code and text) and an allowed action carries neither, so the UI can
never show an unexplained disabled button or a reason on an enabled one.
"""

from __future__ import annotations

from typing import Annotated, Final, Self

from pydantic import Strict, model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.capability_snapshot import ReasonText
from trinetra.domain.entity import EntityRef
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken
from trinetra.errors import TrinetraError


class ProjectionSchemaVersion(CanonicalToken):
    KIND = "projection_schema_version"
    __slots__ = ()


class ActionName(CanonicalToken):
    KIND = "action_name"
    __slots__ = ()


class ActionReasonCode(CanonicalToken):
    KIND = "action_reason_code"
    __slots__ = ()


class ProjectionInconsistentError(TrinetraError):
    code = "PROJECTION_INCONSISTENT"
    default_message = "the facts given to a projection do not agree with each other"


PROJECTION_SCHEMA_VERSION: Final = ProjectionSchemaVersion("v1")


class ProjectionMeta(DomainModel):
    schema_version: ProjectionSchemaVersion
    projection_generated_at: UtcTimestamp

    @classmethod
    def at(cls, generated_at: UtcTimestamp) -> Self:
        return cls(schema_version=PROJECTION_SCHEMA_VERSION, projection_generated_at=generated_at)


class ActionAvailability(DomainModel):
    action: ActionName
    allowed: Annotated[bool, Strict()]
    reason_code: ActionReasonCode | None = None
    reason_text: ReasonText | None = None
    requires_confirmation: Annotated[bool, Strict()] = False
    requires_reason: Annotated[bool, Strict()] = False
    requires_fresh_revision: Annotated[bool, Strict()] = False
    affected_scope_ref: EntityRef | None = None

    @model_validator(mode="after")
    def _reasons_only_when_blocked(self) -> Self:
        has_reason = self.reason_code is not None or self.reason_text is not None
        if self.allowed and has_reason:
            msg = "an allowed action carries no reason"
            raise ValueError(msg)
        if not self.allowed and (self.reason_code is None or self.reason_text is None):
            msg = "a disallowed action must give both reason_code and reason_text"
            raise ValueError(msg)
        return self


__all__ = [
    "PROJECTION_SCHEMA_VERSION", "ActionAvailability", "ActionName", "ActionReasonCode",
    "ProjectionInconsistentError", "ProjectionMeta", "ProjectionSchemaVersion",
]
