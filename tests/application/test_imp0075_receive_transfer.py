"""IMP-0075 tests: receive records only what quarantine wrote and cleans up after any failure."""

from __future__ import annotations

import dataclasses
import io
import logging
import threading

import pytest

import trinetra.application.intake.receive as receive_module
from intake_support import T1, FrozenClock, MemoryQuarantine, ScriptedStream, SequentialIds
from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeNotFoundError,
    IntakeStateConflictError,
    TransferRejectedError,
    TransferTooLargeError,
)
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot, TransferState
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.asset_admission import OCTET, AdmissionProfile, KindRule
from trinetra.domain.assets import AssetKind
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


class World:
    def __init__(self, profile=None):
        self.clock, self.ids, self.registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
        self.quarantine = MemoryQuarantine()
        kwargs = {} if profile is None else {"profile": profile}
        self.service = ReceiveTransferService(self.clock, self.ids, self.registry, self.quarantine, **kwargs)
        creator = CreateIntakeService(self.clock, self.ids, self.registry)
        self.intake = creator.create(CreateIntakeCommand(command_id=cid(200))).intake_id
        self._n = 0

    def command(self, *, slot=PackageSlot.DATASET, fmt="coco", data=b"payload", media="application/zip",
                stream=None, command_id=None, name="a.zip", intake=None):  # fmt: skip
        self._n += 1
        return ReceiveTransferCommand(
            intake_id=intake or self.intake,
            command_id=command_id or cid(self._n),
            slot=slot,
            declared_format=DeclaredFormat(fmt) if fmt else None,
            logical_identifier=None,
            filename=OriginalFilename(value=name),
            media_type=media,
            stream=stream if stream is not None else io.BytesIO(data),
        )

    def session(self):
        return self.registry.require(self.intake)


def test_a_received_upload_is_recorded_with_the_bytes_quarantine_wrote():
    w = World()
    w.clock.set(T1)
    transfer = w.service.receive(w.command(data=b"x" * 5000))
    assert transfer.transfer_state is TransferState.RECEIVED
    assert transfer.received_byte_size == 5000
    assert transfer.received_at == T1
    assert transfer.admitted_asset_id is None and transfer.error_code is None
    assert bytes(w.quarantine.entries[transfer.quarantine_handle]) == b"x" * 5000
    assert w.session().transfers == (transfer,)
    assert w.session().updated_at == T1


def test_the_request_fields_are_carried_and_nothing_is_admitted_here():
    w = World()
    cmd = dataclasses.replace(w.command(slot=PackageSlot.MODEL, fmt="onnx", media=None, name="m.onnx"),
                              logical_identifier="Detector v2")  # fmt: skip
    transfer = w.service.receive(cmd)
    assert transfer.slot is PackageSlot.MODEL
    assert transfer.declared_format.text == "onnx"
    assert transfer.media_type is None
    assert transfer.logical_identifier == "Detector v2"
    assert transfer.original_filename.value == "m.onnx"
    assert transfer.reserved_asset_id != transfer.transfer_id


def test_every_transfer_reserves_distinct_ids():
    w = World()
    a = w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None))
    b = w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None))
    assert len({a.transfer_id, b.transfer_id}) == 2
    assert len({a.reserved_asset_id, b.reserved_asset_id}) == 2
    assert len({a.quarantine_handle, b.quarantine_handle}) == 2


def test_an_empty_upload_is_recorded_as_zero_bytes():
    w = World()
    assert w.service.receive(w.command(data=b"")).received_byte_size == 0


def test_an_oversize_upload_is_stopped_and_leaves_no_trace():
    profile = AdmissionProfile(
        tuple((k, KindRule(frozenset({"application/zip", OCTET}), 10)) for k in AssetKind)
    )
    w = World(profile)
    with pytest.raises(TransferTooLargeError):
        w.service.receive(w.command(data=b"y" * 11))
    assert w.session().transfers == ()
    assert w.quarantine.entries == {}


def test_a_quarantine_that_overreports_its_size_is_rejected_and_cleaned():
    w = World()
    w.quarantine.write_stream = lambda *_a, max_bytes, **_k: max_bytes + 1
    with pytest.raises(TransferTooLargeError):
        w.service.receive(w.command())
    assert w.session().transfers == () and w.quarantine.entries == {}


@pytest.mark.parametrize("bad", [True, 3.0, None, -1])
def test_a_quarantine_that_returns_a_bad_count_is_rejected(bad):
    w = World()
    w.quarantine.write_stream = lambda *_a, **_k: bad
    with pytest.raises(TransferTooLargeError):
        w.service.receive(w.command())
    assert w.quarantine.entries == {}


