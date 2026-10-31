"""IMP-0418: 128-bit private canary token with no incidental serialization."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any


class LeakageCanary:
    __slots__ = ("__token",)
    __token: bytes

    def __init__(self, entropy: Callable[[int], bytes] = secrets.token_bytes) -> None:
        raw = entropy(16)
        if type(raw) is not bytes or len(raw) != 16:
            raise ValueError("canary entropy must contain exactly 128 bits")
        object.__setattr__(self, "_LeakageCanary__token", b"TF-CANARY-" + raw.hex().encode("ascii"))

    def evaluator_private_token(self) -> bytes:
        """Only for hidden metadata insertion and leakage scanning."""
        return self.__token

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("canary is immutable")

    def __repr__(self) -> str:
        return "<private leakage canary>"

    def __getstate__(self) -> object:
        raise TypeError("canary cannot be serialized")

    def __reduce__(self) -> tuple[Any, ...]:
        raise TypeError("canary cannot be serialized")
