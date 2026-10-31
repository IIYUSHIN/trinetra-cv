"""IMP-0095 tests: committing a ready intake stores one PREFLIGHT Case and marks the intake COMMITTED."""

from __future__ import annotations

import io
import threading

import pytest

from intake_support import T1, FakeAssets, FrozenClock, MemoryCaseStore, MemoryQuarantine, SequentialIds
from trinetra.application.errors import (
    ApplicationConflictError,
    CaseAlreadyStoredError,
    IntakeNotReadyError,
    IntakeStateConflictError,
    TransferRejectedError,
)
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.commit_case import CommitIntakeService, link_entries
from trinetra.application.intake.enums import IntakeState, PackageSlot
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.decisions import CommandId


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


class World:
    def __init__(self, label=None):
        self.clock, self.ids, self.registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
        self.quarantine, self.assets, self.cases = MemoryQuarantine(), FakeAssets(), MemoryCaseStore()
        self.receive = ReceiveTransferService(self.clock, self.ids, self.registry, self.quarantine)
        admission = TransferAdmissionService(self.clock, self.registry, self.quarantine, self.assets,
                                             self.assets)
        self.step = CommitAdmissionStep(self.registry, admission)
        self.service = CommitIntakeService(self.step, self.registry, self.cases, self.clock)
        self.intake = CreateIntakeService(self.clock, self.ids, self.registry).create(
            CreateIntakeCommand(command_id=cid(250), case_label=label)
        ).intake_id
        self._n = 0

    def upload(self, slot, fmt=None, media="application/zip"):
        self._n += 1
        return self.receive.receive(ReceiveTransferCommand(
            intake_id=self.intake, command_id=cid(self._n), slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None, logical_identifier=None,
            filename=OriginalFilename(value=f"f{self._n}.bin"), media_type=media, stream=io.BytesIO(b"data"),
        ))  # fmt: skip

    def minimal(self):
        return self.upload(PackageSlot.DATASET, "coco"), self.upload(PackageSlot.MODEL, "onnx")

    def session(self):
        return self.registry.require(self.intake)


def shape(case):
    return [(lk.role, lk.ordinal, lk.asset_id) for lk in case.asset_links]


# ------------------------------------------------------------------ the happy path
def test_one_dataset_and_one_model_become_a_preflight_case_at_revision_one():
    w = World()
    data, model = w.minimal()
    result = w.service.commit(w.intake)
    case = result.case
    assert case.case_id == w.session().reserved_case_id
    assert case.lifecycle_state is CaseLifecycleState.PREFLIGHT
    assert case.case_state_revision.value == 1
    assert shape(case) == [
        (CaseAssetRole.DATASET, 0, data.reserved_asset_id),
        (CaseAssetRole.MODEL, 0, model.reserved_asset_id),
    ]
    assert result.case_created is True
    assert w.cases.cases == {case.case_id: case}


def test_the_intake_is_committed_with_every_transfer_admitted():
    w = World()
    w.minimal()
    result = w.service.commit(w.intake)
    assert result.session.intake_state is IntakeState.COMMITTED
    assert w.session() == result.session


def test_the_case_label_comes_from_the_intake():
    w = World(label="Night patrol v3")
    w.minimal()
    assert w.service.commit(w.intake).case.case_label == "Night patrol v3"


def test_an_unlabelled_intake_gives_an_unlabelled_case():
    w = World()
    w.minimal()
    assert w.service.commit(w.intake).case.case_label is None


def test_the_case_is_stamped_with_the_commit_time_and_links_share_it():
    w = World()
    w.minimal()
    w.clock.set(T1)
    case = w.service.commit(w.intake).case
    assert case.created_at == case.updated_at == T1
    assert {lk.linked_at for lk in case.asset_links} == {T1}


def test_the_store_is_told_which_intake_the_case_came_from():
    w = World()
    w.minimal()
    case = w.service.commit(w.intake).case
    assert w.cases.created == [(case, w.intake)]


def test_optional_assets_link_in_plan_order_with_per_role_ordinals():
    w = World()
    ref1 = w.upload(PackageSlot.REFERENCE)
    meta = w.upload(PackageSlot.CONTRIBUTOR_METADATA, media="text/plain")
    model = w.upload(PackageSlot.MODEL, "onnx")
    ref2 = w.upload(PackageSlot.REFERENCE)
    data = w.upload(PackageSlot.DATASET, "coco")
    profile = w.upload(PackageSlot.ASSESSMENT_PROFILE, media="application/json")
    record = w.upload(PackageSlot.INFERENCE_RECORDS, media="application/json")
    case = w.service.commit(w.intake).case
    got = shape(case)
    assert sorted(got, key=lambda g: (list(CaseAssetRole).index(g[0]), g[1])) == got
    assert got == [
        (CaseAssetRole.DATASET, 0, data.reserved_asset_id),
        (CaseAssetRole.MODEL, 0, model.reserved_asset_id),
        (CaseAssetRole.CONTRIBUTOR_METADATA, 0, meta.reserved_asset_id),
        (CaseAssetRole.REFERENCE, 0, ref1.reserved_asset_id),
        (CaseAssetRole.REFERENCE, 1, ref2.reserved_asset_id),
        (CaseAssetRole.INFERENCE_RECORD, 0, record.reserved_asset_id),
        (CaseAssetRole.ASSESSMENT_PROFILE, 0, profile.reserved_asset_id),
    ]


