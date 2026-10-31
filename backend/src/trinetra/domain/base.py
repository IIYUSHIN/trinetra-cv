"""Immutable base model and conventions for domain value objects (IMP-0016).

Bible I2 acceptance: "All primitives are immutable, validate exact formats,
serialize deterministically where specified, and cannot be confused across
authority/state dimensions"; forbidden: "No generic `status`; no magic
risk/confidence score; no browser-specific types; no persistence imports".

PROPOSED engineering conventions (the Bible gives the law, not the mechanism):

* every domain model is frozen, hashable, strict (no type coercion), rejects
  unknown fields and non-finite floats, and cannot be loosened by subclasses;
* mutable containers (`list`, `dict`, `set`, `bytearray`) are rejected in field
  annotations at class-definition time - use `tuple` / `frozenset`;
* field names that would create a generic status or a magic score are rejected;
* `model_copy` / `model_construct` (which bypass validation) are disabled; use
  `evolve`, which re-validates;
* deterministic wire form is `to_wire()` -> JSON-safe dict and `canonical_bytes()`
  via RFC 8785 JCS (`trinetra.jcs`); no new canonicalisation is introduced.
"""

from __future__ import annotations

import re
import typing
from typing import Any, ClassVar, Final, Self

from pydantic import BaseModel, ConfigDict

from trinetra.errors import TrinetraError
from trinetra.jcs import canonical_digest, canonicalize

FORBIDDEN_FIELD_NAMES: Final = frozenset({"status", "state"})
_SCORE_NAME: Final = re.compile(
    r"(?:^|_)(?:risk|trust|confidence|assurance|integrity|safety|quality)?_?score(?:$|_)|^confidence$|^risk$"
)
_MUTABLE_ORIGINS: Final = (list, dict, set, bytearray)
_REQUIRED_CONFIG: Final = {"frozen": True, "extra": "forbid", "strict": True, "allow_inf_nan": False}


class DomainConventionError(TrinetraError):
    code = "DOMAIN_CONVENTION"
    default_message = "domain convention violated"


def _mutable_in(annotation: object) -> str | None:
    origin = typing.get_origin(annotation) or annotation
    if origin in _MUTABLE_ORIGINS:
        return getattr(origin, "__name__", str(origin))
    if origin is typing.Annotated:
        return _mutable_in(typing.get_args(annotation)[0])
    for arg in typing.get_args(annotation):
        found = _mutable_in(arg)
        if found:
            return found
    return None


class DomainModel(BaseModel):
    """Base for every immutable domain value object."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True, allow_inf_nan=False,
        validate_default=True, revalidate_instances="always",
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        for key, required in _REQUIRED_CONFIG.items():
            if cls.model_config.get(key) != required:
                raise DomainConventionError(
                    f"{cls.__name__}: model_config[{key!r}] must stay {required!r}",
                    details={"class": cls.__name__, "key": key},
                )
        for name, info in cls.model_fields.items():
            if name in FORBIDDEN_FIELD_NAMES or _SCORE_NAME.search(name):
                raise DomainConventionError(
                    f"{cls.__name__}.{name}: generic status/state or magic score fields are forbidden",
                    details={"class": cls.__name__, "field": name},
                )
            mutable = _mutable_in(info.annotation)
            if mutable:
                raise DomainConventionError(
                    f"{cls.__name__}.{name}: mutable container {mutable!r}; use tuple/frozenset",
                    details={"class": cls.__name__, "field": name},
                )

    # -- validation-bypassing pydantic entry points are closed ---------------
    def model_copy(self, *, update: Any = None, deep: bool = False) -> Self:  # noqa: ARG002
        raise DomainConventionError("model_copy bypasses validation; use evolve()")

    @classmethod
    def model_construct(cls, *args: Any, **kwargs: Any) -> Self:  # noqa: ARG003
        raise DomainConventionError("model_construct bypasses validation")

    def evolve(self, **changes: Any) -> Self:
        """Return a new, fully re-validated instance with `changes` applied."""
        unknown = set(changes) - set(type(self).model_fields)
        if unknown:
            raise DomainConventionError(f"unknown field(s): {sorted(unknown)}")
        data = {name: getattr(self, name) for name in type(self).model_fields} | changes
        return type(self)(**data)

    # -- deterministic serialisation ----------------------------------------
    def to_wire(self) -> dict[str, Any]:
        """JSON-safe dict (all fields present, no exclusion of None)."""
        return self.model_dump(mode="json", round_trip=True)

    def canonical_bytes(self) -> bytes:
        """RFC 8785 canonical JSON of `to_wire()`."""
        return canonicalize(self.to_wire())

    def canonical_digest(self) -> str:
        """`sha256:<hex>` of `canonical_bytes()`."""
        return canonical_digest(self.to_wire())


__all__ = ["FORBIDDEN_FIELD_NAMES", "DomainConventionError", "DomainModel"]
