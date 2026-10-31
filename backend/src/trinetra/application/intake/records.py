"""IntakeTransfer and IntakeSession (IMP-0071, IMP-0072): mutable coordination, never assurance history.

Records are immutable values; the registry replaces them. An asset id is reserved when a transfer is
received so a commit retry reuses it; a reservation does not mean the asset exists (only a repository
row does). The session is workflow coordination: after COMMITTED or ABANDONED it never changes again.

Transfer transitions: RECEIVED -> ADMITTED (admitted_asset_id must equal the reserved id) and
RECEIVED -> FAILED (an error code is required); both are terminal. A FAILED transfer is kept for
inspection but no longer counts toward slot cardinality.

Field names `intake_state` / `transfer_state` name their dimension; a bare `state` is forbidden by the
domain conventions (ERR-CL-021, PROPOSED).
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, model_validator

from trinetra.application.errors import IntakeStateConflictError
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.slots import (
    MAX_TRANSFERS_PER_INTAKE,
    SLOT_CONTRACTS,
    DeclaredFormat,
    slot_contract,
)
from trinetra.domain.asset_record import MediaType, SuppliedName
from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId
from trinetra.domain.digests import MAX_SIZE_BYTES
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId
from trinetra.domain.logical_identifier import LogicalIdentifier
from trinetra.domain.timing import UtcTimestamp
from trinetra.domain.tokens import CanonicalToken

ByteCount = Annotated[int, Field(strict=True, ge=0, le=MAX_SIZE_BYTES)]
_COUNTED: Final = frozenset({TransferState.RECEIVED, TransferState.ADMITTED})


class TransferErrorCode(CanonicalToken):
    KIND = "transfer_error_code"
    __slots__ = ()


class OriginalFilename(DomainModel):
    """The client's file name, kept as a display label only (never a path, never identity)."""

    value: SuppliedName


class IntakeTransfer(DomainModel):
    transfer_id: TransferId
    reserved_asset_id: AssetId
    slot: PackageSlot
    transfer_state: TransferState
    quarantine_handle: QuarantineHandle
    declared_format: DeclaredFormat | None
    logical_identifier: LogicalIdentifier | None
    original_filename: OriginalFilename
    media_type: MediaType | None
    received_byte_size: ByteCount
    received_at: UtcTimestamp
    admitted_asset_id: AssetId | None
    error_code: TransferErrorCode | None

    @model_validator(mode="after")
    def _state_matches_its_evidence(self) -> IntakeTransfer:
        if not slot_contract(self.slot).allows(self.declared_format):
            msg = f"declared format is not allowed for the {self.slot.value} slot"
            raise ValueError(msg)
        state = self.transfer_state
        if state is TransferState.RECEIVED and (self.admitted_asset_id or self.error_code):
            msg = "a RECEIVED transfer has neither an admitted asset nor an error"
            raise ValueError(msg)
        if state is TransferState.ADMITTED and (
            self.admitted_asset_id != self.reserved_asset_id or self.error_code is not None
        ):
            msg = "an ADMITTED transfer admits exactly its reserved asset id and has no error"
            raise ValueError(msg)
        if state is TransferState.FAILED and (self.admitted_asset_id is not None or self.error_code is None):
            msg = "a FAILED transfer has an error code and no admitted asset"
            raise ValueError(msg)
        return self

    @property
    def counts(self) -> bool:
        """Whether this transfer counts toward its slot's cardinality."""
        return self.transfer_state in _COUNTED

    def admitted(self) -> IntakeTransfer:
        self._require_received("admit")
        return self.evolve(transfer_state=TransferState.ADMITTED, admitted_asset_id=self.reserved_asset_id)

    def failed(self, error_code: TransferErrorCode) -> IntakeTransfer:
        self._require_received("fail")
        return self.evolve(transfer_state=TransferState.FAILED, error_code=error_code)

    def _require_received(self, verb: str) -> None:
        if self.transfer_state is not TransferState.RECEIVED:
            raise IntakeStateConflictError(
                f"cannot {verb} a transfer that is {self.transfer_state.value}",
                details={"transfer_id": str(self.transfer_id)},
            )


class SlotCount(DomainModel):
    """How many counted uploads a slot has against its required range."""

    slot: PackageSlot
    present: int = Field(strict=True, ge=0)
    minimum: int = Field(strict=True, ge=0)
    maximum: int = Field(strict=True, ge=1)

    @property
    def satisfied(self) -> bool:
        return self.minimum <= self.present <= self.maximum


class CommitReadiness(DomainModel):
    counts: tuple[SlotCount, ...]

    @property
    def ready(self) -> bool:
        return all(c.satisfied for c in self.counts)

    @property
    def unmet(self) -> tuple[SlotCount, ...]:
        return tuple(c for c in self.counts if not c.satisfied)


