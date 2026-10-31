"""Quarantine-to-admitted workflow service (IMP-0059).

One call turns untrusted bytes plus declared metadata into exactly one immutable CAS object and one
authoritative AssetRecord, in a fixed order that never trusts the caller:

1. the request is validated before a single byte is read (kind, media type, name, actor);
2. the bytes stream into quarantine; the admission profile is applied while streaming, so an
   oversize or mistyped upload is stopped early and nothing reaches the CAS;
3. the staged candidate is committed (idempotent for identical content) and its digest and size are
   taken from what was actually written, never from the caller;
4. the AssetRecord is written in the caller's unit of work. The caller owns commit/rollback.

Failure semantics follow the persistence design: a crash or rollback after step 3 leaves an orphan
CAS object, which is acceptable and visible to the quarantine sweep and CAS scan; nothing is repaired
here and no AssetRecord ever exists without its bytes. The asset identity is the record id, never the
supplied file name.

PROPOSED engineering decisions (ERR-CL-017): profile applied streaming-first plus a final full check;
two records may share one blob; entropy and clock are injected. A caller may pass a `record_id` it
reserved earlier (IMP-0077, ERR-CL-022 PROPOSED); the repository still refuses a reused id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from trinetra.domain.asset_admission import DEFAULT_PROFILE, SNIFF_BYTES, AdmissionProfile
from trinetra.domain.asset_record import ActorId, AssetRecord, MediaType, SuppliedName
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.timing import UtcTimestamp
from trinetra.timeutil import SystemClock, format_utc, truncate_to_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

    from trinetra.domain.digests import DigestSha256
    from trinetra.domain.references import ReferenceId
    from trinetra.infrastructure.asset_repository import AssetRepository
    from trinetra.infrastructure.cas_admission import ByteSource
    from trinetra.infrastructure.cas_store import CasStore
    from trinetra.infrastructure.unit_of_work import UnitOfWork
    from trinetra.timeutil import Clock

_MEDIA_TYPE: Final = TypeAdapter(MediaType)
_NAME: Final = TypeAdapter(SuppliedName)


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """What the caller declares. Everything here is validated; the bytes decide digest and size."""

    kind: AssetKind
    media_type: str
    supplied_name: str
    created_by: ActorId
    parent_asset_refs: tuple[AssetId, ...] = ()
    source_metadata_refs: tuple[ReferenceId, ...] = ()
    expected_digest: DigestSha256 | None = None
    expected_size: int | None = None
    record_id: AssetId | None = None  # an id reserved earlier (intake); None: generate one


@dataclass(frozen=True, slots=True)
class AdmittedAsset:
    record: AssetRecord
    blob_created: bool  # False when identical bytes were already in the CAS


class _ProfileGuard:
    """Wraps the byte source: enforces the size ceiling and checks the type on the first bytes."""

    __slots__ = ("_ceiling", "_checked", "_head", "_profile", "_request", "_source", "_total")

    def __init__(self, source: ByteSource, profile: AdmissionProfile, request: AdmissionRequest) -> None:
        self._source = source
        self._profile = profile
        self._request = request
        self._ceiling = profile.rule_for(request.kind).max_bytes
        self._head = b""
        self._total = 0
        self._checked = False

    @property
    def head(self) -> bytes:
        return self._head

    def read(self, size: int) -> object:
        chunk = self._source.read(size)
        if not isinstance(chunk, bytes):
            return chunk  # the streamer rejects non-bytes with its own typed error
        self._total += len(chunk)
        if self._total > self._ceiling:
            # a size-only probe: raises the profile's own typed error for this kind
            self._profile.check(self._request.kind, self._request.media_type, self._total, self._head)
        if len(self._head) < SNIFF_BYTES:
            self._head += chunk[: SNIFF_BYTES - len(self._head)]
        if not self._checked and (len(self._head) >= SNIFF_BYTES or not chunk):
            self._checked = True
            self._profile.check(self._request.kind, self._request.media_type, 0, self._head)
        return chunk


class AssetAdmissionService:
    def __init__(
        self,
        cas: CasStore,
        repository: AssetRepository,
        *,
        profile: AdmissionProfile = DEFAULT_PROFILE,
        clock: Clock | None = None,
        entropy: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._cas = cas
        self._repository = repository
        self._profile = profile
        self._clock: Clock = clock or SystemClock()
        self._entropy = entropy

    def admit(self, uow: UnitOfWork, source: ByteSource, request: AdmissionRequest) -> AdmittedAsset:
        media_type = _MEDIA_TYPE.validate_python(request.media_type)
        name = _NAME.validate_python(request.supplied_name)
        guard = _ProfileGuard(source, self._profile, request)
        result = self._cas.admit(
            guard,
            expected_digest=request.expected_digest,
            expected_size=request.expected_size,
        )
        self._profile.check(request.kind, media_type, result.size, guard.head)
        record = AssetRecord(
            record_id=request.record_id or AssetId.from_entropy(self._entropy),
            asset_kind=request.kind,
            blob_digest=result.digest,
            size_bytes=result.size,
            media_type=media_type,
            supplied_name=name,
            created_at=UtcTimestamp.parse(format_utc(truncate_to_seconds(self._clock.now()))),
            created_by=request.created_by,
            parent_asset_refs=request.parent_asset_refs,
            source_metadata_refs=request.source_metadata_refs,
        )
        self._repository.add(uow, record)
        return AdmittedAsset(record, blob_created=result.created)


__all__ = ["AdmissionRequest", "AdmittedAsset", "AssetAdmissionService"]
