"""Root exception hierarchy and stable error-code base (IMP-0005).

Scope is deliberately minimal (Bible 846-867 forbids domain invention): this
module provides `TrinetraError` and the machinery that makes error codes
stable. Concrete error families belong to the packets that need them and must
subclass `TrinetraError` with their own unique code.

Contract
  * A code is an UPPER_SNAKE_CASE token (<= 64 chars), unique across the
    process, declared on the class itself (never inherited), and never reused or
    renamed once released (see tests/foundation/golden/error_codes.json).
  * `retryable` is a class-level bool, not an instance choice.
  * `details` is a bounded, immutable, JSON-safe mapping (finite numbers,
    I-JSON integer range) so it can later be canonicalised without loss.
  * Messages are human text only; they are never a place for secrets (redaction
    helpers arrive with IMP-0013).
Standard library only.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar, Final

CODE_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
MAX_CODE_LENGTH: Final = 64
MAX_MESSAGE_LENGTH: Final = 1000
MAX_DETAILS_DEPTH: Final = 8
MAX_DETAILS_NODES: Final = 1000
MAX_SAFE_INTEGER: Final = 2**53 - 1  # I-JSON / RFC 8785 interoperable integer range

_REGISTRY: dict[str, type[TrinetraError]] = {}


def validate_error_code(code: object) -> str:
    """Return `code` if it is a well-formed stable error code, else raise ValueError."""
    if not isinstance(code, str):
        raise ValueError(f"error code must be str, got {type(code).__name__}")  # noqa: TRY004  (one ValueError contract for all bad input)
    if len(code) > MAX_CODE_LENGTH:
        raise ValueError(f"error code longer than {MAX_CODE_LENGTH} characters")
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError(f"error code {code!r} is not an UPPER_SNAKE_CASE token")
    return code


def registered_error_codes() -> Mapping[str, type[TrinetraError]]:
    """Read-only snapshot of code -> exception class."""
    return MappingProxyType(dict(_REGISTRY))


def qualified_name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _freeze(value: Any, depth: int, budget: list[int]) -> Any:
    budget[0] += 1
    if budget[0] > MAX_DETAILS_NODES:
        raise ValueError(f"details exceed {MAX_DETAILS_NODES} nodes")
    if depth > MAX_DETAILS_DEPTH:
        raise ValueError(f"details nested deeper than {MAX_DETAILS_DEPTH}")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("details integer outside the I-JSON safe range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("details contain a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("details keys must be str")  # noqa: TRY004  (one ValueError contract for all bad input)
            frozen[key] = _freeze(item, depth + 1, budget)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, depth + 1, budget) for item in value)
    raise ValueError(f"unsupported details value type {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _rebuild(cls: type[TrinetraError], message: str, details: dict[str, Any]) -> TrinetraError:
    return cls(message, details=details or None)


class TrinetraError(Exception):
    """Root of every exception TRINETRA raises on purpose."""

    code: ClassVar[str] = "TRINETRA_ERROR"
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "TRINETRA error"
    _abstract: ClassVar[bool] = False

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._abstract = abstract
        if abstract:
            return
        if "code" not in cls.__dict__:
            raise TypeError(
                f"{cls.__qualname__} must declare its own `code` (or use abstract=True)"
            )
        try:
            validate_error_code(cls.__dict__["code"])
        except ValueError as exc:
            raise TypeError(f"{cls.__qualname__}: {exc}") from exc
        if not isinstance(cls.retryable, bool):
            raise TypeError(f"{cls.__qualname__}.retryable must be a bool")
        existing = _REGISTRY.get(cls.code)
        if existing is not None and existing is not cls:
            raise TypeError(
                f"error code {cls.code} already registered by {qualified_name(existing)}"
            )
        _REGISTRY[cls.code] = cls

    def __init__(self, message: str | None = None, *, details: Mapping[str, Any] | None = None):
        if type(self)._abstract:
            raise TypeError(f"{type(self).__qualname__} is abstract and cannot be raised")
        text = self.default_message if message is None else message
        if not isinstance(text, str):
            raise ValueError(f"message must be str, got {type(text).__name__}")  # noqa: TRY004  (one ValueError contract for all bad input)
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"message longer than {MAX_MESSAGE_LENGTH} characters")
        if details is not None and not isinstance(details, Mapping):
            raise ValueError("details must be a mapping")
        frozen = _freeze(details or {}, 0, [0])
        super().__init__(text)
        self._message = text
        self._details: Mapping[str, Any] = frozen

    @property
    def message(self) -> str:
        return self._message

    @property
    def details(self) -> Mapping[str, Any]:
        return self._details

    def to_dict(self) -> dict[str, Any]:
        """Envelope fields without transport data (`request_id` is added by the API)."""
        out: dict[str, Any] = {
            "error_code": self.code,
            "message": self._message,
            "retryable": self.retryable,
        }
        if self._details:
            out["details"] = _thaw(self._details)
        return out

    def __str__(self) -> str:
        return f"{self.code}: {self._message}"

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild, (type(self), self._message, _thaw(self._details)))


_REGISTRY[TrinetraError.code] = TrinetraError