class IntakeSession(DomainModel):
    intake_id: IntakeId
    intake_state: IntakeState
    case_label: LogicalIdentifier | None
    reserved_case_id: CaseId
    created_at: UtcTimestamp
    updated_at: UtcTimestamp
    transfers: tuple[IntakeTransfer, ...] = Field(max_length=MAX_TRANSFERS_PER_INTAKE)

    @model_validator(mode="after")
    def _consistent(self) -> IntakeSession:
        if self.updated_at < self.created_at:
            msg = "updated_at cannot precede created_at"
            raise ValueError(msg)
        if len({t.transfer_id for t in self.transfers}) != len(self.transfers):
            msg = "transfer ids must be unique"
            raise ValueError(msg)
        if len({t.reserved_asset_id for t in self.transfers}) != len(self.transfers):
            msg = "reserved asset ids must be unique"
            raise ValueError(msg)
        if any(not c.maximum >= c.present for c in self.readiness().counts):
            msg = "a slot holds more counted uploads than its maximum"
            raise ValueError(msg)
        if self.intake_state is IntakeState.COMMITTED:
            counted = [t for t in self.transfers if t.counts]
            unadmitted = any(t.transfer_state is not TransferState.ADMITTED for t in counted)
            if not self.readiness().ready or unadmitted:
                msg = "a COMMITTED intake is ready and has every counted transfer ADMITTED"
                raise ValueError(msg)
        return self

    # -- queries ------------------------------------------------------------
    def readiness(self) -> CommitReadiness:
        counts = []
        for slot, contract in SLOT_CONTRACTS.items():
            present = sum(1 for t in self.transfers if t.slot is slot and t.counts)
            counts.append(SlotCount(slot=slot, present=present, minimum=contract.minimum,
                                    maximum=contract.maximum))  # fmt: skip
        return CommitReadiness(counts=tuple(counts))

    def counted(self, slot: PackageSlot) -> tuple[IntakeTransfer, ...]:
        return tuple(t for t in self.transfers if t.slot is slot and t.counts)

    def get_transfer(self, transfer_id: TransferId) -> IntakeTransfer | None:
        return next((t for t in self.transfers if t.transfer_id == transfer_id), None)

    # -- transitions (each returns a new session) ----------------------------
    def with_transfer(self, transfer: IntakeTransfer, now: UtcTimestamp) -> IntakeSession:
        self._require_open("receive a transfer")
        if transfer.transfer_state is not TransferState.RECEIVED:
            raise IntakeStateConflictError("a new transfer must be RECEIVED")
        if len(self.transfers) >= MAX_TRANSFERS_PER_INTAKE:
            raise IntakeStateConflictError("this intake has reached its transfer limit")
        if len(self.counted(transfer.slot)) >= slot_contract(transfer.slot).maximum:
            raise IntakeStateConflictError(f"the {transfer.slot.value} slot is full",
                                      details={"slot": transfer.slot.value})  # fmt: skip
        return self._touched(now, (*self.transfers, transfer))

    def with_transfer_updated(self, updated: IntakeTransfer, now: UtcTimestamp) -> IntakeSession:
        self._require_open("update a transfer")
        current = self.get_transfer(updated.transfer_id)
        if current is None:
            raise IntakeStateConflictError(
                "unknown transfer", details={"transfer_id": str(updated.transfer_id)}
            )
        legal = current.transfer_state is TransferState.RECEIVED
        if not legal or updated.transfer_state is TransferState.RECEIVED:
            raise IntakeStateConflictError(
                "only RECEIVED -> ADMITTED or FAILED is a legal transfer transition"
            )
        unchanged = current.evolve(
            transfer_state=updated.transfer_state,
            admitted_asset_id=updated.admitted_asset_id,
            error_code=updated.error_code,
        )
        if unchanged != updated:
            raise IntakeStateConflictError("a transition may change only state, admitted asset and error")
        replaced = tuple(updated if t.transfer_id == updated.transfer_id else t for t in self.transfers)
        return self._touched(now, replaced)

    def committed(self, now: UtcTimestamp) -> IntakeSession:
        self._require_open("commit")
        return self._touched(now, self.transfers, IntakeState.COMMITTED)

    def abandoned(self, now: UtcTimestamp) -> IntakeSession:
        self._require_open("abandon")
        return self._touched(now, self.transfers, IntakeState.ABANDONED)

    def _require_open(self, verb: str) -> None:
        if self.intake_state is not IntakeState.OPEN:
            raise IntakeStateConflictError(f"cannot {verb}: the intake is {self.intake_state.value}",
                                      details={"intake_id": str(self.intake_id)})  # fmt: skip

    def _touched(self, now: UtcTimestamp, transfers: tuple[IntakeTransfer, ...],
                 state: IntakeState | None = None) -> IntakeSession:  # fmt: skip
        if now < self.updated_at:
            raise IntakeStateConflictError("time cannot move backwards")
        return self.evolve(updated_at=now, transfers=transfers, intake_state=state or self.intake_state)


__all__ = [
    "ByteCount",
    "CommitReadiness",
    "IntakeSession",
    "IntakeTransfer",
    "OriginalFilename",
    "SlotCount",
    "TransferErrorCode",
]
