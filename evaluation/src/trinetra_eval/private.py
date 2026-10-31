"""Opaque immutable evaluation records; explicit private reads only (IMP-0400/0420)."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from trinetra.jcs import canonical_digest, canonicalize, loads_strict


class PrivateSchema(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True
    )


class PrivateRecord:
    """No implicit serialization. Private bytes must be requested deliberately."""

    __slots__ = ("__payload",)
    SCHEMA: ClassVar[type[BaseModel]]
    DIGEST_FIELD: ClassVar[str | None] = None
    __payload: bytes

    def __init__(self, **values: Any) -> None:
        payload: bytes | None = None
        try:
            parsed = self.SCHEMA(**values).model_dump(mode="json")
            if self.DIGEST_FIELD:
                declared = parsed.pop(self.DIGEST_FIELD)
                if declared != canonical_digest(parsed):
                    payload = b""
                parsed[self.DIGEST_FIELD] = declared
            if payload != b"":
                payload = canonicalize(parsed)
        except Exception:  # noqa: BLE001 - private validation never propagates input-bearing errors
            payload = None
        if not payload:
            raise ValueError("invalid private evaluation record")
        object.__setattr__(self, "_PrivateRecord__payload", payload)

    def evaluator_private_bytes(self) -> bytes:
        """Private authority operation; never call from visible export code."""
        return self.__payload

    def __repr__(self) -> str:
        return "<private evaluation record>"

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("private evaluation record is immutable")

    def __getstate__(self) -> object:
        raise TypeError("private evaluation record cannot be serialized")

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("private evaluation record cannot be serialized")


def private_document(record: PrivateRecord) -> dict[str, Any]:
    """Explicit private read for evaluator composition/sealing, never a visible export."""
    value = loads_strict(record.evaluator_private_bytes())
    if not isinstance(value, dict):
        raise TypeError("private document must be an object")
    return value
