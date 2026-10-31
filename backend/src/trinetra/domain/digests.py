"""DigestSha256 and ContentIdentity (IMP-0027).

`DigestSha256` is the domain value for the I1 digest wire form `sha256:<64 lowercase hex>`
(trinetra.digest owns hashing and the wire grammar; nothing is re-implemented here).
`ContentIdentity` pairs a digest with the exact byte length - the identity of *content*,
distinct from the digest of a record that merely describes it (a recorded failure mode:
"package record digest vs actual model-content identity"). `size_bytes` is PROPOSED as part
of the identity: equal digests with different sizes are rejected upstream as corruption.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import GetCoreSchemaHandler, field_validator
from pydantic_core import core_schema

from trinetra.digest import DigestError, format_digest, parse_digest, sha256_bytes
from trinetra.domain.base import DomainModel
from trinetra.errors import TrinetraError

MAX_SIZE_BYTES = 2**53 - 1


class DigestValueError(TrinetraError):
    code = "DIGEST_VALUE_INVALID"
    default_message = "invalid digest value"


class DigestSha256:
    """Immutable 32-byte SHA-256 value. Wire: ``sha256:<64 lowercase hex>``."""

    __slots__ = ("_raw",)
    _raw: bytes

    def __init__(self, raw: bytes) -> None:
        if type(raw) is not bytes or len(raw) != 32:
            raise DigestValueError("DigestSha256 needs exactly 32 raw bytes")
        object.__setattr__(self, "_raw", raw)

    @classmethod
    def parse(cls, text: object) -> Self:
        try:
            return cls(parse_digest(text))
        except DigestError as exc:
            raise DigestValueError(exc.message) from exc

    @classmethod
    def of(cls, data: bytes) -> Self:
        """Digest of `data` (delegates to trinetra.digest)."""
        return cls(sha256_bytes(data))

    @property
    def raw(self) -> bytes:
        return self._raw

    @property
    def hex(self) -> str:
        return self._raw.hex()

    def to_wire(self) -> str:
        return format_digest(self._raw)

    def __str__(self) -> str:
        return self.to_wire()

    def __repr__(self) -> str:
        return f"DigestSha256({self.to_wire()!r})"

    def __eq__(self, other: object) -> bool:
        return type(other) is DigestSha256 and other._raw == self._raw

    def __hash__(self) -> int:
        return hash(("DigestSha256", self._raw))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("DigestSha256 is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("DigestSha256 is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (DigestSha256, (self._raw,))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(text: str) -> DigestSha256:
            try:
                return cls.parse(text)
            except DigestValueError as exc:
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


class ContentIdentity(DomainModel):
    """Digest and exact length of a byte sequence."""

    digest: DigestSha256
    size_bytes: int

    @field_validator("size_bytes")
    @classmethod
    def _bounded(cls, value: int) -> int:
        if isinstance(value, bool) or not 0 <= value <= MAX_SIZE_BYTES:
            raise ValueError(f"size_bytes must be an int within [0, {MAX_SIZE_BYTES}]")
        return value

    @classmethod
    def of(cls, data: bytes) -> ContentIdentity:
        return cls(digest=DigestSha256.of(data), size_bytes=len(data))

    def matches(self, data: bytes) -> bool:
        """True iff `data` has exactly this length and digest."""
        return len(data) == self.size_bytes and DigestSha256.of(data) == self.digest


__all__ = ["MAX_SIZE_BYTES", "ContentIdentity", "DigestSha256", "DigestValueError"]
