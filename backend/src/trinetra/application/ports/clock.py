"""ClockPort: the application's only source of time (whole-second UTC timestamps)."""

from __future__ import annotations

from typing import Protocol

from trinetra.domain.timing import UtcTimestamp


class ClockPort(Protocol):
    def now(self) -> UtcTimestamp: ...


__all__ = ["ClockPort"]
