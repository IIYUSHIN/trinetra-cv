"""TransferAdmissionService (IMP-0077): admit one received transfer as an authoritative AssetRecord.

Runs under the exclusive intake lock, like the commit it belongs to. A RECEIVED transfer is handed to the
`AssetAdmissionPort` with the asset id reserved at upload time; on success the transfer becomes ADMITTED
and its quarantine entry is removed. Bytes the admission rejects mark the transfer FAILED (kept for
inspection, no longer counted toward its slot, so the client may upload again) and the typed rejection
propagates. Anything else (an infrastructure failure) leaves the transfer RECEIVED so a retry is safe.

Idempotent by construction: if the reserved asset id already has a record (admitted by an earlier attempt
whose result was not recorded), the transfer is marked ADMITTED without a second admission; an ADMITTED
transfer is only verified (its record must still exist) and reused. A reservation alone never counts as
an asset: only a repository record does (`AssetLookupPort`).

PROPOSED (ERR-CL-022): a rejected transfer becomes FAILED with the port's reason as its error code.
"""

from __future__ import annotations

import logging
from typing import Final

from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeStateConflictError,
    TransferRejectedError,
)
from trinetra.application.intake.enums import IntakeState, TransferState
from trinetra.application.intake.records import IntakeSession, IntakeTransfer, TransferErrorCode
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.slots import slot_contract
from trinetra.application.ports.clock import ClockPort
from trinetra.application.ports.storage import AssetAdmissionPort, AssetLookupPort, QuarantinePort
from trinetra.domain.asset_admission import OCTET
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId
from trinetra.domain.tokens import TokenError
from trinetra.structured_log import log_event

_LOG: Final = logging.getLogger("trinetra.application.intake")
_FALLBACK_REASON: Final = "rejected"


class TransferAdmissionService:
    def __init__(
        self,
        clock: ClockPort,
        registry: InMemoryIntakeRegistry,
        quarantine: QuarantinePort,
        admission: AssetAdmissionPort,
        lookup: AssetLookupPort,
    ) -> None:
        self._clock = clock
        self._registry = registry
        self._quarantine = quarantine
        self._admission = admission
        self._lookup = lookup

    def admit(self, intake_id: IntakeId, transfer_id: TransferId) -> IntakeTransfer:
        with self._registry.exclusive(intake_id):
            session = self._registry.require(intake_id)
            if session.intake_state is not IntakeState.OPEN:
                raise IntakeStateConflictError(
                    f"cannot admit: the intake is {session.intake_state.value}",
                    details={"intake_id": str(intake_id)},
                )
            transfer = session.get_transfer(transfer_id)
            if transfer is None:
                raise IntakeStateConflictError("unknown transfer", details={"transfer_id": str(transfer_id)})
            return self.admit_locked(session, transfer)

    def admit_locked(self, session: IntakeSession, transfer: IntakeTransfer) -> IntakeTransfer:
        """Admit `transfer` of `session`; the caller holds the intake lock and has checked OPEN."""
        if transfer.transfer_state is TransferState.ADMITTED:
            asset_id = transfer.admitted_asset_id
            if asset_id is None or not self._lookup.exists(asset_id):
                raise ApplicationConflictError(
                    "an admitted transfer has no asset record",
                    details={"transfer_id": str(transfer.transfer_id)},
                )
            return transfer
        if transfer.transfer_state is TransferState.FAILED:
            raise IntakeStateConflictError(
                "this transfer failed and cannot be admitted; upload it again",
                details={"transfer_id": str(transfer.transfer_id)},
            )
        if self._lookup.exists(transfer.reserved_asset_id):
            return self._mark_admitted(session, transfer)
        try:
            record = self._admission.admit(
                handle=transfer.quarantine_handle,
                asset_id=transfer.reserved_asset_id,
                kind=slot_contract(transfer.slot).asset_kind,
                media_type=transfer.media_type or OCTET,
                supplied_name=transfer.original_filename.value,
            )
        except TransferRejectedError as exc:
            self._mark_failed(session, transfer, _reason(exc))
            raise
        if record.record_id != transfer.reserved_asset_id:
            raise ApplicationConflictError(
                "admission produced a different asset id than the one reserved",
                details={"transfer_id": str(transfer.transfer_id)},
            )
        return self._mark_admitted(session, transfer)

    # -- state changes --------------------------------------------------------------------------
    def _mark_admitted(self, session: IntakeSession, transfer: IntakeTransfer) -> IntakeTransfer:
        updated = transfer.admitted()
        self._registry.replace(session.with_transfer_updated(updated, self._clock.now()))
        self._discard(updated.quarantine_handle)
        return updated

    def _mark_failed(self, session: IntakeSession, transfer: IntakeTransfer, reason: str) -> None:
        failed = transfer.failed(TransferErrorCode(reason))
        self._registry.replace(session.with_transfer_updated(failed, self._clock.now()))
        self._discard(failed.quarantine_handle)

    def _discard(self, handle: QuarantineHandle) -> None:
        try:
            self._quarantine.remove(handle)
        except Exception as exc:  # noqa: BLE001 - cleanup must never undo or mask the admission result
            log_event(_LOG, "WARNING", "intake.quarantine_cleanup_failed", handle=str(handle),
                      error_type=type(exc).__name__)  # fmt: skip


def _reason(error: TransferRejectedError) -> str:
    raw = (error.details or {}).get("reason")
    if not isinstance(raw, str):
        return _FALLBACK_REASON
    try:
        TransferErrorCode(raw)
    except TokenError:
        return _FALLBACK_REASON
    return raw


__all__ = ["TransferAdmissionService"]
