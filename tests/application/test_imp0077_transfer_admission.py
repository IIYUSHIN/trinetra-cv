"""IMP-0077 tests: a received transfer becomes exactly one asset, once, and rejection frees its slot."""

from __future__ import annotations

import io
import logging
import threading

import pytest

from intake_support import T1, FakeAssets, FrozenClock, MemoryQuarantine, SequentialIds
from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeNotFoundError,
    IntakeStateConflictError,
    TransferRejectedError,
)
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.asset_admission import OCTET
from trinetra.domain.assets import AssetKind
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId, TransferId


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


class World:
    def __init__(self):
        self.clock, self.ids, self.registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
        self.quarantine, self.assets = MemoryQuarantine(), FakeAssets()
        self.receive = ReceiveTransferService(self.clock, self.ids, self.registry, self.quarantine)
        self.service = TransferAdmissionService(
            self.clock, self.registry, self.quarantine, self.assets, self.assets
        )
        self.intake = CreateIntakeService(self.clock, self.ids, self.registry).create(
            CreateIntakeCommand(command_id=cid(250))
        ).intake_id
        self._n = 0

    def upload(self, slot=PackageSlot.DATASET, fmt="coco", media="application/zip", name="d.zip",
               data=b"abc"):  # fmt: skip
        self._n += 1
        return self.receive.receive(ReceiveTransferCommand(
            intake_id=self.intake, command_id=cid(self._n), slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None, logical_identifier=None,
            filename=OriginalFilename(value=name), media_type=media, stream=io.BytesIO(data),
        ))  # fmt: skip

    def session(self):
        return self.registry.require(self.intake)

    def stored(self, transfer):
        return self.session().get_transfer(transfer.transfer_id)


def test_admission_turns_a_received_transfer_into_its_reserved_asset():
    w = World()
    received = w.upload()
    w.clock.set(T1)
    admitted = w.service.admit(w.intake, received.transfer_id)
    assert admitted.transfer_state is TransferState.ADMITTED
    assert admitted.admitted_asset_id == received.reserved_asset_id
    assert w.stored(received) == admitted
    assert w.session().updated_at == T1
    assert received.reserved_asset_id in w.assets.records
    assert w.quarantine.entries == {}


def test_the_port_gets_the_reserved_id_the_slots_kind_and_the_clients_claims():
    w = World()
    t = w.upload(slot=PackageSlot.MODEL, fmt="onnx", media="application/zip", name="m.onnx")
    w.service.admit(w.intake, t.transfer_id)
    (call,) = w.assets.calls
    assert call == {
        "handle": t.quarantine_handle,
        "asset_id": t.reserved_asset_id,
        "kind": AssetKind.MODEL,
        "media_type": "application/zip",
        "supplied_name": "m.onnx",
    }


def test_a_missing_media_type_is_admitted_as_octet_stream():
    w = World()
    t = w.upload(slot=PackageSlot.REFERENCE, fmt=None, media=None)
    w.service.admit(w.intake, t.transfer_id)
    assert w.assets.calls[0]["media_type"] == OCTET
    assert w.assets.calls[0]["kind"] is AssetKind.REFERENCE


def test_a_rejected_upload_is_marked_failed_with_the_reason_and_frees_its_slot():
    w = World()
    t = w.upload()
    w.assets.error = TransferRejectedError("no", details={"reason": "asset_profile_rejected"})
    with pytest.raises(TransferRejectedError):
        w.service.admit(w.intake, t.transfer_id)
    failed = w.stored(t)
    assert failed.transfer_state is TransferState.FAILED
    assert failed.error_code.text == "asset_profile_rejected"
    assert failed.admitted_asset_id is None
    assert w.quarantine.entries == {}
    assert w.session().readiness().counts[0].present == 0  # the dataset slot no longer counts it
    w.assets.error = None
    again = w.upload()
    w.service.admit(w.intake, again.transfer_id)
    assert w.stored(again).transfer_state is TransferState.ADMITTED


@pytest.mark.parametrize("details", [None, {}, {"reason": 7}, {"reason": "Not A Token"}, {"reason": ""}])
def test_a_rejection_without_a_usable_reason_still_fails_the_transfer(details):
    w = World()
    t = w.upload()
    w.assets.error = TransferRejectedError("no", details=details)
    with pytest.raises(TransferRejectedError):
        w.service.admit(w.intake, t.transfer_id)
    assert w.stored(t).error_code.text == "rejected"


def test_an_infrastructure_failure_leaves_the_transfer_received_and_retryable():
    w = World()
    t = w.upload()
    w.assets.error = RuntimeError("database is down")
    with pytest.raises(RuntimeError):
        w.service.admit(w.intake, t.transfer_id)
    assert w.stored(t).transfer_state is TransferState.RECEIVED
    assert t.quarantine_handle in w.quarantine.entries
    w.assets.error = None
    assert w.service.admit(w.intake, t.transfer_id).transfer_state is TransferState.ADMITTED


