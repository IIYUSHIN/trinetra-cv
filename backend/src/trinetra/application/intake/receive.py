"""ReceiveTransferService (IMP-0075): stream one upload into quarantine and record it on the intake.

Three phases so a slow upload never blocks the intake: (1) under the intake lock, require OPEN, check the
slot has room counting uploads already in flight, and reserve ids; (2) outside any lock, stream the bytes
into quarantine with the size ceiling enforced as they arrive; (3) under the lock again, re-require OPEN
and record a RECEIVED transfer with the byte count the quarantine actually wrote, never a client claim.

A failed or rejected upload leaves no transfer and no quarantine entry. If the cleanup itself fails the
error is logged (bounded) and startup recovery handles the residue. No AssetRecord is created here: that
is admission (IMP-0077). A repeated command id returns the original transfer without reading the stream
again (process-local replay only; the same id for a different request is a conflict).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Final

from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeStateConflictError,
    TransferRejectedError,
    TransferTooLargeError,
)
from trinetra.application.intake.commands import ReceiveTransferCommand
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.records import IntakeTransfer
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.slots import MAX_TRANSFERS_PER_INTAKE, slot_contract
from trinetra.application.intake.validation import ValidatedTransferRequest, validate_transfer_request
from trinetra.application.ports.clock import ClockPort
from trinetra.application.ports.ids import IdFactoryPort
from trinetra.application.ports.storage import QuarantinePort
from trinetra.domain.asset_admission import DEFAULT_PROFILE, AdmissionProfile
from trinetra.domain.assets import AssetId
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId
from trinetra.errors import TrinetraError
from trinetra.structured_log import log_event

_LOG: Final = logging.getLogger("trinetra.application.intake")


class ReceiveTransferService:
    def __init__(
        self,
        clock: ClockPort,
        ids: IdFactoryPort,
        registry: InMemoryIntakeRegistry,
        quarantine: QuarantinePort,
        profile: AdmissionProfile = DEFAULT_PROFILE,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._registry = registry
        self._quarantine = quarantine
        self._profile = profile
        self._guard = threading.Lock()
        self._in_flight: Counter[tuple[IntakeId, PackageSlot]] = Counter()

    def receive(self, command: ReceiveTransferCommand) -> IntakeTransfer:
        request = validate_transfer_request(command, self._profile)
        fingerprint = _fingerprint(command, request)
        replay = self._replay(command, fingerprint)
        if replay is not None:
            return replay
        transfer_id, asset_id, handle = self._reserve(command)
        try:
            written = self._stream(command, request, handle)
            return self._record(command, request, fingerprint, transfer_id, asset_id, handle, written)
        except BaseException:
            self._discard(handle)
            raise
        finally:
            self._release(command.intake_id, command.slot)

    # -- phase 1 --------------------------------------------------------------------------------
    def _replay(self, command: ReceiveTransferCommand, fingerprint: str) -> IntakeTransfer | None:
        previous = self._registry.recall_transfer(command.command_id)
        if previous is None:
            return None
        intake_id, transfer_id, remembered = previous
        if remembered != fingerprint or intake_id != command.intake_id:
            raise ApplicationConflictError(
                "this command id was already used for a different upload",
                details={"command_id": str(command.command_id)},
            )
        found = self._registry.require(intake_id).get_transfer(transfer_id)
        if found is None:  # unreachable while transfers are never removed
            raise ApplicationConflictError("the remembered transfer no longer exists")
        return found

    def _reserve(self, command: ReceiveTransferCommand) -> tuple[TransferId, AssetId, QuarantineHandle]:
        transfer_id = self._ids.new_transfer_id()
        asset_id = self._ids.new_asset_id()
        handle = self._ids.new_quarantine_handle()
        with self._registry.exclusive(command.intake_id):
            session = self._registry.require(command.intake_id)
            if session.intake_state is not IntakeState.OPEN:
                raise IntakeStateConflictError(
                    f"cannot receive an upload: the intake is {session.intake_state.value}",
                    details={"intake_id": str(session.intake_id)},
                )
            with self._guard:
                pending = sum(n for (i, _), n in self._in_flight.items() if i == command.intake_id)
                pending_here = self._in_flight[(command.intake_id, command.slot)]
                if len(session.transfers) + pending >= MAX_TRANSFERS_PER_INTAKE:
                    raise IntakeStateConflictError("this intake has reached its transfer limit")
                if len(session.counted(command.slot)) + pending_here >= slot_contract(command.slot).maximum:
                    raise IntakeStateConflictError(
                        f"the {command.slot.value} slot is full", details={"slot": command.slot.value}
                    )
                self._in_flight[(command.intake_id, command.slot)] += 1
        return transfer_id, asset_id, handle

    # -- phase 2 --------------------------------------------------------------------------------
    def _stream(
        self, command: ReceiveTransferCommand, request: ValidatedTransferRequest, handle: QuarantineHandle
    ) -> int:
        try:
            self._quarantine.create_entry(handle)
            written = self._quarantine.write_stream(handle, command.stream, max_bytes=request.max_bytes)
        except TrinetraError:
            raise
        except OSError as exc:
            raise TransferRejectedError("the upload stream failed") from exc
        if type(written) is not int or not 0 <= written <= request.max_bytes:
            raise TransferTooLargeError(details={"slot": request.slot.value})
        return written

    # -- phase 3 --------------------------------------------------------------------------------
    def _record(  # noqa: PLR0917
        self,
        command: ReceiveTransferCommand,
        request: ValidatedTransferRequest,
        fingerprint: str,
        transfer_id: TransferId,
        asset_id: AssetId,
        handle: QuarantineHandle,
        written: int,
    ) -> IntakeTransfer:
        with self._registry.exclusive(command.intake_id):
            earlier = self._replay(command, fingerprint)
            if earlier is not None:  # a concurrent identical command finished first
                self._discard(handle)
                return earlier
            session = self._registry.require(command.intake_id)
            now = self._clock.now()
            transfer = IntakeTransfer(
                transfer_id=transfer_id,
                reserved_asset_id=asset_id,
                slot=request.slot,
                transfer_state=TransferState.RECEIVED,
                quarantine_handle=handle,
                declared_format=request.declared_format,
                logical_identifier=request.logical_identifier,
                original_filename=command.filename,
                media_type=request.media_type,
                received_byte_size=written,
                received_at=now,
                admitted_asset_id=None,
                error_code=None,
            )
            self._registry.replace(session.with_transfer(transfer, now))
            self._registry.remember_transfer(command.command_id, command.intake_id, transfer_id, fingerprint)
            return transfer

    # -- bookkeeping ----------------------------------------------------------------------------
    def _release(self, intake_id: IntakeId, slot: PackageSlot) -> None:
        with self._guard:
            self._in_flight[(intake_id, slot)] -= 1
            if self._in_flight[(intake_id, slot)] <= 0:
                del self._in_flight[(intake_id, slot)]

    def _discard(self, handle: QuarantineHandle) -> None:
        try:
            self._quarantine.remove(handle)
        except Exception as exc:  # noqa: BLE001 - cleanup must never mask the real failure
            log_event(_LOG, "WARNING", "intake.quarantine_cleanup_failed", handle=str(handle),
                      error_type=type(exc).__name__)  # fmt: skip


def _fingerprint(command: ReceiveTransferCommand, request: ValidatedTransferRequest) -> str:
    parts = (
        command.slot.value,
        request.declared_format.text if request.declared_format else "",
        request.logical_identifier or "",
        command.filename.value,
        request.media_type or "",
    )
    return "\x1f".join(parts)


__all__ = ["ReceiveTransferService"]
