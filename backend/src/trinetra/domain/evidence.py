"""Typed opaque identifiers: EvidenceId, EvidenceGroupId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class EvidenceId(OpaqueId):
    PREFIX = "evid"
    __slots__ = ()


class EvidenceGroupId(OpaqueId):
    PREFIX = "evgrp"
    __slots__ = ()


__all__ = ['EvidenceGroupId', 'EvidenceId']
