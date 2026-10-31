"""IMP-0078 tests: what a commit needs, in what order, and the shape of its result."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intake_support import SequentialIds, ready_session, session, transfer
from trinetra.application.errors import IntakeNotReadyError, IntakeStateConflictError
from trinetra.application.intake.commands import CommitIntakeCommand, CommitIntakeResult
from trinetra.application.intake.commit_plan import plan_commit
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.domain.case import CaseStateRevision
from trinetra.domain.decisions import CommandId


def cid(n=1):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


def test_a_ready_intake_plans_its_transfers_in_slot_then_arrival_order():
    ref1 = transfer(PackageSlot.REFERENCE, None)
    model = transfer(PackageSlot.MODEL, "onnx")
    ref2 = transfer(PackageSlot.REFERENCE, None)
    meta = transfer(PackageSlot.CONTRIBUTOR_METADATA, "json")
    data = transfer(PackageSlot.DATASET, "coco")
    s = session([ref1, model, ref2, meta, data])  # arrival order differs from slot order
    plan = plan_commit(s)
    assert plan.transfer_ids == (data.transfer_id, model.transfer_id, meta.transfer_id,
                                 ref1.transfer_id, ref2.transfer_id)  # fmt: skip
    assert plan.intake_id == s.intake_id
    assert plan.reserved_case_id == s.reserved_case_id


def test_the_plan_is_the_same_however_often_it_is_asked_and_changes_nothing():
    s = ready_session()
    before = s.model_dump_json()
    assert plan_commit(s) == plan_commit(s)
    assert s.model_dump_json() == before


def test_failed_transfers_are_not_part_of_the_plan():
    good_data = transfer(PackageSlot.DATASET, "coco")
    bad_data = transfer(PackageSlot.DATASET, "coco", state=TransferState.FAILED)
    model = transfer(PackageSlot.MODEL, "onnx")
    plan = plan_commit(session([bad_data, good_data, model]))
    assert plan.transfer_ids == (good_data.transfer_id, model.transfer_id)


def test_already_admitted_transfers_stay_in_the_plan():
    data = transfer(PackageSlot.DATASET, "coco", state=TransferState.ADMITTED)
    model = transfer(PackageSlot.MODEL, "onnx")
    assert plan_commit(session([data, model])).transfer_ids == (data.transfer_id, model.transfer_id)


@pytest.mark.parametrize("missing", [PackageSlot.DATASET, PackageSlot.MODEL])
def test_a_missing_required_slot_names_what_is_unmet(missing):
    parts = {PackageSlot.DATASET: transfer(PackageSlot.DATASET, "coco"),
             PackageSlot.MODEL: transfer(PackageSlot.MODEL, "onnx")}  # fmt: skip
    del parts[missing]
    with pytest.raises(IntakeNotReadyError) as exc:
        plan_commit(session(list(parts.values())))
    (unmet,) = exc.value.details["unmet"]
    assert dict(unmet) == {"slot": missing.value, "present": 0, "minimum": 1, "maximum": 1}


def test_an_empty_intake_reports_both_required_slots_in_slot_order():
    with pytest.raises(IntakeNotReadyError) as exc:
        plan_commit(session())
    assert [u["slot"] for u in exc.value.details["unmet"]] == ["dataset", "model"]


def test_a_failed_only_slot_counts_as_missing():
    only_failed = session([transfer(PackageSlot.DATASET, "coco", state=TransferState.FAILED),
                           transfer(PackageSlot.MODEL, "onnx")])  # fmt: skip
    with pytest.raises(IntakeNotReadyError):
        plan_commit(only_failed)


@pytest.mark.parametrize("state", [IntakeState.COMMITTED, IntakeState.ABANDONED])
def test_a_closed_intake_cannot_be_planned(state):
    base = session([transfer(PackageSlot.DATASET, "coco", state=TransferState.ADMITTED),
                    transfer(PackageSlot.MODEL, "onnx", state=TransferState.ADMITTED)])  # fmt: skip
    close = base.committed if state is IntakeState.COMMITTED else base.abandoned
    closed = close(base.updated_at)
    with pytest.raises(IntakeStateConflictError) as exc:
        plan_commit(closed)
    assert not isinstance(exc.value, IntakeNotReadyError)


def test_not_ready_is_a_kind_of_state_conflict_with_its_own_stable_code():
    assert issubclass(IntakeNotReadyError, IntakeStateConflictError)
    assert IntakeNotReadyError.code == "INTAKE_NOT_READY" != IntakeStateConflictError.code


def test_the_commit_command_is_closed_and_needs_both_ids():
    ids = SequentialIds()
    command = CommitIntakeCommand(intake_id=ids.new_intake_id(), command_id=cid())
    assert command.intake_id and command.command_id
    with pytest.raises(ValidationError):
        CommitIntakeCommand(intake_id=ids.new_intake_id())
    with pytest.raises(ValidationError):
        CommitIntakeCommand(intake_id=ids.new_intake_id(), command_id=cid(), extra=1)


def test_the_result_needs_at_least_a_dataset_and_a_model_and_no_duplicates():
    ids = SequentialIds()
    case = ids.new_case_id()
    a, b = ids.new_asset_id(), ids.new_asset_id()
    revision = CaseStateRevision(1)
    ok = CommitIntakeResult(case_id=case, case_state_revision=revision, admitted_asset_ids=(a, b))
    assert ok.admitted_asset_ids == (a, b)
    with pytest.raises(ValidationError):
        CommitIntakeResult(case_id=case, case_state_revision=revision, admitted_asset_ids=(a,))
    with pytest.raises(ValidationError):
        CommitIntakeResult(case_id=case, case_state_revision=revision, admitted_asset_ids=(a, a))
    with pytest.raises(ValidationError):
        CommitIntakeResult(case_id=case, case_state_revision=revision, admitted_asset_ids=(a, b), extra=1)
