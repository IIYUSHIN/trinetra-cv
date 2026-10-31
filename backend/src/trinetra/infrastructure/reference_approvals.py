"""StoreApprovedReferences: the ApprovedReferencesPort over the Core database (IMP-0098).

Reads, in one read-only unit of work, the references that are usable now for the assets a Case links in the
reference role, and returns their purposes. It never writes and never infers approval from an upload: a
reference asset with no authority record contributes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trinetra.domain.case_assets import CaseAssetRole

if TYPE_CHECKING:
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.references import ReferencePurpose
    from trinetra.domain.timing import UtcTimestamp
    from trinetra.infrastructure.reference_repository import ReferenceRepository
    from trinetra.infrastructure.unit_of_work import CoreStore


class StoreApprovedReferences:
    def __init__(self, store: CoreStore, references: ReferenceRepository) -> None:
        self._store = store
        self._references = references

    def approved_purposes(self, case: AssuranceCase, now: UtcTimestamp) -> frozenset[ReferencePurpose]:
        linked = [link.asset_id for link in case.asset_links if link.role is CaseAssetRole.REFERENCE]
        if not linked:
            return frozenset()
        with self._store.unit_of_work() as uow:
            usable = self._references.usable_for_assets(uow, linked, now)
            uow.rollback()
        return frozenset(record.purpose for record in usable)


__all__ = ["StoreApprovedReferences"]
