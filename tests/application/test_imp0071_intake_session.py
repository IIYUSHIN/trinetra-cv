"""IMP-0071 tests: an intake session is coordination with exact cardinality and terminal states."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from intake_support import T0, T1, T2, entropy, ready_session, session, transfer
from trinetra.application.errors import IntakeStateConflictError
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.records import IntakeSession, TransferErrorCode
from trinetra.application.intake.slots import MAX_TRANSFERS_PER_INTAKE
from trinetra.domain.assets import AssetId

DATASET, MODEL = PackageSlot.DATASET, PackageSlot.MODEL


def test_a_new_session_is_open_empty_and_not_ready():
    s = session()
    assert s.intake_state is IntakeState.OPEN
    assert s.transfers == ()
    r = s.readiness()
    assert r.ready is False
    assert {c.slot for c in r.unmet} == {DATASET, MODEL}


def test_exactly_one_dataset_and_one_model_make_it_ready():
    s = session().with_transfer(transfer(DATASET), T1)
    assert s.readiness().ready is False
    s = s.with_transfer(transfer(MODEL, "pytorch"), T1)
    assert s.readiness().ready is True
    assert s.readiness().unmet == ()


def test_optional_slots_are_optional_and_still_counted():
    s = ready_session()
    for slot, fmt in ((PackageSlot.CONTRIBUTOR_METADATA, "json"), (PackageSlot.ASSESSMENT_PROFILE, "json")):
        s = s.with_transfer(transfer(slot, fmt), T1)
    for _ in range(3):
        s = s.with_transfer(transfer(PackageSlot.REFERENCE, None), T1)
        s = s.with_transfer(transfer(PackageSlot.INFERENCE_RECORDS, None), T1)
    assert s.readiness().ready is True
    assert len(s.counted(PackageSlot.REFERENCE)) == 3


SINGLE_SLOTS = [DATASET, MODEL, PackageSlot.CONTRIBUTOR_METADATA, PackageSlot.ASSESSMENT_PROFILE]


@pytest.mark.parametrize("slot", SINGLE_SLOTS)
def test_single_slots_reject_a_second_counted_upload(slot):
    fmt = {DATASET: "coco", MODEL: "onnx"}.get(slot, "json")
    s = session().with_transfer(transfer(slot, fmt), T1)
    with pytest.raises(IntakeStateConflictError, match="full"):
        s.with_transfer(transfer(slot, fmt), T1)


def test_a_failed_transfer_frees_its_slot_for_a_retry():
    s = session().with_transfer(transfer(DATASET), T1)
    failed = s.transfers[0].failed(TransferErrorCode("rejected"))
    s = s.with_transfer_updated(failed, T1)
    assert s.readiness().counts[0].present == 0
    s = s.with_transfer(transfer(DATASET), T2)
    assert len(s.counted(DATASET)) == 1
    assert len(s.transfers) == 2  # the failure is kept for inspection


def test_a_new_transfer_must_arrive_in_the_received_state():
    with pytest.raises(IntakeStateConflictError, match="RECEIVED"):
        session().with_transfer(transfer(state=TransferState.ADMITTED), T1)


def test_admission_updates_replace_the_transfer_in_place_and_keep_order():
    s = ready_session()
    first, second = s.transfers
    s2 = s.with_transfer_updated(second.admitted(), T1)
    assert [t.transfer_id for t in s2.transfers] == [first.transfer_id, second.transfer_id]
    assert s2.transfers[1].transfer_state is TransferState.ADMITTED
    assert s2.transfers[0] == first


def test_only_received_to_admitted_or_failed_is_a_legal_update():
    s = ready_session()
    t = s.transfers[0]
    with pytest.raises(IntakeStateConflictError):
        s.with_transfer_updated(t, T1)  # RECEIVED -> RECEIVED
    done = s.with_transfer_updated(t.admitted(), T1)
    with pytest.raises(IntakeStateConflictError):
        done.with_transfer_updated(t.admitted(), T2)  # already ADMITTED
    with pytest.raises(IntakeStateConflictError, match="unknown"):
        s.with_transfer_updated(transfer().admitted(), T1)


def test_an_update_may_change_nothing_but_state_asset_and_error():
    s = ready_session()
    t = s.transfers[0]
    sneaky = t.admitted().evolve(received_byte_size=t.received_byte_size + 1)
    with pytest.raises(IntakeStateConflictError, match="only state"):
        s.with_transfer_updated(sneaky, T1)


def test_commit_needs_readiness_and_every_counted_transfer_admitted():
    s = ready_session()
    with pytest.raises(ValidationError):
        s.committed(T1)  # RECEIVED transfers are not admitted yet
    for t in s.transfers:
        s = s.with_transfer_updated(t.admitted(), T1)
    done = s.committed(T2)
    assert done.intake_state is IntakeState.COMMITTED
    assert done.updated_at == T2


def test_an_unready_session_cannot_be_committed_even_if_everything_is_admitted():
    s = session().with_transfer(transfer(DATASET), T1)
    s = s.with_transfer_updated(s.transfers[0].admitted(), T1)
    with pytest.raises(ValidationError):
        s.committed(T2)


def test_a_failed_transfer_does_not_block_commit():
    s = ready_session()
    extra = transfer(PackageSlot.REFERENCE, None)
    s = s.with_transfer(extra, T1).with_transfer_updated(extra.failed(TransferErrorCode("x")), T1)
    for t in s.transfers[:2]:
        s = s.with_transfer_updated(t.admitted(), T1)
    assert s.committed(T2).intake_state is IntakeState.COMMITTED


@pytest.mark.parametrize("terminal", ["committed", "abandoned"])
def test_terminal_sessions_accept_nothing_further(terminal):
    s = ready_session()
    if terminal == "committed":
        for t in s.transfers:
            s = s.with_transfer_updated(t.admitted(), T1)
        s = s.committed(T1)
    else:
        s = s.abandoned(T1)
    with pytest.raises(IntakeStateConflictError):
        s.with_transfer(transfer(PackageSlot.REFERENCE, None), T2)
    with pytest.raises(IntakeStateConflictError):
        s.abandoned(T2)
    with pytest.raises(IntakeStateConflictError):
        s.committed(T2)
    with pytest.raises(IntakeStateConflictError):
        s.with_transfer_updated(s.transfers[0].admitted(), T2)


def test_time_cannot_move_backwards_and_stamps_follow_the_operation():
    s = session()
    s = s.with_transfer(transfer(DATASET), T2)
    assert s.updated_at == T2
    assert s.created_at == T0
    with pytest.raises(IntakeStateConflictError, match="backwards"):
        s.with_transfer(transfer(MODEL, "onnx"), T1)


def test_the_session_rejects_inconsistent_construction():
    t = transfer()
    base = ready_session()
    with pytest.raises(ValidationError, match="unique"):
        base.evolve(transfers=(t, t))
    clone = t.evolve(transfer_id=transfer().transfer_id)  # same reserved asset id, different transfer id
    with pytest.raises(ValidationError, match="reserved asset"):
        base.evolve(transfers=(t, clone))
    with pytest.raises(ValidationError, match="precede"):
        base.evolve(updated_at=T0, created_at=T1)
    two_datasets = (transfer(DATASET), transfer(DATASET))
    with pytest.raises(ValidationError, match="more counted"):
        base.evolve(transfers=two_datasets)


def test_a_committed_session_cannot_be_constructed_unready():
    with pytest.raises(ValidationError, match="COMMITTED"):
        session(state=IntakeState.COMMITTED)


def test_the_transfer_count_is_bounded():
    s = session()
    for _ in range(MAX_TRANSFERS_PER_INTAKE - 1):
        s = s.with_transfer(transfer(PackageSlot.REFERENCE, None), T1)
    s = s.with_transfer(transfer(PackageSlot.REFERENCE, None), T1)
    assert len(s.transfers) == MAX_TRANSFERS_PER_INTAKE
    with pytest.raises(IntakeStateConflictError, match="limit"):
        s.with_transfer(transfer(PackageSlot.REFERENCE, None), T1)


def test_queries_find_transfers_and_count_per_slot():
    s = ready_session()
    t = s.transfers[1]
    assert s.get_transfer(t.transfer_id) == t
    assert s.get_transfer(transfer().transfer_id) is None
    assert s.counted(MODEL) == (t,)
    assert s.counted(PackageSlot.REFERENCE) == ()


def test_case_label_is_optional_logical_text_and_the_case_id_is_reserved_not_created():
    assert session(label="Night shift audit").case_label == "Night shift audit"
    with pytest.raises(ValidationError):
        session(label="bad\nlabel")
    s = ready_session()
    assert str(s.reserved_case_id).startswith("case_")


def test_the_session_is_closed_immutable_and_names_its_dimension():
    s = session()
    with pytest.raises(ValidationError):
        s.intake_state = IntakeState.COMMITTED
    assert "intake_state" in IntakeSession.model_fields
    assert "state" not in IntakeSession.model_fields
    assert AssetId.from_entropy(entropy) != s.reserved_case_id  # distinct id types never compare equal


def test_two_transfers_may_not_share_a_transfer_id_even_with_different_assets():
    t = transfer()
    twin = t.evolve(
        reserved_asset_id=AssetId.from_entropy(entropy), quarantine_handle=transfer().quarantine_handle
    )
    with pytest.raises(ValidationError, match="transfer ids"):
        ready_session().evolve(transfers=(t, twin))


def test_a_slot_count_is_satisfied_only_inside_its_range():
    from trinetra.application.intake.records import SlotCount

    low, ok, high = (SlotCount(slot=DATASET, present=p, minimum=1, maximum=2) for p in (0, 1, 3))
    assert (low.satisfied, ok.satisfied, high.satisfied) == (False, True, False)
    assert SlotCount(slot=DATASET, present=2, minimum=1, maximum=2).satisfied is True
