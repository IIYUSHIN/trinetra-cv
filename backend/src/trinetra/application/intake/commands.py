"""Intake commands and results (IMP-0074, IMP-0075, IMP-0078).

Commands are immutable values. `command_id` is collected from the start so the public contract does not
change when durable replay protection arrives (it is process-local only for now). The upload command
carries a live byte stream, so it is a frozen dataclass, not a serializable model.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.application.ports.storage import ByteStream
from trinetra.domain.assets import AssetId
from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId
from trinetra.domain.logical_identifier import LogicalIdentifier


class CreateIntakeCommand(DomainModel):
    command_id: CommandId
    case_label: LogicalIdentifier | None = None


@dataclass(frozen=True, slots=True)
class ReceiveTransferCommand:
    intake_id: IntakeId
    command_id: CommandId
    slot: PackageSlot
    declared_format: DeclaredFormat | None
    logical_identifier: str | None
    filename: OriginalFilename
    media_type: str | None
    stream: ByteStream


class CommitIntakeCommand(DomainModel):
    intake_id: IntakeId
    command_id: CommandId


class CommitIntakeResult(DomainModel):
    case_id: CaseId
    case_state_revision: CaseStateRevision
    admitted_asset_ids: tuple[AssetId, ...] = Field(min_length=2)


__all__ = ["CommitIntakeCommand", "CommitIntakeResult", "CreateIntakeCommand", "ReceiveTransferCommand"]
