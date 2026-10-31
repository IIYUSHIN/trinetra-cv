"""Bounded display/summary text for domain value objects.

PROPOSED rule (the contracts say "bounded" and "no arbitrary nesting" but give no text grammar for
labels): strict `str`, NFC-normalised, no control characters (Unicode category Cc), no surrogate or
unassigned code points, no leading/trailing whitespace, length counted in code points.
Deterministic by construction: the same logical text has exactly one valid spelling (NFC).
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator, Strict

MAX_LABEL: Final = 200
MAX_SUMMARY: Final = 1024


def check_text(value: str, *, name: str, minimum: int, maximum: int) -> str:
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{name}: length must be within [{minimum}, {maximum}] code points")
    if value != value.strip():
        raise ValueError(f"{name}: must not start or end with whitespace")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name}: must be NFC-normalised")
    for ch in value:
        category = unicodedata.category(ch)
        if category in ("Cc", "Cs", "Cn"):
            raise ValueError(f"{name}: control, surrogate or unassigned code point U+{ord(ch):04X}")
    return value


def text_validator(name: str, minimum: int, maximum: int) -> AfterValidator:
    return AfterValidator(lambda v: check_text(v, name=name, minimum=minimum, maximum=maximum))


Label = Annotated[str, Strict(), text_validator("label", 1, MAX_LABEL)]
Summary = Annotated[str, Strict(), text_validator("summary", 1, MAX_SUMMARY)]

__all__ = ["MAX_LABEL", "MAX_SUMMARY", "Label", "Summary", "check_text", "text_validator"]
