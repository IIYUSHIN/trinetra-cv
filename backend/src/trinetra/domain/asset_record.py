"""AssetRecord: the one authoritative record of an admitted asset's byte identity (IMP-0053).

An asset's identity is its content digest and length, proven by Core at admission. The supplied
filename is a display label only; it never takes part in identity, lookup or path construction. The
record holds no storage location (no path, no locator): where bytes live is an operational detail that
may change, and an AssetRecord must stay valid when it does. One blob may back many records.

PROPOSED fields and grammars (ERR-CL-013, not frozen): flat `blob_digest` / `size_bytes` so the
persisted JSON body can be bound column-for-column by the database; `ActorId` is a canonical token;
`MediaType` is a lowercase `type/subtype` claim, never trusted as detected content; `SuppliedName` is
bounded NFC text without path separators.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, Strict, field_validator

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.base import DomainModel
from trinetra.domain.digests import MAX_SIZE_BYTES, DigestSha256
from trinetra.domain.references import ReferenceId
from trinetra.domain.text import check_text
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken

SCHEMA_NAME: Final = "trinetra.asset_record"
SCHEMA_VERSION: Final = 1
MAX_SUPPLIED_NAME: Final = 255
MAX_PARENTS: Final = 64
MAX_SOURCE_REFS: Final = 64
_MEDIA_TYPE: Final = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}")


class ActorId(CanonicalToken):
    """Who caused a record to be written (`core` for the Core process itself)."""

    KIND = "actor_id"
    __slots__ = ()


def _media_type(value: str) -> str:
    if not _MEDIA_TYPE.fullmatch(value):
        msg = "media_type must be a lowercase type/subtype without parameters"
        raise ValueError(msg)
    return value


def _supplied_name(value: str) -> str:
    check_text(value, name="supplied_name", minimum=0, maximum=MAX_SUPPLIED_NAME)
    if "/" in value or "\\" in value:
        msg = "supplied_name must not contain a path separator"
        raise ValueError(msg)
    return value


MediaType = Annotated[str, Strict(), AfterValidator(_media_type)]
SuppliedName = Annotated[str, Strict(), AfterValidator(_supplied_name)]


class AssetRecord(DomainModel):
    schema_name: Literal["trinetra.asset_record"] = SCHEMA_NAME
    schema_version: Literal[1] = SCHEMA_VERSION
    record_id: AssetId
    asset_kind: AssetKind
    blob_digest: DigestSha256
    size_bytes: int
    media_type: MediaType
    supplied_name: SuppliedName
    created_at: UtcTimestamp
    created_by: ActorId
    parent_asset_refs: Annotated[tuple[AssetId, ...], Field(max_length=MAX_PARENTS)] = ()
    source_metadata_refs: Annotated[tuple[ReferenceId, ...], Field(max_length=MAX_SOURCE_REFS)] = ()

    @field_validator("size_bytes")
    @classmethod
    def _bounded(cls, value: int) -> int:
        if isinstance(value, bool) or not 0 <= value <= MAX_SIZE_BYTES:
            msg = f"size_bytes must be an int within [0, {MAX_SIZE_BYTES}]"
            raise ValueError(msg)
        return value

    @field_validator("parent_asset_refs", "source_metadata_refs")
    @classmethod
    def _unique(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        if len(set(value)) != len(value):
            msg = "references must be unique"
            raise ValueError(msg)
        return value

    def body_json(self) -> str:
        """The canonical (RFC 8785) JSON text that is persisted and digested."""
        return self.canonical_bytes().decode("utf-8")


__all__ = ["ActorId", "AssetRecord", "MediaType", "SuppliedName"]