def test_link_entries_follow_the_plan_order_of_the_admitted_session():
    w = World()
    data, model = w.minimal()
    admitted = w.step.admit_all(w.intake)
    assert link_entries(admitted.session) == (
        (CaseAssetRole.DATASET, data.reserved_asset_id), (CaseAssetRole.MODEL, model.reserved_asset_id))


def test_link_entries_refuse_an_unadmitted_counted_transfer():
    w = World()
    w.minimal()
    with pytest.raises(ApplicationConflictError):
        link_entries(w.session())


# ------------------------------------------------------------------ not ready / bad state
def test_an_intake_missing_its_model_does_not_commit_and_stores_nothing():
    w = World()
    w.upload(PackageSlot.DATASET, "coco")
    with pytest.raises(IntakeNotReadyError):
        w.service.commit(w.intake)
    assert w.cases.cases == {}
    assert w.session().intake_state is IntakeState.OPEN


def test_an_empty_intake_does_not_commit():
    w = World()
    with pytest.raises(IntakeNotReadyError):
        w.service.commit(w.intake)
    assert w.cases.cases == {}


def test_an_abandoned_intake_does_not_commit():
    w = World()
    w.minimal()
    w.registry.replace(w.session().abandoned(w.clock.now()))
    with pytest.raises(IntakeStateConflictError):
        w.service.commit(w.intake)
    assert w.cases.cases == {}


def test_a_rejected_transfer_aborts_the_commit_without_a_case_and_leaves_the_intake_open():
    w = World()
    w.minimal()
    w.assets.error = TransferRejectedError()
    with pytest.raises(TransferRejectedError):
        w.service.commit(w.intake)
    assert w.cases.cases == {}
    assert w.session().intake_state is IntakeState.OPEN


# ------------------------------------------------------------------ idempotent replay
def test_committing_twice_returns_the_same_case_and_stores_it_once():
    w = World()
    w.minimal()
    first = w.service.commit(w.intake)
    again = w.service.commit(w.intake)
    assert again.case == first.case
    assert again.case_created is False
    assert again.session == first.session
    assert len(w.cases.created) == 1


def test_a_replay_admits_nothing_again():
    w = World()
    w.minimal()
    w.service.commit(w.intake)
    calls = len(w.assets.calls)
    w.service.commit(w.intake)
    assert len(w.assets.calls) == calls


def test_a_committed_intake_whose_case_is_missing_is_a_conflict_not_a_new_case():
    w = World()
    w.minimal()
    w.service.commit(w.intake)
    w.cases.cases.clear()
    with pytest.raises(ApplicationConflictError):
        w.service.commit(w.intake)
    assert w.cases.cases == {}


def test_two_threads_committing_together_store_exactly_one_case():
    w = World()
    w.minimal()
    results, errors = [], []

    def run():
        try:
            results.append(w.service.commit(w.intake))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len({r.case for r in results}) == 1
    assert sum(r.case_created for r in results) == 1
    assert len(w.cases.created) == 1


# ---------------------------------------------------------------- partial commit and recovery
def test_a_store_failure_keeps_assets_admitted_and_the_intake_open_and_a_retry_completes():
    w = World()
    data, _model = w.minimal()
    w.cases.fail_next = RuntimeError("disk gone")
    with pytest.raises(RuntimeError):
        w.service.commit(w.intake)
    assert w.session().intake_state is IntakeState.OPEN
    admitted_calls = len(w.assets.calls)
    assert admitted_calls == 2 and w.cases.cases == {}
    result = w.service.commit(w.intake)
    assert len(w.assets.calls) == admitted_calls  # nothing admitted twice
    assert result.case_created is True
    assert result.case.case_id == w.session().reserved_case_id
    assert shape(result.case)[0][2] == data.reserved_asset_id


def test_a_crash_after_the_case_was_stored_is_recovered_without_a_second_case():
    w = World()
    w.minimal()
    # the process "dies" right after the case is stored: registry never learns the intake is committed
    w.cases.after_create = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        w.service.commit(w.intake)
    assert w.session().intake_state is IntakeState.OPEN
    assert len(w.cases.cases) == 1
    stored = next(iter(w.cases.cases.values()))
    w.cases.after_create = None
    result = w.service.commit(w.intake)
    assert result.case == stored
    assert result.case_created is False
    assert result.session.intake_state is IntakeState.COMMITTED
    assert len(w.cases.created) == 1


