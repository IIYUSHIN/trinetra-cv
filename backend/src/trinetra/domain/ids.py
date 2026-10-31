"""Opaque identifier base type (IMP-0017) and the typed identifiers built on it.

Sources: "128-bit opaque identity" (receipt identity, S000031:579), "Use random
opaque identity" (S000033:228), "canonical identity remains the opaque attempt
ID" (S000025:607); Bible I2 acceptance "cannot be confused across authority/state
dimensions" (line 251).

PROPOSED wire form (the Bible fixes opacity and 128 bits, not the spelling):
``<prefix>_<32 lowercase hex>`` - e.g. ``case_0123...``. Each identifier class owns
exactly one prefix (registered, unique), so an AssetId can never parse as a CaseId,
never compares equal to one, and never hashes alike across types. Identifiers carry
no meaning: callers must not parse, order or derive anything from the hex part.
Randomness is injected (`from_entropy`) so the domain stays pure; `generate` is the
single convenience that draws from the OS CSPRNG.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from typing import Any, ClassVar, Final, Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from trinetra.errors import TrinetraError

ID_BYTES: Final = 16
_HEX_LEN: Final = ID_BYTES * 2
_PREFIX_RE: Final = re.compile(r"[a-z][a-z0-9]{1,7}")
_HEX_RE: Final = re.compile(r"[0-9a-f]{32}")
_REGISTRY: dict[str, type[OpaqueId]] = {}


class IdentifierError(TrinetraError):
    code = "IDENTIFIER_INVALID"
    default_message = "invalid identifier"


def registered_id_types() -> dict[str, type[OpaqueId]]:
    """prefix -> identifier class (a copy)."""
    return dict(_REGISTRY)


class OpaqueId:
    """Immutable 128-bit opaque identifier; subclass and set ``PREFIX``."""

    PREFIX: ClassVar[str]
    __slots__ = ("_hex",)
    _hex: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        prefix = cls.__dict__.get("PREFIX")
        if not isinstance(prefix, str) or not _PREFIX_RE.fullmatch(prefix):
            raise IdentifierError(f"{cls.__name__}: PREFIX must match [a-z][a-z0-9]{{1,7}}",
                                  details={"class": cls.__name__})
        if prefix in _REGISTRY:
            raise IdentifierError(f"{cls.__name__}: PREFIX {prefix!r} already used by "
                                  f"{_REGISTRY[prefix].__name__}", details={"prefix": prefix})
        if "__slots__" not in cls.__dict__:
            raise IdentifierError(f"{cls.__name__}: subclasses must declare __slots__ = ()")
        _REGISTRY[prefix] = cls

    def __new__(cls, *_: object, **__: object) -> Self:
        raise IdentifierError(f"use {cls.__name__}.parse / from_entropy / generate, not the constructor")

    @classmethod
    def _make(cls, hex32: str) -> Self:
        obj = object.__new__(cls)
        object.__setattr__(obj, "_hex", hex32)
        return obj

    # -- construction ------------------------------------------------------
    @classmethod
    def parse(cls, text: object) -> Self:
        """Strict parse of ``<prefix>_<32 lowercase hex>``; raises IdentifierError."""
        if cls is OpaqueId:
            raise IdentifierError("OpaqueId is abstract")
        if not isinstance(text, str):
            raise IdentifierError(f"{cls.__name__}: expected str, got {type(text).__name__}")
        prefix, sep, rest = text.partition("_")
        if sep != "_" or prefix != cls.PREFIX:
            raise IdentifierError(f"{cls.__name__}: wrong or missing prefix (expected {cls.PREFIX!r}_)",
                                  details={"expected_prefix": cls.PREFIX})
        if not _HEX_RE.fullmatch(rest):
            raise IdentifierError(f"{cls.__name__}: body must be 32 lowercase hex characters")
        return cls._make(rest)

    @classmethod
    def from_entropy(cls, entropy: Callable[[int], bytes]) -> Self:
        """Build from an injected entropy source (``entropy(16) -> 16 bytes``)."""
        if cls is OpaqueId:
            raise IdentifierError("OpaqueId is abstract")
        raw = entropy(ID_BYTES)
        if not isinstance(raw, bytes | bytearray) or len(raw) != ID_BYTES:
            raise IdentifierError("entropy source must return exactly 16 bytes")
        if not any(raw):
            raise IdentifierError("entropy source returned all-zero bytes")
        return cls._make(bytes(raw).hex())

    @classmethod
    def generate(cls) -> Self:
        """Draw 128 random bits from the operating-system CSPRNG."""
        return cls.from_entropy(secrets.token_bytes)

    # -- value semantics ---------------------------------------------------
    def __str__(self) -> str:
        return f"{self.PREFIX}_{self._hex}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other._hex == self._hex

    def __hash__(self) -> int:
        return hash((type(self).__qualname__, self._hex))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self).PREFIX, self._hex))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: object) -> Self:
        return self

    def to_wire(self) -> str:
        return str(self)

    # -- pydantic integration (strict: instance of this exact type, or its wire string) ---
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(text: str) -> OpaqueId:
            try:
                return cls.parse(text)
            except IdentifierError as exc:  # pydantic reports ValueError as a ValidationError
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


def _rebuild(prefix: str, hex32: str) -> OpaqueId:
    cls = _REGISTRY.get(prefix)
    if cls is None or not _HEX_RE.fullmatch(hex32):
        raise IdentifierError("cannot rebuild identifier")
    return cls._make(hex32)


__all__ = ["ID_BYTES", "IdentifierError", "OpaqueId", "registered_id_types"]