def test_an_asset_that_already_exists_for_the_reserved_id_is_not_admitted_twice():
    w = World()
    t = w.upload()
    w.assets.admit(handle=t.quarantine_handle, asset_id=t.reserved_asset_id, kind=AssetKind.DATASET,
                   media_type="application/zip", supplied_name="d.zip")  # an earlier attempt  # fmt: skip
    w.assets.calls.clear()
    admitted = w.service.admit(w.intake, t.transfer_id)
    assert admitted.transfer_state is TransferState.ADMITTED
    assert w.assets.calls == []
    assert w.quarantine.entries == {}


def test_an_already_admitted_transfer_is_verified_and_reused():
    w = World()
    t = w.upload()
    first = w.service.admit(w.intake, t.transfer_id)
    w.assets.calls.clear()
    assert w.service.admit(w.intake, t.transfer_id) == first
    assert w.assets.calls == []


def test_an_admitted_transfer_whose_record_vanished_is_a_conflict_not_a_silent_reuse():
    w = World()
    t = w.upload()
    w.service.admit(w.intake, t.transfer_id)
    w.assets.records.clear()
    with pytest.raises(ApplicationConflictError):
        w.service.admit(w.intake, t.transfer_id)


def test_a_failed_transfer_cannot_be_admitted_again():
    w = World()
    t = w.upload()
    w.assets.error = TransferRejectedError("no", details={"reason": "x"})
    with pytest.raises(TransferRejectedError):
        w.service.admit(w.intake, t.transfer_id)
    w.assets.calls.clear()
    with pytest.raises(IntakeStateConflictError, match="upload it again"):
        w.service.admit(w.intake, t.transfer_id)
    assert w.assets.calls == []


def test_a_closed_intake_unknown_intake_and_unknown_transfer_are_refused():
    w = World()
    t = w.upload()
    with pytest.raises(IntakeStateConflictError, match="unknown transfer"):
        w.service.admit(w.intake, TransferId.from_entropy(lambda n: b"\xee" * n))
    with pytest.raises(IntakeNotFoundError):
        w.service.admit(IntakeId.from_entropy(lambda n: b"\xee" * n), t.transfer_id)
    w.registry.replace(w.session().abandoned(w.clock.now()))
    with pytest.raises(IntakeStateConflictError) as exc:
        w.service.admit(w.intake, t.transfer_id)
    assert w.session().intake_state is IntakeState.ABANDONED
    assert "ABANDONED" in str(exc.value)
    assert w.assets.calls == []


def test_an_admission_that_returns_a_different_asset_id_is_refused():
    w = World()
    t = w.upload()
    w.assets.wrong_id = True
    with pytest.raises(ApplicationConflictError, match="different asset id"):
        w.service.admit(w.intake, t.transfer_id)
    assert w.stored(t).transfer_state is TransferState.RECEIVED


def test_a_failing_quarantine_cleanup_never_undoes_the_admission(caplog):
    w = World()
    t = w.upload()
    w.quarantine.fail_remove = True
    with caplog.at_level(logging.WARNING, logger="trinetra.application.intake"):
        admitted = w.service.admit(w.intake, t.transfer_id)
    assert admitted.transfer_state is TransferState.ADMITTED
    assert any("quarantine_cleanup_failed" in r.getMessage() for r in caplog.records)


def test_a_rejected_transfers_failing_cleanup_still_records_the_failure(caplog):
    w = World()
    t = w.upload()
    w.quarantine.fail_remove = True
    w.assets.error = TransferRejectedError("no", details={"reason": "boom"})
    logger = "trinetra.application.intake"
    with caplog.at_level(logging.WARNING, logger=logger), pytest.raises(TransferRejectedError):
        w.service.admit(w.intake, t.transfer_id)
    assert w.stored(t).transfer_state is TransferState.FAILED


def test_two_threads_admitting_the_same_transfer_call_the_port_once():
    w = World()
    t = w.upload()
    barrier = threading.Barrier(2, timeout=5)
    results, errors = [], []

    def run():
        barrier.wait()
        try:
            results.append(w.service.admit(w.intake, t.transfer_id))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert errors == [] and len(results) == 2 and results[0] == results[1]
    assert len(w.assets.calls) == 1


def test_admission_holds_the_intake_lock_so_a_commit_cannot_interleave():
    w = World()
    t = w.upload()
    held = {}

    def probe():
        # while the port runs, another thread must not be able to take the intake lock
        got = threading.Event()

        def other():
            with w.registry.exclusive(w.intake):
                got.set()

        thread = threading.Thread(target=other)
        thread.start()
        held["blocked"] = not got.wait(0.3)
        w.assets.on_admit = None
        thread.join(5)

    w.assets.on_admit = probe
    w.service.admit(w.intake, t.transfer_id)
    assert held["blocked"] is True