def test_a_stored_case_with_different_links_is_never_adopted_or_overwritten():
    w = World()
    w.minimal()
    admitted = w.step.admit_all(w.intake)
    foreign = w.service  # the stored case below has the reserved id but another dataset
    from projection_support import OTHER_ID
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case_assets import link_assets

    entries = [(CaseAssetRole.DATASET, OTHER_ID), (CaseAssetRole.MODEL, admitted.asset_ids[1])]
    imposter = AssuranceCase.create(admitted.case_id, link_assets(entries, T1), T1)
    w.cases.cases[admitted.case_id] = imposter
    with pytest.raises(ApplicationConflictError):
        foreign.commit(w.intake)
    assert w.cases.cases[admitted.case_id] == imposter
    assert w.session().intake_state is IntakeState.OPEN


def test_a_stored_case_with_a_different_label_is_not_adopted():
    w = World(label="Mine")
    w.minimal()
    admitted = w.step.admit_all(w.intake)
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case_assets import link_assets

    other = AssuranceCase.create(
        admitted.case_id,
        link_assets(list(link_entries(admitted.session)), T1), T1, label="Someone elses")
    w.cases.cases[admitted.case_id] = other
    with pytest.raises(ApplicationConflictError):
        w.service.commit(w.intake)


def test_a_stored_case_with_the_same_links_but_other_timestamps_is_the_same_case():
    w = World()
    w.minimal()
    admitted = w.step.admit_all(w.intake)
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case_assets import link_assets

    earlier = AssuranceCase.create(
        admitted.case_id, link_assets(list(link_entries(admitted.session)), T1), T1)
    w.cases.cases[admitted.case_id] = earlier
    result = w.service.commit(w.intake)
    assert result.case == earlier and result.case_created is False


def test_a_case_that_appears_during_storing_is_reported_as_a_conflict():
    w = World()
    w.minimal()
    w.cases.fail_next = CaseAlreadyStoredError()
    with pytest.raises(ApplicationConflictError) as error:
        w.service.commit(w.intake)
    assert "appeared" in error.value.message
    assert w.session().intake_state is IntakeState.OPEN


def test_a_clock_that_went_backwards_keeps_the_intake_open_and_a_later_retry_completes():
    w = World()
    w.minimal()
    w.cases.after_create = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        w.service.commit(w.intake)
    w.cases.after_create = None
    w.clock.set(type(w.session().updated_at).parse("2026-10-31T07:00:00Z"))
    with pytest.raises(IntakeStateConflictError):
        w.service.commit(w.intake)
    assert w.session().intake_state is IntakeState.OPEN
    assert len(w.cases.cases) == 1
    w.clock.set(T1)
    result = w.service.commit(w.intake)
    assert result.case_created is False and result.session.intake_state is IntakeState.COMMITTED


def _stored_variant(w, entries_for):
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case_assets import link_assets

    admitted = w.step.admit_all(w.intake)
    w.cases.cases[admitted.case_id] = AssuranceCase.create(
        admitted.case_id, link_assets(entries_for(admitted), T1), T1)
    return admitted


def test_a_stored_case_with_the_roles_swapped_is_not_adopted():
    w = World()
    w.minimal()
    _stored_variant(w, lambda a: [
        (CaseAssetRole.DATASET, a.asset_ids[1]), (CaseAssetRole.MODEL, a.asset_ids[0])])
    with pytest.raises(ApplicationConflictError):
        w.service.commit(w.intake)


def test_a_stored_case_with_the_references_in_another_order_is_not_adopted():
    w = World()
    w.upload(PackageSlot.DATASET, "coco")
    w.upload(PackageSlot.MODEL, "onnx")
    w.upload(PackageSlot.REFERENCE)
    w.upload(PackageSlot.REFERENCE)
    _stored_variant(w, lambda a: [
        (CaseAssetRole.DATASET, a.asset_ids[0]), (CaseAssetRole.MODEL, a.asset_ids[1]),
        (CaseAssetRole.REFERENCE, a.asset_ids[3]), (CaseAssetRole.REFERENCE, a.asset_ids[2])])
    with pytest.raises(ApplicationConflictError):
        w.service.commit(w.intake)


def test_a_stored_case_where_only_one_assets_role_differs_is_not_adopted():
    w = World()
    w.upload(PackageSlot.DATASET, "coco")
    w.upload(PackageSlot.MODEL, "onnx")
    w.upload(PackageSlot.REFERENCE)
    _stored_variant(w, lambda a: [
        (CaseAssetRole.DATASET, a.asset_ids[0]), (CaseAssetRole.MODEL, a.asset_ids[1]),
        (CaseAssetRole.CONTRIBUTOR_METADATA, a.asset_ids[2])])
    with pytest.raises(ApplicationConflictError):
        w.service.commit(w.intake)
