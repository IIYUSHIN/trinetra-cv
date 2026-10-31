"""Typed opaque identifiers: ExecutionAttemptId (see trinetra.domain.ids)."""

from __future__ import annotations

from trinetra.domain.counters import Counter
from trinetra.domain.ids import OpaqueId


class ExecutionAttemptId(OpaqueId):
    PREFIX = "attempt"
    __slots__ = ()


class AttemptNumber(Counter):
    """1-based ordinal of an attempt for one scientific JobSpec. Retries of the same JobSpec
    are NOT independent evidence (Bible section 8); the canonical identity of an attempt is
    its ExecutionAttemptId, never the number."""

    KIND = "attempt_number"
    __slots__ = ()


__all__ = ["AttemptNumber", "ExecutionAttemptId"]
