"""Base for closed domain enumerations with an exact lowercase/uppercase wire form.

Every state dimension in the Bible (TestVerdict, ClaimState, ...) is its own closed enum;
none may be merged into a generic status (Bible section 4 / 7). `WireEnum` gives them one
validation behaviour: in a `DomainModel` (strict) a field accepts the enum member itself or
its exact wire string - never a different case, an int, or a member of another enum - and
serialises to the wire string. Members are compared by identity of type: two enums never
compare equal even when their values coincide (StrEnum would otherwise let them equal a bare str).
"""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import Any, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

_UNORDERED = "wire enum members are unordered; ranking must be an explicit named rule"


class WireEnum(StrEnum):
    """StrEnum whose pydantic schema is strict about the exact wire value."""

    @classmethod
    def from_wire(cls, text: object) -> Self:
        if not isinstance(text, str) or isinstance(text, Enum):
            raise ValueError(f"{cls.__name__}: expected the exact wire string")  # noqa: TRY004
        for member in cls:
            if member.value == text:
                return member
        raise ValueError(f"{cls.__name__}: {text!r} is not a member")

    def to_wire(self) -> str:
        return str(self.value)

    def __eq__(self, other: object) -> bool:  # members of different enums, or bare strings, are not equal
        return type(other) is type(self) and other.value == self.value

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash((type(self).__qualname__, self.value))

    # Raising (not NotImplemented): NotImplemented lets the reflected str comparison answer for a bare string.
    # StrEnum inherits str ordering, so `PASS < FAIL` would silently compare spellings. These
    # dimensions are unordered: any ranking is an explicit, named domain rule elsewhere.
    def __lt__(self, other: object) -> bool:
        raise TypeError(_UNORDERED)

    def __le__(self, other: object) -> bool:
        raise TypeError(_UNORDERED)

    def __gt__(self, other: object) -> bool:
        raise TypeError(_UNORDERED)

    def __ge__(self, other: object) -> bool:
        raise TypeError(_UNORDERED)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(value: object) -> WireEnum:
            if isinstance(value, cls):
                return value
            if type(value) is not str:  # exact str only: no subclasses (e.g. another enum's member)
                raise ValueError(f"{cls.__name__}: expected the exact wire string")
            return cls.from_wire(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls.to_wire, return_schema=core_schema.str_schema()
            ),
        )


__all__ = ["WireEnum"]
