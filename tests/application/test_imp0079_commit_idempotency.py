"""IMP-0079 tests: a commit's admission step is repeatable and never admits an asset twice."""

from __future__ import annotations

import io
import threading

import pytest

from intake_support import FakeAssets, FrozenClock, MemoryQuarantine, SequentialIds
from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeNotReadyError,
    IntakeStateConflictError,
    TransferRejectedError,
)
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.decisions import CommandId


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


class World:
    def __init__(self):
        self.clock, self.ids, self.registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
        self.quarantine, self.assets = MemoryQuarantine(), FakeAssets()
        self.receive = ReceiveTransferService(self.clock, self.ids, self.registry, self.quarantine)
        self.admission = TransferAdmissionService(
            self.clock, self.registry, self.quarantine, self.assets, self.assets
        )
        self.step = CommitAdmissionStep(self.registry, self.admission)
        self.intake = CreateIntakeService(self.clock, self.ids, self.registry).create(
            CreateIntakeCommand(command_id=cid(250))
        ).intake_id
        self._n = 0

    def upload(self, slot, fmt, media="application/zip"):
        self._n += 1
        return self.receive.receive(ReceiveTransferCommand(
            intake_id=self.intake, command_id=cid(self._n), slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None, logical_identifier=None,
            filename=OriginalFilename(value=f"f{self._n}.bin"), media_type=media, stream=io.BytesIO(b"data"),
        ))  # fmt: skip

    def ready(self):
        reference = self.upload(PackageSlot.REFERENCE, None, None)
        model = self.upload(PackageSlot.MODEL, "onnx")
        data = self.upload(PackageSlot.DATASET, "coco")
        return data, model, reference

    def session(self):
        return self.registry.require(self.intake)


def test_everything_planned_is_admitted_in_plan_order_with_the_reserved_case():
    w = World()
    data, model, ref = w.ready()
    result = w.step.admit_all(w.intake)
    assert result.asset_ids == (data.reserved_asset_id, model.reserved_asset_id, ref.reserved_asset_id)
    assert [c["asset_id"] for c in w.assets.calls] == list(result.asset_ids)  # the port saw the same order
    assert result.case_id == w.session().reserved_case_id
    assert result.session.intake_state is IntakeState.OPEN  # committing the Case is a later step
    assert all(t.transfer_state is TransferState.ADMITTED for t in w.session().transfers)


def test_repeating_the_step_admits_nothing_again_and_returns_the_same_answer():
    w = World()
    w.ready()
    first = w.step.admit_all(w.intake)
    calls = len(w.assets.calls)
    second = w.step.admit_all(w.intake)
    assert len(w.assets.calls) == calls == 3
    assert second.asset_ids == first.asset_ids and second.case_id == first.case_id


def test_a_failure_part_way_keeps_earlier_admissions_and_a_retry_only_continues():
    w = World()
    data, model, ref = w.ready()
    w.assets.on_admit = None
    original_admit = w.assets.admit
    state = {"n": 0}

    def flaky(**kw):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("database is down")
        return original_admit(**kw)

    w.assets.admit = flaky
    with pytest.raises(RuntimeError):
        w.step.admit_all(w.intake)
    states = {t.transfer_id: t.transfer_state for t in w.session().transfers}
    assert states[data.transfer_id] is TransferState.ADMITTED
    assert states[model.transfer_id] is TransferState.RECEIVED
    assert states[ref.transfer_id] is TransferState.RECEIVED
    assert w.session().intake_state is IntakeState.OPEN
    w.assets.admit = original_admit
    w.assets.calls.clear()
    result = w.step.admit_all(w.intake)
    assert [c["asset_id"] for c in w.assets.calls] == [model.reserved_asset_id, ref.reserved_asset_id]
    assert len(result.asset_ids) == 3 and result.asset_ids[0] == data.reserved_asset_id


def test_the_reserved_case_id_is_the_same_on_every_attempt():
    w = World()
    w.ready()
    reserved = w.session().reserved_case_id
    w.assets.error = RuntimeError("down")
    with pytest.raises(RuntimeError):
        w.step.admit_all(w.intake)
    w.assets.error = None
    assert w.step.admit_all(w.intake).case_id == reserved == w.session().reserved_case_id


