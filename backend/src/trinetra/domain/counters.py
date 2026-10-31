"""Bounded positive counters: revisions and attempt numbers (IMP-0018, IMP-0022).

`case_id` and `case_state_revision` are explicit, separate projection fields; a
generic `source_revision` is forbidden (Bible lines 297, 486, 608). A counter
starts at 1, only moves forward by exactly 1 (`next`), is a plain JSON integer on
the wire, and is limited to the I-JSON safe range so JCS can carry it.

Starting value 1 and the +1 step are PROPOSED engineering decisions.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from trinetra.errors import TrinetraError

MAX_COUNTER: Final = 2**53 - 1


class CounterError(TrinetraError):
    code = "COUNTER_INVALID"
    default_message = "invalid counter value"


class Counter:
    """Immutable integer in [1, 2**53-1]; subclass for each distinct meaning."""

    __slots__ = ("_n",)
    _n: int
    KIND: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not isinstance(cls.__dict__.get("KIND"), str) or "__slots__" not in cls.__dict__:
            raise CounterError(f"{cls.__name__}: needs its own KIND and __slots__ = ()")

    def __init__(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CounterError(f"{type(self).KIND}: expected int, got {type(value).__name__}")
        if not 1 <= value <= MAX_COUNTER:
            shown = value if abs(value) <= MAX_COUNTER else "beyond I-JSON range"
            raise CounterError(f"{type(self).KIND}: must be within [1, {MAX_COUNTER}]",
                               details={"value": shown})
        object.__setattr__(self, "_n", value)

    @property
    def value(self) -> int:
        return self._n

    @classmethod
    def first(cls) -> Self:
        return cls(1)

    def next(self) -> Self:
        """The successor; raises at the I-JSON limit rather than wrapping."""
        return type(self)(self._n + 1)

    def __int__(self) -> int:
        return self._n

    def __index__(self) -> int:
        raise TypeError(f"{type(self).KIND} is not an integer; use .value explicitly")

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other._n == self._n

    def __lt__(self, other: object) -> bool:
        return self._ordered(other) and self._n < other._n  # type: ignore[attr-defined]

    def __le__(self, other: object) -> bool:
        return self._ordered(other) and self._n <= other._n  # type: ignore[attr-defined]

    def __gt__(self, other: object) -> bool:
        return self._ordered(other) and self._n > other._n  # type: ignore[attr-defined]

    def __ge__(self, other: object) -> bool:
        return self._ordered(other) and self._n >= other._n  # type: ignore[attr-defined]

    def _ordered(self, other: object) -> bool:
        if type(other) is not type(self):
            raise TypeError(f"cannot order {type(self).__name__} against {type(other).__name__}")
        return True

    def __hash__(self) -> int:
        return hash((type(self).__qualname__, self._n))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._n})"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self._n,))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    def to_wire(self) -> int:
        return self._n

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(raw: int) -> Counter:
            try:
                return cls(raw)
            except CounterError as exc:
                raise ValueError(exc.message) from exc

        from_int = core_schema.chain_schema(
            [core_schema.int_schema(strict=True), core_schema.no_info_plain_validator_function(validate)]
        )
        return core_schema.json_or_python_schema(
            json_schema=from_int,
            python_schema=core_schema.union_schema([core_schema.is_instance_schema(cls), from_int]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                int, return_schema=core_schema.int_schema()
            ),
        )


__all__ = ["MAX_COUNTER", "Counter", "CounterError"]