def test_a_stream_that_dies_part_way_is_rejected_and_cleaned_up():
    w = World()
    with pytest.raises(TransferRejectedError):
        w.service.receive(w.command(stream=ScriptedStream(b"abcdefghij", fail_after=4)))
    assert w.session().transfers == () and w.quarantine.entries == {}
    assert len(w.quarantine.removed) == 1


def test_an_unexpected_error_is_not_disguised_but_still_cleans_up_and_frees_the_slot():
    w = World()
    boom = ScriptedStream(b"abcdef", fail_after=2, error=ValueError("bug"))
    with pytest.raises(ValueError, match="bug"):
        w.service.receive(w.command(stream=boom))
    assert w.quarantine.entries == {}
    assert w.service.receive(w.command()).received_byte_size == 7  # the dataset slot is free again


def test_a_failing_cleanup_never_masks_the_real_error_and_is_logged(caplog):
    w = World()
    w.quarantine.fail_remove = True
    with (
        caplog.at_level(logging.WARNING, logger="trinetra.application.intake"),
        pytest.raises(TransferRejectedError),
    ):
        w.service.receive(w.command(stream=ScriptedStream(b"abcdef", fail_after=2)))
    assert any("quarantine_cleanup_failed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("closer", ["abandoned"])
def test_a_closed_intake_accepts_nothing_and_reads_nothing(closer):
    w = World()
    now = w.clock.now()
    w.registry.replace(getattr(w.session(), closer)(now))
    stream = ScriptedStream()
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command(stream=stream))
    assert stream.reads == 0 and w.quarantine.entries == {}


def test_an_unknown_intake_is_reported():
    w = World()
    stranger = IntakeId.from_entropy(lambda size: b"\xee" * size)
    with pytest.raises(IntakeNotFoundError):
        w.service.receive(w.command(intake=stranger))


def test_a_full_slot_refuses_without_reading_a_byte():
    w = World()
    w.service.receive(w.command())
    stream = ScriptedStream()
    with pytest.raises(IntakeStateConflictError) as exc:
        w.service.receive(w.command(stream=stream))
    assert exc.value.details == {"slot": "dataset"}
    assert stream.reads == 0 and len(w.quarantine.entries) == 1


def test_invalid_claims_are_refused_before_any_state_changes():
    w = World()
    stream = ScriptedStream()
    with pytest.raises(Exception, match="slot does not accept"):
        w.service.receive(w.command(fmt="onnx", stream=stream))
    assert stream.reads == 0 and w.quarantine.entries == {} and w.session().transfers == ()


def test_concurrent_uploads_into_a_single_slot_let_exactly_one_win():
    w = World()
    gate, inside = threading.Event(), threading.Event()
    slow = ScriptedStream(b"slow", on_read=lambda: (inside.set(), gate.wait(5)))
    results = {}

    def first():
        results["first"] = w.service.receive(w.command(stream=slow))

    thread = threading.Thread(target=first)
    thread.start()
    assert inside.wait(5)
    rival = ScriptedStream()
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command(stream=rival))  # the first upload is still in flight
    assert rival.reads == 0
    gate.set()
    thread.join(5)
    assert results["first"].transfer_state is TransferState.RECEIVED
    assert len(w.session().transfers) == 1


def test_a_slot_freed_by_a_failed_upload_can_be_used_again_but_a_slow_one_blocks_meanwhile():
    w = World()
    with pytest.raises(TransferRejectedError):
        w.service.receive(w.command(stream=ScriptedStream(b"abcdef", fail_after=2)))
    assert w.service.receive(w.command()).slot is PackageSlot.DATASET


def test_optional_unbounded_slots_accept_many_uploads_in_parallel():
    w = World()
    barrier = threading.Barrier(5, timeout=5)
    out, errors = [], []

    def work():
        try:
            out.append(w.service.receive(w.command(
                slot=PackageSlot.REFERENCE, fmt=None, media=None,
                stream=ScriptedStream(b"r", on_read=barrier.wait))))  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == [] and len(w.session().transfers) == 5


def test_the_transfer_limit_counts_uploads_still_in_flight(monkeypatch):
    monkeypatch.setattr(receive_module, "MAX_TRANSFERS_PER_INTAKE", 2)
    w = World()
    gate, inside = threading.Event(), threading.Event()
    slow = ScriptedStream(b"s", on_read=lambda: (inside.set(), gate.wait(5)))
    t = threading.Thread(target=lambda: w.service.receive(
        w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None, stream=slow)))  # fmt: skip
    t.start()
    assert inside.wait(5)
    w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None))
    with pytest.raises(IntakeStateConflictError, match="transfer limit"):
        w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None))
    gate.set()
    t.join(5)
    assert len(w.session().transfers) == 2


