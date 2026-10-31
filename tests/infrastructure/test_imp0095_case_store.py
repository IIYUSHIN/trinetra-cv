"""IMP-0095 tests: the real intake -> Case path over the Core database, CAS and audit journal."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from intake_support import FrozenClock, SequentialIds
from projection_support import CASE_ID, MODEL_ID, OTHER_ID, T0
from trinetra.application.errors import CaseAlreadyStoredError
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.commit_case import CommitIntakeService
from trinetra.application.intake.enums import IntakeState, PackageSlot
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.audit import AuditOutcome
from trinetra.domain.case_assets import CaseAssetRole, link_assets
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.decisions import CommandId
from trinetra.domain.digests import DigestSha256
from trinetra.domain.intake_ids import IntakeId
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


@pytest.fixture
def rig(store, state_root):
    return Rig(store, state_root)


def commit_minimal(rig, label=None):
    rig.new_intake(label)
    data = rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    model = rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    return data, model, rig.service.commit(rig.intake)


def test_a_dataset_and_a_model_become_a_persistent_preflight_case(rig):
    data, model, result = commit_minimal(rig, "Night patrol v3")
    case = rig.cases.get(result.case.case_id)
    assert case == result.case
    assert case.lifecycle_state is CaseLifecycleState.PREFLIGHT
    assert case.case_state_revision.value == 1
    assert case.case_label == "Night patrol v3"
    assert [(lk.role, lk.asset_id) for lk in case.asset_links] == [
        (CaseAssetRole.DATASET, data.reserved_asset_id), (CaseAssetRole.MODEL, model.reserved_asset_id)]
    assert rig.registry.require(rig.intake).intake_state is IntakeState.COMMITTED


def test_the_audit_journal_records_two_ingestions_then_the_case_and_stays_intact(rig):
    data, model, result = commit_minimal(rig)
    events, report = rig.events()
    assert [e.event_type.text for e in events] == ["asset_ingested", "asset_ingested", "case_created"]
    created = events[2]
    assert created.outcome is AuditOutcome.SUCCESS
    [subject] = created.subject_refs
    assert (subject.object_type.text, subject.object_id) == ("case", str(result.case.case_id))
    assert subject.record_digest == DigestSha256.parse(result.case.canonical_digest())
    assert created.context_ids == (str(rig.intake), str(data.reserved_asset_id), str(model.reserved_asset_id))
    assert report.intact


def test_optional_references_and_metadata_link_correctly(rig):
    rig.new_intake()
    ref1 = rig.upload(PackageSlot.REFERENCE, media="application/zip")
    meta = rig.upload(PackageSlot.CONTRIBUTOR_METADATA, media="text/plain", data=b"notes about the data")
    rig.upload(PackageSlot.MODEL, "onnx")
    data = rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    ref2 = rig.upload(PackageSlot.REFERENCE, media="application/zip")
    case = rig.service.commit(rig.intake).case
    stored = rig.cases.get(case.case_id)
    assert stored == case
    by_role = {}
    for lk in stored.asset_links:
        by_role.setdefault(lk.role, []).append(lk.asset_id)
    assert by_role[CaseAssetRole.DATASET] == [data.reserved_asset_id]
    assert by_role[CaseAssetRole.CONTRIBUTOR_METADATA] == [meta.reserved_asset_id]
    assert by_role[CaseAssetRole.REFERENCE] == [ref1.reserved_asset_id, ref2.reserved_asset_id]


def test_a_retry_after_a_first_commit_returns_the_same_case_without_a_second_audit_event(rig):
    _d, _m, first = commit_minimal(rig)
    again = rig.service.commit(rig.intake)
    assert again.case == first.case and again.case_created is False
    events, _ = rig.events()
    assert [e.event_type.text for e in events].count("case_created") == 1


def foreign_case():
    """A valid Case whose assets were never admitted to this database."""
    entries = [(CaseAssetRole.DATASET, OTHER_ID), (CaseAssetRole.MODEL, MODEL_ID)]
    return AssuranceCase.create(CASE_ID, link_assets(entries, T0), T0)


def an_intake_id():
    return IntakeId.from_entropy(lambda size: b"\x05" * size)


def test_a_missing_case_reads_as_none(rig):
    assert rig.cases.get(CASE_ID) is None


def test_storing_a_case_twice_is_refused_and_leaves_the_first_untouched(rig):
    _d, _m, result = commit_minimal(rig)
    with pytest.raises(CaseAlreadyStoredError) as error:
        rig.cases.create(result.case, intake_id=rig.intake)
    assert error.value.details["case_id"] == str(result.case.case_id)
    assert rig.cases.get(result.case.case_id) == result.case
    events, report = rig.events()
    last = events[-1]
    assert (last.event_type.text, last.outcome) == ("case_creation_failed", AuditOutcome.FAILURE)
    assert events[-1].reason_code == "CASE_ALREADY_EXISTS"
    assert events[-1].context_ids == (str(result.case.case_id), str(rig.intake))
    assert report.intact


def test_a_case_naming_assets_that_do_not_exist_is_not_stored_and_the_failure_is_audited(rig):
    with pytest.raises(IntegrityError):
        rig.cases.create(foreign_case(), intake_id=an_intake_id())
    assert rig.cases.get(CASE_ID) is None
    events, report = rig.events()
    assert [(e.event_type.text, e.outcome) for e in events] == [
        ("case_creation_failed", AuditOutcome.FAILURE)]
    assert events[0].reason_code == "INTERNAL_ERROR"
    assert report.intact


def test_if_the_audit_write_fails_the_case_is_not_stored(rig):
    _d, _m, result = commit_minimal(rig)
    entries = [(CaseAssetRole.DATASET, result.case.asset_links[0].asset_id),
               (CaseAssetRole.MODEL, result.case.asset_links[1].asset_id)]
    twin_id = type(CASE_ID).from_entropy(lambda size: b"\x66" * size)
    twin = AssuranceCase.create(twin_id, link_assets(entries, T0), T0)

    class Failing:
        def __init__(self, inner):
            self.inner, self.armed = inner, True

        def append(self, uow, draft):
            if self.armed and draft.event_type.text == "case_created":
                self.armed = False
                raise RuntimeError("journal unavailable")
            return self.inner.append(uow, draft)

    store = AuditedCaseStore(rig.store, CaseRepository(), Failing(rig.audit))
    with pytest.raises(RuntimeError, match=r"journal unavailable"):
        store.create(twin, intake_id=an_intake_id())
    assert store.get(twin_id) is None  # no Case without its audit fact
    events, _ = rig.events()
    assert [e.event_type.text for e in events][-1] == "case_creation_failed"


def test_reading_does_not_leave_a_transaction_open(rig):
    _d, _m, result = commit_minimal(rig)
    for _ in range(3):
        assert rig.cases.get(result.case.case_id) == result.case
    with rig.store.unit_of_work() as uow:  # the writer lock is free again
        uow.rollback()
