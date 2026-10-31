"""PreflightQueryService (IMP-0099): assemble the read-side views of one stored Case.

Reads the Case, the records of its dataset and model, and which reference purposes are approved for it
right now; asks the PreflightPlanner what this build can honestly do; and hands the facts to the pure
projection builders. It never writes, never calculates a capability state itself and never reads a view it
has not just built, so a view always reflects the Case revision it was built from.
"""

from __future__ import annotations

from trinetra.application.errors import UnknownCaseError
from trinetra.application.ports.cases import CaseStorePort
from trinetra.application.ports.clock import ClockPort
from trinetra.application.ports.references import ApprovedReferencesPort
from trinetra.application.ports.storage import AssetRecordReaderPort
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.case_context import CaseContextProjection, build_case_context
from trinetra.application.projections.preflight import PreflightProjection, build_preflight
from trinetra.application.projections.shared import ProjectionInconsistentError
from trinetra.domain.asset_record import AssetRecord
from trinetra.domain.assets import AssetId
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.case import CaseId
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.timing import UtcTimestamp


class PreflightQueryService:
    def __init__(
        self, cases: CaseStorePort, assets: AssetRecordReaderPort, references: ApprovedReferencesPort,
        planner: PreflightPlanner, clock: ClockPort,
    ) -> None:
        self._cases = cases
        self._assets = assets
        self._references = references
        self._planner = planner
        self._clock = clock

    def case_context(self, case_id: CaseId) -> CaseContextProjection:
        case, dataset, model, snapshot, _approved, now = self._facts(case_id)
        return build_case_context(case, dataset, model, snapshot, now)

    def preflight(self, case_id: CaseId) -> PreflightProjection:
        case, dataset, model, snapshot, approved, now = self._facts(case_id)
        return build_preflight(case, dataset, model, snapshot, approved_purposes=approved, generated_at=now)

    def _facts(
        self, case_id: CaseId
    ) -> tuple[AssuranceCase, AssetRecord, AssetRecord, CapabilitySnapshot, frozenset[ReferencePurpose],
               UtcTimestamp]:
        case = self._cases.get(case_id)
        if case is None:
            raise UnknownCaseError(details={"case_id": str(case_id)})
        now = self._clock.now()
        dataset = self._record(case, CaseAssetRole.DATASET)
        model = self._record(case, CaseAssetRole.MODEL)
        approved = self._references.approved_purposes(case, now)
        return case, dataset, model, self._planner.plan(case, approved), approved, now

    def _record(self, case: AssuranceCase, role: CaseAssetRole) -> AssetRecord:
        asset_id: AssetId = next(link.asset_id for link in case.asset_links if link.role is role)
        record = self._assets.get(asset_id)
        if record is None:
            raise ProjectionInconsistentError(
                "a case links an asset that has no record",
                details={"case_id": str(case.case_id), "role": role.to_wire(), "asset_id": str(asset_id)},
            )
        return record


__all__ = ["PreflightQueryService"]
