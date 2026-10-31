"""IdFactoryPort: strongly typed identifier generation; tests inject deterministic values."""

from __future__ import annotations

from typing import Protocol

from trinetra.domain.assets import AssetId
from trinetra.domain.case import CaseId
from trinetra.domain.intake_ids import IntakeId, QuarantineHandle, TransferId


class IdFactoryPort(Protocol):
    def new_intake_id(self) -> IntakeId: ...
    def new_transfer_id(self) -> TransferId: ...
    def new_asset_id(self) -> AssetId: ...
    def new_case_id(self) -> CaseId: ...
    def new_quarantine_handle(self) -> QuarantineHandle: ...


__all__ = ["IdFactoryPort"]
