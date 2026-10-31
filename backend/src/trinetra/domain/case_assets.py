"""Case asset roles and links (IMP-0082).

A `CaseAssetLink` says which admitted asset plays which role in a Case. Roles are a closed lowercase
vocabulary that mirrors the package parts an intake can carry; each maps to exactly one `AssetKind`
(`role_for_kind`). Kinds produced later by the system (inference output, inference receipt) are not
package roles and cannot be linked at commit.

Cardinality is the frozen commit rule (EIB-04): exactly one dataset and one model, at most one
contributor metadata and one assessment profile, any number of references and inference records.
`link_assets` is the only constructor of a link set: it numbers each role's links 1..n in the order
given (the deterministic commit-plan order), rejects a repeated asset (an asset has one role in a Case)
and enforces the cardinality, so an invalid set cannot be built.

PROPOSED (ERR-CL-022): role names and the 1-based per-role `position`; one role per asset per Case; the
per-role cap `MAX_LINKS_PER_ROLE` as an abuse bound matching the intake transfer bound.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Final

from pydantic import Field, model_validator

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId
from trinetra.domain.enums import WireEnum
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError

MAX_LINKS_PER_ROLE: Final = 1_000


class CaseAssetRole(WireEnum):
    DATASET = "dataset"
    MODEL = "model"
    CONTRIBUTOR_METADATA = "contributor_metadata"
    REFERENCE = "reference"
    INFERENCE_RECORD = "inference_record"
    ASSESSMENT_PROFILE = "assessment_profile"


class CaseAssetLinkError(TrinetraError):
    code = "CASE_ASSET_LINKS_INVALID"
    default_message = "the case asset links are invalid"


# role -> (minimum, maximum) links per Case
ROLE_CARDINALITY: Final = MappingProxyType({
    CaseAssetRole.DATASET: (1, 1),
    CaseAssetRole.MODEL: (1, 1),
    CaseAssetRole.CONTRIBUTOR_METADATA: (0, 1),
    CaseAssetRole.REFERENCE: (0, MAX_LINKS_PER_ROLE),
    CaseAssetRole.INFERENCE_RECORD: (0, MAX_LINKS_PER_ROLE),
    CaseAssetRole.ASSESSMENT_PROFILE: (0, 1),
})
_ROLE_BY_KIND: Final = MappingProxyType({
    AssetKind.DATASET: CaseAssetRole.DATASET,
    AssetKind.MODEL: CaseAssetRole.MODEL,
    AssetKind.CONTRIBUTOR_METADATA: CaseAssetRole.CONTRIBUTOR_METADATA,
    AssetKind.REFERENCE: CaseAssetRole.REFERENCE,
    AssetKind.INFERENCE_RECORD: CaseAssetRole.INFERENCE_RECORD,
    AssetKind.ASSESSMENT_PROFILE: CaseAssetRole.ASSESSMENT_PROFILE,
})


def role_for_kind(kind: AssetKind) -> CaseAssetRole:
    role = _ROLE_BY_KIND.get(kind)
    if role is None:
        raise CaseAssetLinkError("this asset kind has no case role", details={"kind": kind.to_wire()})
    return role


class CaseAssetLink(DomainModel):
    case_id: CaseId
    asset_id: AssetId
    role: CaseAssetRole
    position: int = Field(strict=True, ge=1, le=MAX_LINKS_PER_ROLE)  # 1-based, within the role
    linked_at: UtcTimestamp

    @model_validator(mode="after")
    def _within_cardinality(self) -> CaseAssetLink:
        if self.position > ROLE_CARDINALITY[self.role][1]:
            msg = "position exceeds the role's maximum"
            raise ValueError(msg)
        return self


def link_assets(
    case_id: CaseId, entries: Iterable[tuple[CaseAssetRole, AssetId]], linked_at: UtcTimestamp
) -> tuple[CaseAssetLink, ...]:
    """Build the full, valid link set for a new Case from (role, asset) pairs in plan order."""
    items = list(entries)
    seen: set[AssetId] = set()
    counts = dict.fromkeys(CaseAssetRole, 0)
    links: list[CaseAssetLink] = []
    for role, asset_id in items:
        if asset_id in seen:
            raise CaseAssetLinkError("an asset can hold only one role in a case")
        seen.add(asset_id)
        counts[role] += 1
        if counts[role] > ROLE_CARDINALITY[role][1]:
            raise CaseAssetLinkError("too many assets for a role", details={"role": role.to_wire()})
        links.append(CaseAssetLink(
            case_id=case_id, asset_id=asset_id, role=role, position=counts[role], linked_at=linked_at,
        ))
    missing = [r.to_wire() for r, (lo, _) in ROLE_CARDINALITY.items() if counts[r] < lo]
    if missing:
        raise CaseAssetLinkError("required roles are missing", details={"roles": missing})
    return tuple(links)


__all__ = [
    "MAX_LINKS_PER_ROLE", "ROLE_CARDINALITY", "CaseAssetLink", "CaseAssetLinkError", "CaseAssetRole",
    "link_assets", "role_for_kind",
]
