"""Typed opaque identifiers: ReferenceId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId
from trinetra.domain.tokens import CanonicalToken


class ReferenceId(OpaqueId):
    PREFIX = "ref"
    __slots__ = ()


class ReferencePurpose(CanonicalToken):
    """Why a reference exists (e.g. what it may be used to check). Open vocabulary:
    approving a reference for a purpose is a Core decision, never implied by upload."""

    KIND = "reference_purpose"
    __slots__ = ()


__all__ = ["ReferenceId", "ReferencePurpose"]
