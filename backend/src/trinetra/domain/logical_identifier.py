"""LogicalIdentifier: human-meaningful, case-sensitive, NFC text name used by ontologies and cases.

The sources name `LogicalIdentifier` as a field type (class names, case labels) but never define its
grammar (ERR-CL-020, PROPOSED): 1..128 code points, NFC-normalised, only single interior ASCII spaces,
no control, format, separator or unassigned code points. Equality is exact; nothing is case-folded.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator, Strict

MAX_NAME_LENGTH: Final = 128


def _logical_identifier(value: str) -> str:
    if not 1 <= len(value) <= MAX_NAME_LENGTH:
        msg = f"name: length must be within [1, {MAX_NAME_LENGTH}] code points"
        raise ValueError(msg)
    if unicodedata.normalize("NFC", value) != value:
        msg = "name: must be NFC-normalised"
        raise ValueError(msg)
    if value != value.strip(" ") or "  " in value:
        msg = "name: spaces only singly between words"
        raise ValueError(msg)
    for ch in value:
        if ch != " " and (ch.isspace() or unicodedata.category(ch)[0] in "CZ"):
            msg = f"name: control, format, separator or unassigned code point U+{ord(ch):04X}"
            raise ValueError(msg)
    return value


LogicalIdentifier = Annotated[str, Strict(), AfterValidator(_logical_identifier)]

__all__ = ["MAX_NAME_LENGTH", "LogicalIdentifier"]
