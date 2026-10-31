"""Typed opaque identifiers: ClaimDefinitionId, ClaimSnapshotId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class ClaimDefinitionId(OpaqueId):
    PREFIX = "claimdef"
    __slots__ = ()


class ClaimSnapshotId(OpaqueId):
    PREFIX = "claimsn"
    __slots__ = ()


__all__ = ['ClaimDefinitionId', 'ClaimSnapshotId']
