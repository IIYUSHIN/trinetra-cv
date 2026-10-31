"""Typed opaque identifiers for the intake workflow: IntakeId, TransferId, QuarantineHandle.

PROPOSED prefixes (ERR-CL-022): `intake`, `transfer`, `quar`. A QuarantineHandle names an incoming
byte stream awaiting admission; it is an identifier, never a path (see trinetra.infrastructure).
"""

from __future__ import annotations

from trinetra.domain.ids import OpaqueId


class IntakeId(OpaqueId):
    PREFIX = "intake"
    __slots__ = ()


class TransferId(OpaqueId):
    PREFIX = "transfer"
    __slots__ = ()


class QuarantineHandle(OpaqueId):
    PREFIX = "quar"
    __slots__ = ()


__all__ = ["IntakeId", "QuarantineHandle", "TransferId"]
