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
