"""IMP-0095 tests: the real intake -> Case path over the Core database, CAS and audit journal."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from commit_support import commit_minimal
from projection_support import CASE_ID, MODEL_ID, OTHER_ID, T0
from trinetra.application.errors import CaseAlreadyStoredError
from trinetra.application.intake.enums import IntakeState, PackageSlot
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.audit import AuditOutcome
from trinetra.domain.case_assets import CaseAssetRole, link_assets
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.digests import DigestSha256
from trinetra.domain.intake_ids import IntakeId
from trinetra.infrastructure.case_repository import CaseRepository
from trinetra.infrastructure.case_store import AuditedCaseStore


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
