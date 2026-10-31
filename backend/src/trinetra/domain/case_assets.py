"""Case asset roles and links (IMP-0082).

A `CaseAssetLink` says which admitted asset plays which role in a Case. Roles are a closed lowercase
vocabulary that mirrors the package parts an intake can carry; each maps to exactly one `AssetKind`
(`role_for_kind`). Kinds produced later by the system (inference output, inference receipt) are not
package roles and cannot be linked at commit.

Cardinality is the frozen commit rule (EIB-04): exactly one dataset and one model, at most one
contributor metadata and one assessment profile, any number of references and inference records. The
dataset and the model sit at ordinal 0. `link_assets` is the only constructor of a link set: it numbers
each role's links 0..n-1 in the order given (the deterministic commit-plan order), rejects a repeated
asset and enforces the cardinality, so an invalid set cannot be built. `validate_link_set` re-checks a
set read back from storage by the same rules.

PROPOSED (ERR-CL-022): an asset holds one role per Case (its kind fixes the role anyway); the per-role
cap `MAX_LINKS_PER_ROLE` is an abuse bound that matches the intake transfer bound.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from types import MappingProxyType
from typing import Final

from pydantic import Field

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import DomainModel
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
    asset_id: AssetId
    role: CaseAssetRole
    ordinal: int = Field(strict=True, ge=0, lt=MAX_LINKS_PER_ROLE)  # 0-based, within the role
    linked_at: UtcTimestamp


_ROLE_RANK: Final = {role: rank for rank, role in enumerate(CaseAssetRole)}


def link_sort_key(link: CaseAssetLink) -> tuple[int, int]:
    return _ROLE_RANK[link.role], link.ordinal


def link_assets(
    entries: Iterable[tuple[CaseAssetRole, AssetId]], linked_at: UtcTimestamp
) -> tuple[CaseAssetLink, ...]:
    """Build the full, valid link set for a new Case from (role, asset) pairs in plan order.

    The result is in canonical order (role in the order of `CaseAssetRole`, then ordinal), so the same
    package always yields the same tuple, hence the same wire bytes."""
    counts = dict.fromkeys(CaseAssetRole, 0)
    links: list[CaseAssetLink] = []
    for role, asset_id in entries:
        if counts[role] >= ROLE_CARDINALITY[role][1]:
            raise CaseAssetLinkError("too many assets for a role", details={"role": role.to_wire()})
        links.append(CaseAssetLink(asset_id=asset_id, role=role, ordinal=counts[role], linked_at=linked_at))
        counts[role] += 1
    links.sort(key=link_sort_key)
    validate_link_set(links)
    return tuple(links)


def validate_link_set(links: Sequence[CaseAssetLink]) -> None:
    """Raise `CaseAssetLinkError` unless `links` is a complete, correctly numbered set."""
    if list(links) != sorted(links, key=link_sort_key):
        raise CaseAssetLinkError("links must be in canonical order (role, then ordinal)")
    seen: set[AssetId] = set()
    ordinals: dict[CaseAssetRole, list[int]] = {role: [] for role in CaseAssetRole}
    for link in links:
        if link.asset_id in seen:
            raise CaseAssetLinkError("an asset can hold only one role in a case")
        seen.add(link.asset_id)
        ordinals[link.role].append(link.ordinal)
    for role, (minimum, maximum) in ROLE_CARDINALITY.items():
        found = sorted(ordinals[role])
        if len(found) > maximum:
            raise CaseAssetLinkError("too many assets for a role", details={"role": role.to_wire()})
        if len(found) < minimum:
            raise CaseAssetLinkError("a required role is missing", details={"role": role.to_wire()})
        if found != list(range(len(found))):
            raise CaseAssetLinkError("ordinals must run 0..n-1", details={"role": role.to_wire()})


__all__ = [
    "MAX_LINKS_PER_ROLE", "ROLE_CARDINALITY", "CaseAssetLink", "CaseAssetLinkError", "CaseAssetRole",
    "link_assets", "link_sort_key", "role_for_kind", "validate_link_set",
]
