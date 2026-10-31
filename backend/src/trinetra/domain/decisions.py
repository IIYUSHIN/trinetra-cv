"""Typed opaque identifiers: AnalystDecisionId, CommandId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class AnalystDecisionId(OpaqueId):
    PREFIX = "anldec"
    __slots__ = ()


class CommandId(OpaqueId):
    PREFIX = "cmd"
    __slots__ = ()


__all__ = ['AnalystDecisionId', 'CommandId']
