"""A real intake -> Case rig over the Core database, CAS and audit journal (not a test module)."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from intake_support import FrozenClock, SequentialIds
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.commit_case import CommitIntakeService
from trinetra.application.intake.enums import PackageSlot
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.decisions import CommandId
from trinetra.infrastructure.asset_admission_service import AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_repository import AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.case_repository import CaseRepository
from trinetra.infrastructure.case_store import AuditedCaseStore
from trinetra.infrastructure.intake_adapters import AuditedAssetAdmission, IntakeQuarantine, StoreAssetLookup
from trinetra.timeutil import FixedClock

ZIP = b"PK\x03\x04" + b"0" * 40
WHEN = datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


class Rig:
    def __init__(self, store, state_root):
        self.store = store
        numbers = iter(range(1, 10**6))
        self.audit = AuditRepository(clock=FixedClock(WHEN),
                                     entropy=lambda n: next(numbers).to_bytes(n, "big"))
        self.quarantine = IntakeQuarantine.for_state_root(state_root)
        assets = AssetRepository()
        service = AssetAdmissionService(CasStore(CasLayout.for_state_root(state_root)), assets)
        self.admission = AuditedAssetAdmission(store, self.audit, service, self.quarantine)
        self.lookup = StoreAssetLookup(store, assets)
        self.cases = AuditedCaseStore(store, CaseRepository(), self.audit)
        self.clock, self.ids, self.registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
        self.receive = ReceiveTransferService(self.clock, self.ids, self.registry, self.quarantine)
        admit = TransferAdmissionService(self.clock, self.registry, self.quarantine, self.admission,
                                         self.lookup)
        self.service = CommitIntakeService(CommitAdmissionStep(self.registry, admit), self.registry,
                                           self.cases, self.clock)
        self.intake = None
        self._n = 0

    def new_intake(self, label=None):
        self.intake = CreateIntakeService(self.clock, self.ids, self.registry).create(
            CreateIntakeCommand(command_id=cid(250), case_label=label)).intake_id
        return self.intake

    def upload(self, slot, fmt=None, media="application/octet-stream", data=ZIP):
        self._n += 1
        return self.receive.receive(ReceiveTransferCommand(
            intake_id=self.intake, command_id=cid(self._n), slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None, logical_identifier=None,
            filename=OriginalFilename(value=f"f{self._n}.bin"), media_type=media, stream=io.BytesIO(data),
        ))  # fmt: skip

    def events(self):
        with self.store.unit_of_work() as uow:
            page = self.audit.list_page(uow, limit=100)
            report = verify_chain(uow, page.events[0].journal_id) if page.events else None
            uow.rollback()
        return page.events, report


def commit_minimal(rig, label=None):
    rig.new_intake(label)
    data = rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    model = rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    return data, model, rig.service.commit(rig.intake)
