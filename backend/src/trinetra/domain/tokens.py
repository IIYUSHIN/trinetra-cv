"""Canonical lowercase tokens (used by ReferencePurpose and later capability/access names).

The corpus uses `CanonicalToken` as a field type but the available sources do not define
its grammar (ERR-CL-002, PROVISIONAL). The grammar below is a PROPOSED engineering
decision, consistent with "wire forms lowercase": 1..64 characters, ASCII lowercase
letters/digits with single underscores between groups, starting with a letter. A
`CanonicalToken` subclass is a distinct type: tokens of different meanings never compare
equal, even with identical text.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Final, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from trinetra.errors import TrinetraError

MAX_TOKEN_LENGTH: Final = 64
_TOKEN_RE: Final = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


class TokenError(TrinetraError):
    code = "TOKEN_INVALID"
    default_message = "invalid canonical token"


class CanonicalToken:
    __slots__ = ("_text",)
    _text: str
    KIND: ClassVar[str] = "canonical_token"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "__slots__" not in cls.__dict__ or not isinstance(cls.__dict__.get("KIND"), str):
            raise TokenError(f"{cls.__name__}: needs its own KIND and __slots__ = ()")

    def __init__(self, text: str) -> None:
        if type(text) is not str:
            raise TokenError(f"{type(self).KIND}: expected str, got {type(text).__name__}")
        if not 1 <= len(text) <= MAX_TOKEN_LENGTH or not _TOKEN_RE.fullmatch(text):
            raise TokenError(f"{type(self).KIND}: must be 1..{MAX_TOKEN_LENGTH} chars of "
                             "lowercase letters/digits with single underscores, starting with a letter")
        object.__setattr__(self, "_text", text)

    @property
    def text(self) -> str:
        return self._text

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._text!r})"

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other._text == self._text

    def __hash__(self) -> int:
        return hash((type(self).__qualname__, self._text))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self._text,))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    def to_wire(self) -> str:
        return self._text

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(text: str) -> CanonicalToken:
            try:
                return cls(text)
            except TokenError as exc:
                raise ValueError(exc.message) from exc

        from_str = core_schema.chain_schema(
            [core_schema.str_schema(strict=True), core_schema.no_info_plain_validator_function(validate)]
        )
        return core_schema.json_or_python_schema(
            json_schema=from_str,
            python_schema=core_schema.union_schema([core_schema.is_instance_schema(cls), from_str]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema()
            ),
        )


__all__ = ["MAX_TOKEN_LENGTH", "CanonicalToken", "TokenError"]