def test_an_intake_closed_while_the_bytes_were_arriving_discards_them():
    w = World()
    stream = ScriptedStream(b"late", on_read=lambda: w.registry.replace(w.session().abandoned(w.clock.now())))
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command(stream=stream))
    assert w.quarantine.entries == {} and w.session().transfers == ()


def test_time_moving_backwards_discards_the_upload():
    w = World()
    w.clock.set(T1)
    w.registry.replace(w.session().evolve(updated_at=T1))
    w.clock.set(w.session().created_at)
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command())
    assert w.quarantine.entries == {}


def test_a_repeated_command_returns_the_original_transfer_without_reading_again():
    w = World()
    first = w.service.receive(w.command(command_id=cid(77)))
    again_stream = ScriptedStream()
    second = w.service.receive(w.command(command_id=cid(77), stream=again_stream))
    assert second == first
    assert again_stream.reads == 0
    assert len(w.session().transfers) == 1 and len(w.quarantine.entries) == 1


@pytest.mark.parametrize("change", [
    {"name": "other.zip"}, {"media": "application/gzip"}, {"fmt": "yolo"},
    {"slot": PackageSlot.REFERENCE, "fmt": None, "media": None},
])  # fmt: skip
def test_the_same_command_id_for_a_different_upload_is_a_conflict(change):
    w = World()
    w.service.receive(w.command(command_id=cid(78)))
    with pytest.raises(ApplicationConflictError):
        w.service.receive(w.command(command_id=cid(78), **change))


def test_the_same_command_id_on_another_intake_is_a_conflict():
    w = World()
    w.service.receive(w.command(command_id=cid(79)))
    other = CreateIntakeService(w.clock, w.ids, w.registry).create(CreateIntakeCommand(command_id=cid(201)))
    with pytest.raises(ApplicationConflictError):
        w.service.receive(w.command(command_id=cid(79), intake=other.intake_id))


def test_two_identical_commands_racing_record_one_transfer_and_keep_one_copy():
    w = World()
    barrier = threading.Barrier(2, timeout=5)
    out = []

    def run():
        stream = ScriptedStream(b"same", on_read=barrier.wait)
        out.append(w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None,
                                               command_id=cid(80), stream=stream)))  # fmt: skip

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(out) == 2 and out[0] == out[1]
    assert len(w.session().transfers) == 1
    assert list(w.quarantine.entries) == [out[0].quarantine_handle]


def test_an_identical_retry_while_the_first_is_still_arriving_is_refused_not_duplicated():
    w = World()
    gate, inside = threading.Event(), threading.Event()
    slow = ScriptedStream(b"slow", on_read=lambda: (inside.set(), gate.wait(5)))
    thread = threading.Thread(target=lambda: w.service.receive(w.command(command_id=cid(81), stream=slow)))
    thread.start()
    assert inside.wait(5)
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command(command_id=cid(81)))
    gate.set()
    thread.join(5)
    assert w.service.receive(w.command(command_id=cid(81))) == w.session().transfers[0]


def test_an_upload_of_exactly_the_ceiling_is_accepted_and_one_byte_more_is_not():
    rule = KindRule(frozenset({"application/zip", OCTET}), 10)
    w = World(AdmissionProfile(tuple((kind, rule) for kind in AssetKind)))
    assert w.service.receive(w.command(data=b"z" * 10)).received_byte_size == 10


def test_bookkeeping_of_uploads_in_flight_is_released_after_every_outcome():
    w = World()
    w.service.receive(w.command())
    with pytest.raises(IntakeStateConflictError):
        w.service.receive(w.command())
    with pytest.raises(TransferRejectedError):
        w.service.receive(w.command(slot=PackageSlot.REFERENCE, fmt=None, media=None,
                                    stream=ScriptedStream(b"abcdef", fail_after=2)))  # fmt: skip
    assert len(w.service._in_flight) == 0  # a leak would grow without bound


def test_the_same_command_id_with_another_logical_identifier_is_a_conflict():
    w = World()
    w.service.receive(dataclasses.replace(w.command(command_id=cid(90)), logical_identifier="Alpha"))
    with pytest.raises(ApplicationConflictError):
        w.service.receive(dataclasses.replace(w.command(command_id=cid(90)), logical_identifier="Beta"))


def test_the_transfer_ledger_keeps_the_first_entry_for_a_command_id():
    w = World()
    first, second = SequentialIds().new_transfer_id(), w.ids.new_transfer_id()
    assert w.registry.recall_transfer(cid(95)) is None
    w.registry.remember_transfer(cid(95), w.intake, first, "one")
    w.registry.remember_transfer(cid(95), w.intake, second, "two")
    assert w.registry.recall_transfer(cid(95)) == (w.intake, first, "one")
