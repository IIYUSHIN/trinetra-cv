"""EntityRef: a bounded reference to a domain entity (IMP-0029).

Source contract (EntityRefDto): `entity_type, entity_id, display_label, digest?, version_label?` -
"Use when a full domain object is unnecessary. No arbitrary object nesting."
The type is an open canonical token and the id is an opaque string of bounded length; the exact
type vocabulary and id spelling per type are not fixed by the available sources (ERR-CL-004,
PROVISIONAL), so this value object validates shape only and never interprets the id.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import AfterValidator, Strict

from trinetra.domain.base import DomainModel
from trinetra.domain.digests import DigestSha256
from trinetra.domain.text import Label
from trinetra.domain.tokens import CanonicalToken

MAX_ENTITY_ID: Final = 128
MAX_VERSION_LABEL: Final = 64
_ENTITY_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]*")
_VERSION_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*")


class EntityType(CanonicalToken):
    KIND = "entity_type"
    __slots__ = ()


def _entity_id(value: str) -> str:
    if not 1 <= len(value) <= MAX_ENTITY_ID or not _ENTITY_ID_RE.fullmatch(value):
        raise ValueError(f"entity_id must be 1..{MAX_ENTITY_ID} chars of [A-Za-z0-9._:-], alphanumeric start")
    return value


def _version(value: str) -> str:
    if not 1 <= len(value) <= MAX_VERSION_LABEL or not _VERSION_RE.fullmatch(value):
        raise ValueError(f"version_label must be 1..{MAX_VERSION_LABEL} chars of [A-Za-z0-9._+-]")
    return value


EntityIdText = Annotated[str, Strict(), AfterValidator(_entity_id)]
VersionLabel = Annotated[str, Strict(), AfterValidator(_version)]


class EntityRef(DomainModel):
    entity_type: EntityType
    entity_id: EntityIdText
    display_label: Label
    digest: DigestSha256 | None = None
    version_label: VersionLabel | None = None


__all__ = ["MAX_ENTITY_ID", "MAX_VERSION_LABEL", "EntityIdText", "EntityRef", "EntityType", "VersionLabel"]
