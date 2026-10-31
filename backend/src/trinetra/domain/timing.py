"""UtcTimestamp and bounded Duration (IMP-0028).

Both delegate grammar and arithmetic limits to trinetra.timeutil. PROPOSED canonical rule:
an instant has exactly ONE wire spelling - `YYYY-MM-DDTHH:MM:SSZ` when it has no
microseconds, otherwise `YYYY-MM-DDTHH:MM:SS.ffffffZ`; the `.000000Z` spelling of a whole
second is rejected so equal instants always serialise (and hash) identically.
`Duration` is an integer number of microseconds in [0, 2^53-1] (no floats: determinism);
negative spans are impossible by construction, so an `until` that would go backwards raises.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from trinetra.errors import TrinetraError
from trinetra.timeutil import TimeError, format_utc, parse_utc, require_utc

MAX_DURATION_US = 2**53 - 1
_US = timedelta(microseconds=1)


class TimingError(TrinetraError):
    code = "TIMING_INVALID"
    default_message = "invalid timestamp or duration"


def _wrap(exc: TimeError) -> TimingError:
    return TimingError(exc.message)


class UtcTimestamp:
    """Immutable instant in UTC with microsecond precision, years 1970..9999 (timeutil limits)."""

    __slots__ = ("_dt",)
    _dt: datetime

    def __init__(self, moment: datetime) -> None:
        try:
            checked = require_utc(moment, "timestamp")
        except TimeError as exc:
            raise _wrap(exc) from exc
        if checked.timestamp() < 0 or checked.year > 9999:
            raise TimingError("timestamp outside 1970-01-01T00:00:00Z .. 9999-12-31T23:59:59.999999Z")
        object.__setattr__(self, "_dt", checked)

    @classmethod
    def parse(cls, text: object) -> Self:
        try:
            moment = parse_utc(text)
        except TimeError as exc:
            raise _wrap(exc) from exc
        if isinstance(text, str) and text.endswith(".000000Z"):
            raise TimingError("whole seconds must be written without the .000000 fraction")
        return cls(moment)

    @property
    def moment(self) -> datetime:
        return self._dt

    def to_wire(self) -> str:
        return format_utc(self._dt, precision="microseconds" if self._dt.microsecond else "seconds")

    def plus(self, duration: Duration) -> UtcTimestamp:
        try:
            return UtcTimestamp(self._dt + timedelta(microseconds=duration.microseconds))
        except OverflowError as exc:
            raise TimingError("timestamp overflow") from exc

    def until(self, later: UtcTimestamp) -> Duration:
        if later._dt < self._dt:
            raise TimingError("until(): the other timestamp is earlier")
        return Duration((later._dt - self._dt) // _US)

    def _cmp(self, other: object) -> datetime:
        if type(other) is not UtcTimestamp:
            raise TypeError(f"cannot order UtcTimestamp against {type(other).__name__}")
        return other._dt

    def __lt__(self, other: object) -> bool:
        return self._dt < self._cmp(other)

    def __le__(self, other: object) -> bool:
        return self._dt <= self._cmp(other)

    def __gt__(self, other: object) -> bool:
        return self._dt > self._cmp(other)

    def __ge__(self, other: object) -> bool:
        return self._dt >= self._cmp(other)

    def __eq__(self, other: object) -> bool:
        return type(other) is UtcTimestamp and other._dt == self._dt

    def __hash__(self) -> int:
        return hash(("UtcTimestamp", self._dt))

    def __str__(self) -> str:
        return self.to_wire()

    def __repr__(self) -> str:
        return f"UtcTimestamp({self.to_wire()!r})"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("UtcTimestamp is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("UtcTimestamp is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (UtcTimestamp, (self._dt,))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(text: str) -> UtcTimestamp:
            try:
                return cls.parse(text)
            except TimingError as exc:
                raise ValueError(exc.message) from exc

        from_str = core_schema.chain_schema(
            [core_schema.str_schema(strict=True), core_schema.no_info_plain_validator_function(validate)]
        )
        return core_schema.json_or_python_schema(
            json_schema=from_str,
            python_schema=core_schema.union_schema([core_schema.is_instance_schema(cls), from_str]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls.to_wire, return_schema=core_schema.str_schema()
            ),
        )


class Duration:
    """Immutable non-negative span as integer microseconds."""

    __slots__ = ("_us",)
    _us: int

    def __init__(self, microseconds: int) -> None:
        if isinstance(microseconds, bool) or not isinstance(microseconds, int):
            raise TimingError(f"Duration needs int microseconds, got {type(microseconds).__name__}")
        if not 0 <= microseconds <= MAX_DURATION_US:
            raise TimingError(f"Duration must be within [0, {MAX_DURATION_US}] microseconds")
        object.__setattr__(self, "_us", microseconds)

    @classmethod
    def from_seconds(cls, seconds: int) -> Self:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise TimingError("from_seconds needs a non-negative int")
        return cls(seconds * 1_000_000)

    @property
    def microseconds(self) -> int:
        return self._us

    def to_wire(self) -> int:
        return self._us

    def _cmp(self, other: object) -> int:
        if type(other) is not Duration:
            raise TypeError(f"cannot order Duration against {type(other).__name__}")
        return other._us

    def __lt__(self, other: object) -> bool:
        return self._us < self._cmp(other)

    def __le__(self, other: object) -> bool:
        return self._us <= self._cmp(other)

    def __gt__(self, other: object) -> bool:
        return self._us > self._cmp(other)

    def __ge__(self, other: object) -> bool:
        return self._us >= self._cmp(other)

    def __eq__(self, other: object) -> bool:
        return type(other) is Duration and other._us == self._us

    def __hash__(self) -> int:
        return hash(("Duration", self._us))

    def __repr__(self) -> str:
        return f"Duration({self._us})"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Duration is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Duration is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (Duration, (self._us,))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(value: int) -> Duration:
            try:
                return cls(value)
            except TimingError as exc:
                raise ValueError(exc.message) from exc

        from_int = core_schema.chain_schema(
            [core_schema.int_schema(strict=True), core_schema.no_info_plain_validator_function(validate)]
        )
        return core_schema.json_or_python_schema(
            json_schema=from_int,
            python_schema=core_schema.union_schema([core_schema.is_instance_schema(cls), from_int]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls.to_wire, return_schema=core_schema.int_schema()
            ),
        )


__all__ = ["MAX_DURATION_US", "Duration", "TimingError", "UtcTimestamp"]
