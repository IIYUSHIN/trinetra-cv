"""AssuranceCase aggregate and revision invariant (IMP-0080, IMP-0081).

A Case names its optional label, persisted lifecycle position, state revision, timestamps and the asset
links fixed at commit. It is created in PREFLIGHT at revision 1; every committed mutation moves the
revision forward by exactly one and never moves `updated_at` backwards. Every Case holds a complete link
set (one dataset, one model, optional extras) from the moment it exists: package presence only, no
scientific validation is implied.

There is deliberately no `READY` lifecycle state and no transition table: only creation into PREFLIGHT is
implemented in EIB-04, and lifecycle transitions belong to the lifecycle machine, which needs real
assessments to exist first. `touch` therefore changes only the revision and the timestamp.

PROPOSED (ERR-CL-022): `case_label` is a `LogicalIdentifier` (so at most 128 code points; storage allows
256); links are immutable once the Case exists (asset replacement inside a Case is a later feature).
"""

from __future__ import annotations

from typing import Final, Self

from pydantic import model_validator

from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_assets import CaseAssetLink, validate_link_set
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.logical_identifier import LogicalIdentifier
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError

INITIAL_LIFECYCLE: Final = CaseLifecycleState.PREFLIGHT


class CaseInvariantError(TrinetraError):
    code = "CASE_INVARIANT_VIOLATED"
    default_message = "the case invariant was violated"


class AssuranceCase(DomainModel):
    case_id: CaseId
    case_label: LogicalIdentifier | None
    lifecycle_state: CaseLifecycleState
    case_state_revision: CaseStateRevision
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    asset_links: tuple[CaseAssetLink, ...]

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.updated_at < self.created_at:
            msg = "updated_at cannot precede created_at"
            raise ValueError(msg)
        try:
            validate_link_set(self.asset_links)
        except TrinetraError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @classmethod
    def create(
        cls, case_id: CaseId, links: tuple[CaseAssetLink, ...], now: UtcTimestamp,
        *, label: LogicalIdentifier | None = None,
    ) -> Self:
        return cls(
            case_id=case_id, case_label=label, lifecycle_state=INITIAL_LIFECYCLE,
            case_state_revision=CaseStateRevision.first(), created_at=now, updated_at=now,
            asset_links=links,
        )

    def touch(self, now: UtcTimestamp) -> Self:
        """The successor after one committed mutation (revision +1, lifecycle and links unchanged)."""
        if now < self.updated_at:
            raise CaseInvariantError("time cannot move backwards")
        return self.evolve(case_state_revision=self.case_state_revision.next(), updated_at=now)


__all__ = ["INITIAL_LIFECYCLE", "AssuranceCase", "CaseInvariantError"]
