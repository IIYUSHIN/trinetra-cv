"""AssuranceCase header and revision invariant (IMP-0080, IMP-0081).

The header names a Case, its persisted lifecycle position and its state revision. A Case is created in
PREFLIGHT at revision 1; every persisted change moves the revision forward by exactly one and never
moves `updated_at` backwards. Asset links live in their own records (`case_assets`).

There is deliberately no `READY` lifecycle state (readiness is derived preflight output) and no
transition table here: lifecycle transitions belong to the lifecycle machine, which needs real
assessments to exist first. `touch` therefore changes only the revision and the timestamp; it cannot
change the lifecycle position. PROPOSED (ERR-CL-022).
"""

from __future__ import annotations

from typing import Final, Self

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError

INITIAL_LIFECYCLE: Final = CaseLifecycleState.PREFLIGHT


class CaseInvariantError(TrinetraError):
    code = "CASE_INVARIANT_VIOLATED"
    default_message = "the case invariant was violated"


class AssuranceCase(DomainModel):
    case_id: CaseId
    lifecycle_state: CaseLifecycleState
    case_state_revision: CaseStateRevision
    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.updated_at < self.created_at:
            msg = "updated_at cannot precede created_at"
            raise ValueError(msg)
        return self

    @classmethod
    def create(cls, case_id: CaseId, now: UtcTimestamp) -> Self:
        return cls(
            case_id=case_id, lifecycle_state=INITIAL_LIFECYCLE,
            case_state_revision=CaseStateRevision.first(), created_at=now, updated_at=now,
        )

    def touch(self, now: UtcTimestamp) -> Self:
        """The successor header after one persisted change (revision +1, lifecycle unchanged)."""
        if now < self.updated_at:
            raise CaseInvariantError("time cannot move backwards")
        return self.evolve(case_state_revision=self.case_state_revision.next(), updated_at=now)


__all__ = ["INITIAL_LIFECYCLE", "AssuranceCase", "CaseInvariantError"]
