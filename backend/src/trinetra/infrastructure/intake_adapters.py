"""Infrastructure adapters for the intake use cases (IMP-0077).

* `IntakeQuarantine` holds incoming upload bytes in a private directory under the state root. It is the
  application's `QuarantinePort`; it never exposes a path to the application and every entry is a
  private regular file named after its opaque handle.
* `AuditedAssetAdmission` is the application's `AssetAdmissionPort`: it feeds one quarantined entry to the
  I3 admission service with the id reserved at upload time, and writes the AssetRecord and its audit
  event in ONE unit of work (success), or an audit failure event after rollback. Bytes the admission
  profile or the CAS reject become a typed `TransferRejectedError`; infrastructure failures propagate.
* `StoreAssetLookup` answers `AssetLookupPort` from the authoritative repository.
* `SystemClock`/`SystemIdFactory` supply whole-second UTC timestamps and OS entropy.

The directory is deliberately separate from the CAS staging area so CAS sweeps never touch intake bytes.
PROPOSED (ERR-CL-022): entries are removed after admission; leftovers from a crash are swept by age,
because the in-memory intake registry cannot outlive the process.
"""

from __future__ import annotations

import contextlib
import os
import stat
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Final

from trinetra.application.errors import TransferRejectedError, TransferTooLargeError
from trinetra.domain.asset_admission import AdmissionProfileError
from trinetra.domain.asset_record import ActorId, AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.audit import AuditEventType, AuditOutcome, ObjectRef, ObjectType
from trinetra.domain.case import CaseId
from trinetra.domain.digests import DigestSha256
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.asset_admission_service import AdmissionRequest
from trinetra.infrastructure.audit_coupling import run_audited
from trinetra.infrastructure.audit_repository import AuditDraft
from trinetra.infrastructure.cas_admission import AdmissionError
from trinetra.timeutil import SystemClock as _RealClock
from trinetra.timeutil import format_utc, truncate_to_seconds

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from trinetra.application.ports.storage import ByteStream
    from trinetra.infrastructure.asset_admission_service import AssetAdmissionService
    from trinetra.infrastructure.asset_repository import AssetRepository
    from trinetra.infrastructure.audit_repository import AuditRepository
    from trinetra.infrastructure.unit_of_work import CoreStore, UnitOfWork

DIRNAME: Final = "intake_quarantine"
SUFFIX: Final = ".part"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600
CHUNK: Final = 1024 * 1024
ASSET_RECORD: Final = ObjectType("asset_record")
CORE_ACTOR: Final = ActorId("core")
EVENT_INGESTED: Final = AuditEventType("asset_ingested")
EVENT_REJECTED: Final = AuditEventType("asset_rejected")