def test_a_rejected_upload_aborts_the_commit_and_needs_a_replacement_before_retry():
    w = World()
    data, model, _ref = w.ready()
    original_admit = w.assets.admit

    def reject_model(**kw):
        if kw["asset_id"] == model.reserved_asset_id:
            raise TransferRejectedError("no", details={"reason": "asset_profile_rejected"})
        return original_admit(**kw)

    w.assets.admit = reject_model
    with pytest.raises(TransferRejectedError):
        w.step.admit_all(w.intake)
    by_id = {t.transfer_id: t for t in w.session().transfers}
    assert by_id[data.transfer_id].transfer_state is TransferState.ADMITTED  # kept, not rolled back
    assert by_id[model.transfer_id].transfer_state is TransferState.FAILED
    assert w.session().intake_state is IntakeState.OPEN
    with pytest.raises(IntakeNotReadyError) as exc:
        w.step.admit_all(w.intake)
    assert [dict(u)["slot"] for u in exc.value.details["unmet"]] == ["model"]
    w.assets.admit = original_admit
    w.assets.calls.clear()
    replacement = w.upload(PackageSlot.MODEL, "onnx")
    result = w.step.admit_all(w.intake)
    assert replacement.reserved_asset_id in result.asset_ids
    assert data.reserved_asset_id in result.asset_ids  # never admitted twice
    assert [c["asset_id"] for c in w.assets.calls].count(data.reserved_asset_id) == 0


def test_an_intake_that_is_not_ready_or_not_open_admits_nothing():
    w = World()
    w.upload(PackageSlot.DATASET, "coco")
    with pytest.raises(IntakeNotReadyError):
        w.step.admit_all(w.intake)
    assert w.assets.calls == []
    w.registry.replace(w.session().abandoned(w.clock.now()))
    with pytest.raises(IntakeStateConflictError):
        w.step.admit_all(w.intake)
    assert w.assets.calls == []


def test_a_reservation_alone_is_not_an_asset_but_an_existing_record_is_adopted():
    w = World()
    data, _, _ = w.ready()
    assert not w.assets.exists(data.reserved_asset_id)  # reserved, yet no record
    w.assets.admit(handle=data.quarantine_handle, asset_id=data.reserved_asset_id,
                   kind=__import__("trinetra.domain.assets", fromlist=["AssetKind"]).AssetKind.DATASET,
                   media_type="application/zip", supplied_name="x")  # an earlier attempt  # fmt: skip
    w.assets.calls.clear()
    result = w.step.admit_all(w.intake)
    assert data.reserved_asset_id in result.asset_ids
    assert data.reserved_asset_id not in [c["asset_id"] for c in w.assets.calls]


def test_an_admitted_asset_whose_record_vanished_blocks_the_commit():
    w = World()
    data, _, _ = w.ready()
    w.step.admit_all(w.intake)
    del w.assets.records[data.reserved_asset_id]
    with pytest.raises(ApplicationConflictError):
        w.step.admit_all(w.intake)


def test_two_concurrent_commits_admit_each_asset_once():
    w = World()
    w.ready()
    barrier = threading.Barrier(2, timeout=5)
    results, errors = [], []

    def run():
        barrier.wait()
        try:
            results.append(w.step.admit_all(w.intake))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == [] and len(results) == 2
    assert results[0].asset_ids == results[1].asset_ids
    assert len(w.assets.calls) == 3


def test_a_failed_transfer_is_never_part_of_the_result():
    w = World()
    data, model, ref = w.ready()
    w.assets.error = TransferRejectedError("no", details={"reason": "x"})
    with pytest.raises(TransferRejectedError):
        w.admission.admit(w.intake, ref.transfer_id)
    w.assets.error = None
    result = w.step.admit_all(w.intake)
    assert ref.reserved_asset_id not in result.asset_ids
    assert result.asset_ids == (data.reserved_asset_id, model.reserved_asset_id)


def test_the_returned_session_is_the_latest_stored_version():
    w = World()
    w.ready()
    result = w.step.admit_all(w.intake)
    assert result.session == w.session()
    assert all(t.transfer_state is TransferState.ADMITTED for t in result.session.transfers)


def test_a_port_that_returns_without_admitting_cannot_yield_a_result():
    w = World()
    w.ready()

    class Lazy:
        def admit_locked(self, session, transfer):
            return None

    step = CommitAdmissionStep(w.registry, Lazy())
    with pytest.raises(ApplicationConflictError):
        step.admit_all(w.intake)
