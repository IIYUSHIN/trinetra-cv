"""IMP-0098 tests: uploading a reference is NOT approving it - approval is a separate Core act, per Case."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from commit_support import commit_minimal
from projection_support import FixedClock
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot
from trinetra.application.preflight.planner import PreflightPlanner
from trinetra.application.projections.preflight import ReferenceReadiness, build_preflight
from trinetra.domain.capability import CapabilityName, CapabilityState
from trinetra.domain.capability_snapshot import CapabilityAssessment, CapabilityReasonCode
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.reference_records import (
    ReasonCode,
    ReferenceAssuranceRecord,
    ReferenceHistory,
)
from trinetra.domain.references import ReferenceEventId, ReferenceId, ReferencePurpose
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.reference_approvals import StoreApprovedReferences
from trinetra.infrastructure.reference_repository import ReferenceQueryError, ReferenceRepository

NOW = UtcTimestamp.parse("2026-10-31T12:00:00Z")
EARLIER = UtcTimestamp.parse("2026-10-31T09:00:00Z")
LATER = UtcTimestamp.parse("2026-10-31T15:00:00Z")
GT = ReferencePurpose("ground_truth")
MASKS = ReferencePurpose("masks")
SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "trinetra"


def refs_of(case):
    return [lk.asset_id for lk in case.asset_links if lk.role is CaseAssetRole.REFERENCE]


def linked_to(case, role):
    return next(lk.asset_id for lk in case.asset_links if lk.role is role)


def commit_with_references(rig, count=2):
    rig.new_intake()
    rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    for _ in range(count):
        rig.upload(PackageSlot.REFERENCE, media="application/zip")
        # distinct bytes are not needed: assets are distinct records even for equal content
    return rig.service.commit(rig.intake).case


def approve(store, n, asset, purpose, *, start=EARLIER, until=None):
    rid = ReferenceId.from_entropy(lambda s: bytes([n]) * s)
    record = ReferenceAssuranceRecord(
        reference_id=rid, asset_id=asset, purpose=purpose, authority="Harbour QA board",
        valid_from=start, valid_until=until, created_at=EARLIER)
    first = ReferenceHistory.begin(ReferenceEventId.from_entropy(lambda s: bytes([n + 100]) * s), rid,
                                   EARLIER).events[0]
    with store.unit_of_work() as uow:
        ReferenceRepository().add(uow, record, first)
        uow.commit()
    return rid


def revoke(store, rid):
    repo = ReferenceRepository()
    with store.unit_of_work() as uow:
        history = repo.history(uow, rid)
        revoked = history.revoke(ReferenceEventId.from_entropy(lambda s: b"\xee" * s), EARLIER,
                                 reason_code=ReasonCode("withdrawn"), reason_text="Withdrawn by the QA board")
        repo.append_state_event(uow, revoked.events[-1], 1)
        uow.commit()


def row_count(store, table):
    with store.unit_of_work() as uow:
        n = uow.connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()  # noqa: S608
        uow.rollback()
    return n


@pytest.fixture
def approvals(store):
    return StoreApprovedReferences(store, ReferenceRepository())


# ------------------------------------------------------------------ upload is not approval
def test_uploading_references_creates_assets_and_links_but_no_reference_record_at_all(rig, store):
    case = commit_with_references(rig, 2)
    assert len(refs_of(case)) == 2
    assert row_count(store, "reference_records") == 0
    assert row_count(store, "reference_state_events") == 0


def test_no_approval_event_is_ever_written_by_intake_or_commit(rig):
    commit_with_references(rig)
    assert set(rig.event_types()) == {"asset_ingested", "case_created"}


def test_a_case_with_uploaded_references_has_no_approved_purpose(rig, approvals):
    case = commit_with_references(rig)
    assert approvals.approved_purposes(case, NOW) == frozenset()


def test_no_purpose_lookup_finds_an_uploaded_reference(rig, store):
    commit_with_references(rig)
    repo = ReferenceRepository()
    with store.unit_of_work() as uow:
        for purpose in (GT, MASKS, ReferencePurpose("reference"), ReferencePurpose("approved")):
            assert repo.find_active_by_purpose(uow, purpose, NOW) == ()
        uow.rollback()


def test_the_upload_command_has_no_way_to_name_a_purpose_authority_or_approval():
    names = {f.name for f in dataclasses.fields(ReceiveTransferCommand)}
    assert names.isdisjoint(
        {"purpose", "authority", "approved", "approval", "reference_purpose", "valid_from"})
    assert names.isdisjoint(set(CreateIntakeCommand.model_fields) - {"command_id", "case_label"})


def test_a_reference_uploaded_with_an_approval_sounding_name_is_still_just_an_asset(rig, store, approvals):
    rig.new_intake()
    rig.upload(PackageSlot.DATASET, "coco", "application/zip")
    rig.upload(PackageSlot.MODEL, "onnx", data=b"\x08\x07" + b"m" * 30)
    import io

    from commit_support import cid
    from trinetra.application.intake.records import OriginalFilename

    rig.receive.receive(ReceiveTransferCommand(
        intake_id=rig.intake, command_id=cid(99), slot=PackageSlot.REFERENCE, declared_format=None,
        logical_identifier="APPROVED ground truth",
        filename=OriginalFilename(value="APPROVED_ground_truth.zip"), media_type="application/zip",
        stream=io.BytesIO(b"PK\x03\x04" + b"1" * 20)))
    case = rig.service.commit(rig.intake).case
    assert approvals.approved_purposes(case, NOW) == frozenset()
    assert row_count(store, "reference_records") == 0


# ------------------------------------------------------------------ what the planner and the view show
def needs(purpose):
    return CapabilityAssessment(
        capability=CapabilityName("label_agreement"), capability_state=CapabilityState.AVAILABLE,
        reason_code=CapabilityReasonCode("ready"), reason_text="Ready if its reference is approved.",
        required_access=(), available_access=(), required_reference_purposes=(purpose,))


class Declared:
    def __init__(self, *items):
        self.items = items

    def declared(self):
        return self.items


def view(rig, store, case, approvals, purpose=GT):
    approved = approvals.approved_purposes(case, NOW)
    snapshot = PreflightPlanner(Declared(needs(purpose)), FixedClock(NOW)).plan(case, approved)
    with store.unit_of_work() as uow:
        assets = AssetRepository()
        dataset = assets.get(uow, linked_to(case, CaseAssetRole.DATASET))
        model = assets.get(uow, linked_to(case, CaseAssetRole.MODEL))
        uow.rollback()
    projection = build_preflight(case, dataset, model, snapshot, approved_purposes=approved,
                                 generated_at=NOW)
    return snapshot, projection


def test_a_capability_needing_a_reference_stays_unavailable_after_an_upload(rig, store, approvals):
    case = commit_with_references(rig)
    snapshot, projection = view(rig, store, case, approvals)
    item = snapshot.items[0]
    assert item.capability_state is CapabilityState.UNAVAILABLE
    assert item.reason_code.text == "required_reference_not_approved"
    entry = projection.capability_groups[0].capabilities[0]
    assert entry.reference_state is ReferenceReadiness.MISSING
    [requirement] = projection.reference_requirements
    assert (requirement.purpose, requirement.approved) == (GT, False)


def test_only_an_explicit_core_approval_for_a_linked_reference_makes_it_count(rig, store, approvals):
    case = commit_with_references(rig)
    approve(store, 1, refs_of(case)[0], GT)
    assert approvals.approved_purposes(case, NOW) == frozenset({GT})
    snapshot, projection = view(rig, store, case, approvals)
    assert snapshot.items[0].capability_state is CapabilityState.AVAILABLE
    assert projection.capability_groups[0].capabilities[0].reference_state is ReferenceReadiness.SATISFIED
    assert projection.reference_requirements[0].approved is True


def test_approving_a_different_purpose_does_not_satisfy_the_one_that_is_needed(rig, store, approvals):
    case = commit_with_references(rig)
    approve(store, 1, refs_of(case)[0], MASKS)
    assert approvals.approved_purposes(case, NOW) == frozenset({MASKS})
    snapshot, _ = view(rig, store, case, approvals, GT)
    assert snapshot.items[0].capability_state is CapabilityState.UNAVAILABLE


def test_each_linked_reference_contributes_only_its_own_purpose(rig, store, approvals):
    case = commit_with_references(rig, 2)
    first, second = refs_of(case)
    approve(store, 1, first, GT)
    approve(store, 2, second, MASKS)
    assert approvals.approved_purposes(case, NOW) == frozenset({GT, MASKS})


# ------------------------------------------------------------- approval belongs to a Case's references
def test_an_approval_of_another_cases_reference_does_not_count_here(rig, store, approvals):
    first = commit_with_references(rig, 1)
    second = commit_with_references(rig, 1)
    approve(store, 1, refs_of(second)[0], GT)
    assert approvals.approved_purposes(second, NOW) == frozenset({GT})
    assert approvals.approved_purposes(first, NOW) == frozenset()


def test_a_case_without_references_has_no_approved_purpose(rig, approvals):
    _d, _m, result = commit_minimal(rig)
    assert approvals.approved_purposes(result.case, NOW) == frozenset()


# ------------------------------------------------------------------ approval can be lost
def test_a_revoked_reference_no_longer_counts(rig, store, approvals):
    case = commit_with_references(rig)
    rid = approve(store, 1, refs_of(case)[0], GT)
    assert approvals.approved_purposes(case, NOW) == frozenset({GT})
    revoke(store, rid)
    assert approvals.approved_purposes(case, NOW) == frozenset()


def test_an_expired_reference_does_not_count(rig, store, approvals):
    case = commit_with_references(rig)
    closes = UtcTimestamp.parse("2026-10-31T11:00:00Z")
    approve(store, 1, refs_of(case)[0], GT, start=EARLIER, until=closes)
    assert approvals.approved_purposes(case, NOW) == frozenset()
    assert approvals.approved_purposes(case, UtcTimestamp.parse("2026-10-31T10:00:00Z")) == frozenset({GT})


def test_a_reference_whose_window_has_not_started_does_not_count_yet(rig, store, approvals):
    case = commit_with_references(rig)
    approve(store, 1, refs_of(case)[0], GT, start=LATER)
    assert approvals.approved_purposes(case, NOW) == frozenset()
    assert approvals.approved_purposes(case, LATER) == frozenset({GT})


# ------------------------------------------------------------------ the lookup itself
def test_usable_for_assets_with_no_assets_asks_nothing(store):
    with store.unit_of_work() as uow:
        assert ReferenceRepository().usable_for_assets(uow, [], NOW) == ()
        uow.rollback()


def test_usable_for_assets_refuses_an_oversized_question(store, raw):
    from persistence_support import put_asset
    from trinetra.domain.assets import AssetId

    ids = [AssetId.parse(put_asset(raw, i, "reference")) for i in range(1, 202)]
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceQueryError):
            ReferenceRepository().usable_for_assets(uow, ids, NOW)
        assert ReferenceRepository().usable_for_assets(uow, ids[:200], NOW) == ()
        uow.rollback()


def test_usable_for_assets_ignores_duplicates_and_returns_oldest_window_first(rig, store):
    case = commit_with_references(rig, 2)
    first, second = refs_of(case)
    approve(store, 2, second, GT, start=EARLIER)
    approve(store, 1, first, MASKS, start=UtcTimestamp.parse("2026-10-31T09:30:00Z"))
    with store.unit_of_work() as uow:
        found = ReferenceRepository().usable_for_assets(uow, [first, second, first, second], NOW)
        uow.rollback()
    assert [r.purpose for r in found] == [GT, MASKS]


# ------------------------------------------------------------------ nothing on the intake path can approve
@pytest.mark.parametrize("package", ["intake", "cases", "preflight", "projections"])
def test_no_intake_case_or_view_module_touches_reference_authority(package):
    forbidden = {"trinetra.domain.reference_records", "trinetra.infrastructure.reference_repository"}
    for path in (SRC / "application" / package).glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                pytest.fail(f"{path.name} imports {node.module}")
            if isinstance(node, ast.Import) and any(a.name in forbidden for a in node.names):
                pytest.fail(f"{path.name} imports reference authority")


def test_only_the_reference_repository_and_domain_ever_create_authority_records():
    creators = []
    for path in (SRC).rglob("*.py"):
        text = path.read_text()
        if "ReferenceAssuranceRecord(" in text and "class ReferenceAssuranceRecord" not in text:
            creators.append(path.relative_to(SRC).as_posix())
    assert creators == ["infrastructure/reference_repository.py"]


def test_asking_about_the_same_asset_many_times_counts_it_once(rig, store):
    case = commit_with_references(rig, 1)
    only = refs_of(case)[0]
    approve(store, 1, only, GT)
    with store.unit_of_work() as uow:
        found = ReferenceRepository().usable_for_assets(uow, [only] * 500, NOW)
        uow.rollback()
    assert [r.purpose for r in found] == [GT]
