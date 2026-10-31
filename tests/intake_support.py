"""Builders shared by the intake tests (not a test module)."""

from __future__ import annotations

import itertools

from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.records import IntakeSession, IntakeTransfer, OriginalFilename
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.assets import AssetId
from trinetra.domain.case import CaseId
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId
from trinetra.domain.timing import UtcTimestamp

T0 = UtcTimestamp.parse("2026-10-31T08:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T08:00:05Z")
T2 = UtcTimestamp.parse("2026-10-31T08:00:09Z")
_counter = itertools.count(1)


def entropy(n):
    return next(_counter).to_bytes(n, "big")


def transfer(slot=PackageSlot.DATASET, fmt="coco", state=TransferState.RECEIVED, size=10, name="data.zip"):
    base = IntakeTransfer(
        transfer_id=TransferId.from_entropy(entropy),
        reserved_asset_id=AssetId.from_entropy(entropy),
        slot=slot,
        transfer_state=TransferState.RECEIVED,
        quarantine_handle=QuarantineHandle.from_entropy(entropy),
        declared_format=DeclaredFormat(fmt) if fmt else None,
        logical_identifier=None,
        original_filename=OriginalFilename(value=name),
        media_type="application/zip",
        received_byte_size=size,
        received_at=T0,
        admitted_asset_id=None,
        error_code=None,
    )
    if state is TransferState.ADMITTED:
        return base.admitted()
    if state is TransferState.FAILED:
        from trinetra.application.intake.records import TransferErrorCode

        return base.failed(TransferErrorCode("rejected"))
    return base


def session(transfers=(), state=IntakeState.OPEN, label=None):
    return IntakeSession(
        intake_id=IntakeId.from_entropy(entropy),
        intake_state=state,
        case_label=label,
        reserved_case_id=CaseId.from_entropy(entropy),
        created_at=T0,
        updated_at=T0,
        transfers=tuple(transfers),
    )


def ready_session():
    return session([transfer(PackageSlot.DATASET, "coco"), transfer(PackageSlot.MODEL, "onnx")])


class FrozenClock:
    """ClockPort double: time only moves when the test says so."""

    def __init__(self, now=T0):
        self.current = now

    def now(self):
        return self.current

    def set(self, moment):
        self.current = moment


class SequentialIds:
    """IdFactoryPort double: distinct deterministic ids."""

    def __init__(self):
        self._n = itertools.count(1)

    def _draw(self, cls):
        return cls.from_entropy(lambda size: next(self._n).to_bytes(size, "big"))

    def new_intake_id(self):
        return self._draw(IntakeId)

    def new_transfer_id(self):
        return self._draw(TransferId)

    def new_asset_id(self):
        return self._draw(AssetId)

    def new_case_id(self):
        return self._draw(CaseId)

    def new_quarantine_handle(self):
        return self._draw(QuarantineHandle)


class MemoryQuarantine:
    """QuarantinePort double: bytes in a dict, the size ceiling enforced while streaming."""

    def __init__(self):
        self.entries = {}
        self.removed = []
        self.fail_remove = False

    def create_entry(self, handle):
        if handle in self.entries:
            raise FileExistsError(str(handle))
        self.entries[handle] = bytearray()

    def write_stream(self, handle, source, *, max_bytes):
        from trinetra.application.errors import TransferTooLargeError

        buffer = self.entries[handle]
        while chunk := source.read(4096):
            if len(buffer) + len(chunk) > max_bytes:
                raise TransferTooLargeError(details={"max_bytes": max_bytes})
            buffer.extend(chunk)
        return len(buffer)

    def remove(self, handle):
        if self.fail_remove:
            raise OSError("disk says no")
        self.removed.append(handle)
        self.entries.pop(handle, None)


class ScriptedStream:
    """A byte stream that can pause, call back, or fail part way through."""

    def __init__(self, data=b"payload", *, fail_after=None, error=None, on_read=None):
        import io

        self._buffer = io.BytesIO(data)
        self.fail_after = fail_after
        self.error = error
        self.on_read = on_read
        self.reads = 0
        self.delivered = 0

    def read(self, size, /):
        self.reads += 1
        if self.on_read is not None:
            callback, self.on_read = self.on_read, None
            callback()
        if self.fail_after is not None and self.delivered >= self.fail_after:
            raise self.error or OSError("connection reset")
        chunk = self._buffer.read(size if self.fail_after is None else min(size, 3))
        self.delivered += len(chunk)
        return chunk


class FakeAssets:
    """AssetAdmissionPort + AssetLookupPort double sharing one 'repository' of reserved ids."""

    def __init__(self):
        self.records = {}
        self.calls = []
        self.error = None
        self.wrong_id = False
        self.on_admit = None

    def exists(self, asset_id):
        return asset_id in self.records

    def admit(self, *, handle, asset_id, kind, media_type, supplied_name):
        from trinetra.domain.asset_record import ActorId, AssetRecord
        from trinetra.domain.digests import DigestSha256

        self.calls.append({"handle": handle, "asset_id": asset_id, "kind": kind,
                           "media_type": media_type, "supplied_name": supplied_name})  # fmt: skip
        if self.on_admit is not None:
            self.on_admit()
        if self.error is not None:
            raise self.error
        record_id = AssetId.from_entropy(lambda n: b"\x7f" * n) if self.wrong_id else asset_id
        record = AssetRecord(
            record_id=record_id, asset_kind=kind, blob_digest=DigestSha256.of(b"x"), size_bytes=1,
            media_type=media_type, supplied_name=supplied_name, created_at=T0, created_by=ActorId("core"),
            parent_asset_refs=(), source_metadata_refs=(),
        )  # fmt: skip
        self.records[record_id] = record
        return record


class MemoryCaseStore:
    """CaseStorePort double: keeps Cases in memory and can be told to fail at chosen moments."""

    def __init__(self):
        self.cases = {}
        self.created = []  # (case, intake_id) in call order
        self.fail_next = None
        self.after_create = None

    def get(self, case_id):
        return self.cases.get(case_id)

    def create(self, case, *, intake_id):
        from trinetra.application.errors import CaseAlreadyStoredError

        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        if case.case_id in self.cases:
            raise CaseAlreadyStoredError
        self.cases[case.case_id] = case
        self.created.append((case, intake_id))
        if self.after_create is not None:
            self.after_create()
