"""Storage ports: incoming-byte quarantine, asset admission and asset existence (EIB-04).

The application never receives a filesystem path or a CAS location. `QuarantinePort` holds bytes that
arrived but are not yet assets; `AssetAdmissionPort` turns one quarantine entry into exactly one
authoritative AssetRecord (Quarantine -> CAS -> DB) in its own transaction and returns that record, so
asset admission facts stay valid even when a later Case commit fails. `AssetLookupPort` answers only
whether an asset record exists.
"""

from __future__ import annotations

from typing import Protocol

from trinetra.domain.asset_record import AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.intake_ids import QuarantineHandle


class ByteStream(Protocol):
    def read(self, size: int, /) -> bytes: ...


class QuarantinePort(Protocol):
    def create_entry(self, handle: QuarantineHandle) -> None:
        """Create an empty entry; raises if the handle already exists."""

    def write_stream(self, handle: QuarantineHandle, source: ByteStream, *, max_bytes: int) -> int:
        """Copy `source` into the entry and return the bytes written.

        Raises `TransferTooLargeError` as soon as more than `max_bytes` would be written.
        """

    def remove(self, handle: QuarantineHandle) -> None:
        """Remove the entry; removing a missing entry is not an error."""


class AssetAdmissionPort(Protocol):
    def admit(
        self,
        *,
        handle: QuarantineHandle,
        asset_id: AssetId,
        kind: AssetKind,
        media_type: str,
        supplied_name: str,
    ) -> AssetRecord:
        """Admit the quarantined bytes as the asset `asset_id`, or raise a typed rejection."""


class AssetLookupPort(Protocol):
    def exists(self, asset_id: AssetId) -> bool: ...


__all__ = ["AssetAdmissionPort", "AssetLookupPort", "ByteStream", "QuarantinePort"]