class IntakeQuarantine:
    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._check_directory()

    @classmethod
    def for_state_root(cls, state_root: Path) -> IntakeQuarantine:
        directory = state_root / DIRNAME
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=DIR_MODE)
        return cls(directory)

    # -- QuarantinePort ---------------------------------------------------------------------------
    def create_entry(self, handle: QuarantineHandle) -> None:
        self._check_directory()
        fd = os.open(self._path(handle), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     FILE_MODE)  # fmt: skip
        os.close(fd)

    def write_stream(self, handle: QuarantineHandle, source: ByteStream, *, max_bytes: int) -> int:
        fd = os.open(self._path(handle), os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self._require_private_file(fd)
            total = 0
            while True:
                chunk = source.read(CHUNK)
                if not isinstance(chunk, bytes) or len(chunk) > CHUNK:
                    raise TransferRejectedError("the upload stream returned invalid data")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise TransferTooLargeError(details={"max_bytes": max_bytes})
                view = memoryview(chunk)
                while view:
                    view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        return total

    def remove(self, handle: QuarantineHandle) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(handle).unlink()

    # -- admission support (not part of the application port) -----------------------------------
    def open_for_read(self, handle: QuarantineHandle) -> IO[bytes]:
        fd = os.open(self._path(handle), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            self._require_private_file(fd)
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    def entries(self) -> list[str]:
        return sorted(name for name in os.listdir(self._dir) if name.endswith(SUFFIX))

    def sweep(self, *, older_than_seconds: float, now: float | None = None) -> list[str]:
        """Remove entries last modified more than `older_than_seconds` ago; return their names."""
        if older_than_seconds < 0:
            msg = "older_than_seconds must not be negative"
            raise ValueError(msg)
        cutoff = (time.time() if now is None else now) - older_than_seconds
        removed = []
        for name in self.entries():
            path = self._dir / name
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                    path.unlink()
                    removed.append(name)
            except FileNotFoundError:
                continue
        return removed

    # -- internals ------------------------------------------------------------------------------
    def _path(self, handle: QuarantineHandle) -> Path:
        return self._dir / f"{handle}{SUFFIX}"

    def _check_directory(self) -> None:
        info = self._dir.lstat()
        if not stat.S_ISDIR(info.st_mode):
            msg = "the intake quarantine is not a real directory"
            raise TransferRejectedError(msg)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            msg = "the intake quarantine is not private to the running user"
            raise TransferRejectedError(msg)

    @staticmethod
    def _require_private_file(fd: int) -> None:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            msg = "the quarantine entry is not a private regular file"
            raise TransferRejectedError(msg)


class AuditedAssetAdmission:
    def __init__(
        self,
        store: CoreStore,
        audit: AuditRepository,
        service: AssetAdmissionService,
        quarantine: IntakeQuarantine,
        *,
        actor: ActorId = CORE_ACTOR,
    ) -> None:
        self._store = store
        self._audit = audit
        self._service = service
        self._quarantine = quarantine
        self._actor = actor

    def admit(
        self,
        *,
        handle: QuarantineHandle,
        asset_id: AssetId,
        kind: AssetKind,
        media_type: str,
        supplied_name: str,
    ) -> AssetRecord:
        def work(uow: UnitOfWork) -> tuple[AssetRecord, AuditDraft]:
            request = AdmissionRequest(
                kind=kind, media_type=media_type, supplied_name=supplied_name,
                created_by=self._actor, record_id=asset_id,
            )  # fmt: skip
            with self._quarantine.open_for_read(handle) as source:
                admitted = self._service.admit(uow, source, request)
            record = admitted.record
            subject = ObjectRef(
                object_type=ASSET_RECORD,
                object_id=str(record.record_id),
                record_digest=DigestSha256.parse(record.canonical_digest()),
            )
            draft = AuditDraft(event_type=EVENT_INGESTED, outcome=AuditOutcome.SUCCESS,
                               created_by=self._actor, subject_refs=(subject,),
                               context_ids=(str(asset_id),))  # fmt: skip
            return record, draft

        failure = AuditDraft(event_type=EVENT_REJECTED, outcome=AuditOutcome.FAILURE,
                             created_by=self._actor, context_ids=(str(asset_id),))  # fmt: skip
        try:
            return run_audited(self._store, self._audit, work, on_failure=failure).value
        except (AdmissionProfileError, AdmissionError) as exc:
            raise TransferRejectedError(
                "the upload was rejected at admission", details={"reason": exc.code.lower()}
            ) from exc


class StoreAssetLookup:
    def __init__(self, store: CoreStore, repository: AssetRepository) -> None:
        self._store = store
        self._repository = repository

    def exists(self, asset_id: AssetId) -> bool:
        with self._store.unit_of_work() as uow:
            found = self._repository.exists(uow, asset_id)
            uow.rollback()
        return found


class SystemClock:
    def __init__(self) -> None:
        self._clock = _RealClock()

    def now(self) -> UtcTimestamp:
        return UtcTimestamp.parse(format_utc(truncate_to_seconds(self._clock.now())))


@dataclass(frozen=True, slots=True)
class SystemIdFactory:
    entropy: Callable[[int], bytes] = os.urandom

    def new_intake_id(self) -> IntakeId:
        return IntakeId.from_entropy(self.entropy)

    def new_transfer_id(self) -> TransferId:
        return TransferId.from_entropy(self.entropy)

    def new_asset_id(self) -> AssetId:
        return AssetId.from_entropy(self.entropy)

    def new_case_id(self) -> CaseId:
        return CaseId.from_entropy(self.entropy)

    def new_quarantine_handle(self) -> QuarantineHandle:
        return QuarantineHandle.from_entropy(self.entropy)


__all__ = [
    "AuditedAssetAdmission",
    "IntakeQuarantine",
    "StoreAssetLookup",
    "SystemClock",
    "SystemIdFactory",
]
