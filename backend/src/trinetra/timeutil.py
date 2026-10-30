"""UTC timestamp and monotonic-time primitives (IMP-0007).

Standard library only. These are foundation *helpers*, not the domain
`UtcTimestamp` value object (that is a separate, later packet in I2).

Two kinds of time are kept strictly apart:

* wall time: timezone-aware UTC `datetime`, for timestamps that are recorded
  and shown. It can jump (NTP, manual change) and must never be used to
  measure durations.
* monotonic time: integer nanoseconds from `Clock.monotonic_ns()`, for
  measuring elapsed time and ordering within one process. It has no relation
  to the calendar and must never be turned into a timestamp.

Wire format (PROPOSED; the Bible shows only ``"2026-09-02T14:35:20Z"``):
``YYYY-MM-DDTHH:MM:SSZ``; an explicit microsecond variant
``YYYY-MM-DDTHH:MM:SS.ffffffZ`` exists. Upper-case ``T`` and ``Z`` only, ASCII
digits only, no offsets, no whitespace, no leap seconds, years 0001-9999.
Parsing is the exact inverse of formatting.

Code that needs "now" takes a `Clock`; tests inject `FixedClock`/`ManualClock`.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol

from trinetra.errors import TrinetraError

_SECONDS_RE: Final = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z")
_MICROS_RE: Final = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{6})Z"
)
MIN_EPOCH_SECONDS: Final = 0
MAX_EPOCH_SECONDS: Final = 253_402_300_799  # 9999-12-31T23:59:59Z
MAX_NS: Final = 2**63 - 1

Precision = Literal["seconds", "microseconds"]


class TimeError(TrinetraError):
    code = "TIME_INVALID"
    default_message = "invalid time value"


def _is_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def require_utc(value: object, name: str = "value") -> datetime:
    """Return `value` if it is an aware datetime whose offset is exactly zero.

    Naive datetimes and non-UTC offsets are rejected, never guessed or
    converted; use `to_utc` for an explicit conversion of an aware value.
    """
    if type(value) is not datetime:
        raise TimeError(f"{name} must be a datetime", details={"name": name})
    offset = value.utcoffset()
    if offset is None:
        raise TimeError(f"{name} must be timezone-aware", details={"name": name})
    if offset != timedelta(0):
        raise TimeError(f"{name} must be UTC (offset zero)", details={"name": name})
    return value.replace(tzinfo=UTC)


def to_utc(value: object, name: str = "value") -> datetime:
    """Explicitly convert an aware datetime in any zone to UTC. Naive is rejected."""
    if type(value) is not datetime:
        raise TimeError(f"{name} must be a datetime", details={"name": name})
    if value.utcoffset() is None:
        raise TimeError(f"{name} must be timezone-aware", details={"name": name})
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise TimeError(f"{name} is out of the supported range", details={"name": name}) from exc


def truncate_to_seconds(value: datetime) -> datetime:
    """Explicitly drop sub-second precision (toward the past)."""
    return require_utc(value).replace(microsecond=0)


def _fixed_fields(m: datetime) -> str:
    # Not strftime: %Y is platform-dependent and is not zero-padded below year 1000 on glibc.
    return f"{m.year:04d}-{m.month:02d}-{m.day:02d}T{m.hour:02d}:{m.minute:02d}:{m.second:02d}"


def format_utc(value: datetime, *, precision: Precision = "seconds") -> str:
    """Format a UTC datetime. Refuses to drop sub-second data silently."""
    moment = require_utc(value)
    if precision == "seconds":
        if moment.microsecond:
            raise TimeError(
                "value has sub-second precision; truncate_to_seconds() or use precision='microseconds'",
                details={"name": "value"},
            )
        return _fixed_fields(moment) + "Z"
    if precision == "microseconds":
        return f"{_fixed_fields(moment)}.{moment.microsecond:06d}Z"
    raise TimeError("precision must be 'seconds' or 'microseconds'", details={"name": "precision"})


def parse_utc(text: object) -> datetime:
    """Strictly parse the wire format; the exact inverse of `format_utc`."""
    if not isinstance(text, str):
        raise TimeError("timestamp must be a string", details={"name": "timestamp"})
    match = _SECONDS_RE.fullmatch(text) or _MICROS_RE.fullmatch(text)
    if match is None:
        raise TimeError("timestamp is not in the strict UTC format", details={"name": "timestamp"})
    groups = match.groups()
    year, month, day, hour, minute, second = (int(g) for g in groups[:6])
    micro = int(groups[6]) if len(groups) == 7 else 0
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError as exc:  # month 13, day 31 of April, hour 24, second 60 ...
        raise TimeError(f"timestamp is not a real instant: {exc}", details={"name": "timestamp"}) from exc


def utc_from_epoch_seconds(seconds: object) -> datetime:
    """Whole epoch seconds (0 .. 9999-12-31T23:59:59Z) to an aware UTC datetime."""
    if not _is_exact_int(seconds):
        raise TimeError("epoch seconds must be an int", details={"name": "seconds"})
    if not MIN_EPOCH_SECONDS <= seconds <= MAX_EPOCH_SECONDS:  # type: ignore[operator]
        raise TimeError("epoch seconds out of range", details={"name": "seconds"})
    return datetime.fromtimestamp(seconds, UTC)  # type: ignore[arg-type]


def epoch_seconds(value: datetime) -> int:
    """Whole epoch seconds of a UTC datetime; refuses sub-second values."""
    moment = require_utc(value)
    if moment.microsecond:
        raise TimeError("value has sub-second precision", details={"name": "value"})
    seconds = int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())
    if not MIN_EPOCH_SECONDS <= seconds <= MAX_EPOCH_SECONDS:
        raise TimeError("value is before the Unix epoch", details={"name": "value"})
    return seconds


class Clock(Protocol):
    """Injectable source of both kinds of time."""

    def now(self) -> datetime: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    """The real clocks. `now()` is validated UTC; `monotonic_ns()` never goes back."""

    def now(self) -> datetime:
        return require_utc(datetime.now(UTC), "system clock")

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class FixedClock:
    """Wall time frozen at one instant; monotonic time frozen too."""

    def __init__(self, moment: datetime, monotonic_ns: int = 0) -> None:
        self._moment = require_utc(moment, "moment")
        self._mono = _check_ns(monotonic_ns)

    def now(self) -> datetime:
        return self._moment

    def monotonic_ns(self) -> int:
        return self._mono


class ManualClock:
    """Test clock that only moves when told to.

    `advance` moves both clocks. `set_wall` jumps wall time (forwards or
    backwards) without touching the monotonic reading - exactly the situation
    in which wall time must not be used to measure a duration.
    """

    def __init__(self, moment: datetime, monotonic_ns: int = 0) -> None:
        self._moment = require_utc(moment, "moment")
        self._mono = _check_ns(monotonic_ns)

    def now(self) -> datetime:
        return self._moment

    def monotonic_ns(self) -> int:
        return self._mono

    def advance(self, delta: timedelta) -> None:
        if type(delta) is not timedelta or delta < timedelta(0):
            raise TimeError("advance needs a non-negative timedelta", details={"name": "delta"})
        ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000
        new_mono = _check_ns(self._mono + ns)
        try:
            self._moment = self._moment + delta
        except OverflowError as exc:
            raise TimeError("clock would leave the supported range", details={"name": "delta"}) from exc
        self._mono = new_mono

    def set_wall(self, moment: datetime) -> None:
        self._moment = require_utc(moment, "moment")


def _check_ns(value: object) -> int:
    if not _is_exact_int(value) or not 0 <= value <= MAX_NS:  # type: ignore[operator]
        raise TimeError("monotonic reading must be an int in [0, 2**63-1]", details={"name": "ns"})
    return value  # type: ignore[return-value]


def elapsed_ns(start_ns: object, end_ns: object) -> int:
    """Duration between two monotonic readings; a reversed pair is an error."""
    start, end = _check_ns(start_ns), _check_ns(end_ns)
    if end < start:
        raise TimeError("monotonic readings are reversed", details={"name": "end_ns"})
    return end - start


class Stopwatch:
    """Measures elapsed monotonic time against an injected `Clock`."""

    __slots__ = ("_clock", "_start")

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._start = _check_ns(clock.monotonic_ns())

    def elapsed_ns(self) -> int:
        return elapsed_ns(self._start, self._clock.monotonic_ns())
