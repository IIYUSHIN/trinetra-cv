"""Typed opaque identifiers: PolicyProfileId, PolicyDecisionId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class PolicyProfileId(OpaqueId):
    PREFIX = "polprof"
    __slots__ = ()


class PolicyDecisionId(OpaqueId):
    PREFIX = "poldec"
    __slots__ = ()


__all__ = ['PolicyDecisionId', 'PolicyProfileId']
