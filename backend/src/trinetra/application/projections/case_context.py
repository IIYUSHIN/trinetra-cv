"""CaseContextProjection and its builder (IMP-0092).

Source contract (EIB-04): the context of one Case - identity, lifecycle position, revision and its two
primary assets (dataset and model) - plus a fixed set of fields that exist now so the wire shape does not
change later but are explicitly `null` until something real can fill them: principal claim, assurance
sufficiency, policy recommendation, analyst disposition, freshness, attention category/reason and coverage
summary. A missing value is never a default and never a zero.

The builder checks that the dataset and model records it is given are the ones the Case links; a mismatch
raises `ProjectionInconsistentError` rather than showing the wrong asset under a Case.

PROPOSED (ERR-CL-022): each primary asset's `entity_type` is its asset kind (`dataset` / `model`), the label
is the supplied name (or the kind word when none was supplied), and the digest is the blob digest.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field

from trinetra.application.projections.actions import available_actions
from trinetra.application.projections.limitations import critical_limitations
from trinetra.application.projections.shared import (
    ActionAvailability,
    ProjectionInconsistentError,
    ProjectionMeta,
)
from trinetra.domain.asset_record import AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.base import DomainModel
from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.entity import EntityRef, EntityType
from trinetra.domain.limitations import Limitation
from trinetra.domain.logical_identifier import LogicalIdentifier
from trinetra.domain.timing import UtcTimestamp

MAX_ITEMS: Final = 64


class CaseContextProjection(DomainModel):
    meta: ProjectionMeta
    case_id: CaseId
    case_label: LogicalIdentifier | None
    case_lifecycle_state: CaseLifecycleState
    case_state_revision: CaseStateRevision
    primary_assets: Annotated[tuple[EntityRef, ...], Field(min_length=2, max_length=2)]
    # Reserved: present on the wire, explicitly null until a real value can exist.
    principal_claim: None = None
    assurance_sufficiency: None = None
    policy_recommendation: None = None
    analyst_disposition: None = None
    freshness: None = None
    attention_category: None = None
    attention_reason: None = None
    coverage_summary: None = None
    critical_limitations: Annotated[tuple[Limitation, ...], Field(max_length=MAX_ITEMS)]
    available_actions: Annotated[tuple[ActionAvailability, ...], Field(max_length=MAX_ITEMS)]


def _linked(case: AssuranceCase, role: CaseAssetRole) -> AssetId:
    ids = [link.asset_id for link in case.asset_links if link.role is role]
    if len(ids) != 1:  # unreachable for a valid Case; refuse rather than guess
        raise ProjectionInconsistentError("the case does not link exactly one asset for a primary role",
                                          details={"role": role.to_wire()})
    return ids[0]


def _asset_ref(case: AssuranceCase, role: CaseAssetRole, kind: AssetKind, record: AssetRecord) -> EntityRef:
    if record.record_id != _linked(case, role) or record.asset_kind is not kind:
        raise ProjectionInconsistentError("the asset record is not the one the case links for this role",
                                          details={"role": role.to_wire()})
    return EntityRef(entity_type=EntityType(kind.to_wire()), entity_id=str(record.record_id),
                     display_label=record.supplied_name or kind.to_wire(), digest=record.blob_digest)


def build_case_context(
    case: AssuranceCase, dataset: AssetRecord, model: AssetRecord, snapshot: CapabilitySnapshot,
    generated_at: UtcTimestamp,
) -> CaseContextProjection:
    if snapshot.case_id != case.case_id or snapshot.case_state_revision != case.case_state_revision:
        raise ProjectionInconsistentError("the capability snapshot is not for this case at this revision")
    return CaseContextProjection(
        meta=ProjectionMeta.at(generated_at), case_id=case.case_id, case_label=case.case_label,
        case_lifecycle_state=case.lifecycle_state, case_state_revision=case.case_state_revision,
        primary_assets=(
            _asset_ref(case, CaseAssetRole.DATASET, AssetKind.DATASET, dataset),
            _asset_ref(case, CaseAssetRole.MODEL, AssetKind.MODEL, model),
        ),
        critical_limitations=critical_limitations(case, snapshot),
        available_actions=available_actions(case, snapshot),
    )


__all__ = ["CaseContextProjection", "build_case_context"]
