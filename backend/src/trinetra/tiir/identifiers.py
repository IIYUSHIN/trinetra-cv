"""IMP-0285: provisional ERR-CL-019 binary identities, distinct from OpaqueId.

Generation retains 122 random bits after UUIDv4 masking. Acceptance is length-only;
version/variant diagnostics never authorize or reject an existing identity.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from trinetra.tiir.deterministic_cbor import decode_cbor, encode_cbor
from trinetra.tiir.errors import TiirSchemaError

_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _require_bytes(raw: object) -> bytes:
    if type(raw) is not bytes or len(raw) != 16:
        raise TiirSchemaError("TIIR identity requires exactly 16 bytes")
    return raw


def _generate_uuid_v4_bytes(entropy: Callable[[int], bytes] | None) -> bytes:
    """ERR-CL-019: isolate the provisional generation choice from all parsing."""
    source = os.urandom if entropy is None else entropy
    raw = bytearray(_require_bytes(source(16)))
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return bytes(raw)


class _BinaryIdentity:
    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, raw: bytes) -> None:
        if hasattr(self, "_raw"):
            raise AttributeError("TIIR identity is immutable")
        object.__setattr__(self, "_raw", _require_bytes(raw))

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def is_uuid_v4(self) -> bool:
        return self._raw[6] >> 4 == 4 and self._raw[8] >> 6 == 2

    @classmethod
    def generate(cls, entropy: Callable[[int], bytes] | None = None) -> Self:
        return cls(_generate_uuid_v4_bytes(entropy))

    @classmethod
    def parse(cls, text: object) -> Self:
        if type(text) is not str or not _TEXT.fullmatch(text):
            raise TiirSchemaError("TIIR identity text must be canonical lowercase hyphenated hex")
        return cls(bytes.fromhex(text.replace("-", "")))

    def to_text(self) -> str:
        text = self._raw.hex()
        return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"

    def to_cbor_value(self) -> bytes:
        return self._raw

    def to_cbor(self) -> bytes:
        return encode_cbor(self._raw)

    @classmethod
    def from_cbor(cls, data: bytes) -> Self:
        return cls(_require_bytes(decode_cbor(data, max_bytes=17)))

    def __str__(self) -> str:
        return self.to_text()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.to_text()!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _BinaryIdentity) and type(other) is type(self) and self._raw == other._raw

    def __hash__(self) -> int:
        return hash((type(self), self._raw))

    def _other_bytes(self, other: object) -> bytes:
        if not isinstance(other, _BinaryIdentity) or type(other) is not type(self):
            raise TypeError("TIIR identities can be ordered only against the same identity type")
        return other._raw

    def __lt__(self, other: object) -> bool:
        return self._raw < self._other_bytes(other)

    def __le__(self, other: object) -> bool:
        return self._raw <= self._other_bytes(other)

    def __gt__(self, other: object) -> bool:
        return self._raw > self._other_bytes(other)

    def __ge__(self, other: object) -> bool:
        return self._raw >= self._other_bytes(other)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("TIIR identity is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("TIIR identity is immutable")

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    def __reduce__(self) -> tuple[Any, ...]:
        return type(self), (self._raw,)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        def validate(value: object) -> _BinaryIdentity:
            if isinstance(value, cls) and type(value) is cls:
                return value
            try:
                return cls.parse(value)
            except TiirSchemaError as exc:
                raise ValueError(exc.message) from exc

        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_after_validator_function(
                validate, core_schema.str_schema(strict=True),
            ),
            python_schema=core_schema.no_info_plain_validator_function(validate),
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(),
            ),
        )


class ReceiptId(_BinaryIdentity):
    __slots__ = ()


class StreamId(_BinaryIdentity):
    __slots__ = ()
