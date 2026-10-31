"""IMP-0077 tests: the real quarantine and admission adapters behind the intake ports."""

from __future__ import annotations

import io
import os
import stat
from datetime import UTC, datetime

import pytest

from intake_support import ScriptedStream
from trinetra.application.errors import TransferRejectedError, TransferTooLargeError
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commands import CreateIntakeCommand, ReceiveTransferCommand
from trinetra.application.intake.enums import PackageSlot, TransferState
from trinetra.application.intake.receive import ReceiveTransferService
from trinetra.application.intake.records import OriginalFilename
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.application.intake.slots import DeclaredFormat
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.audit import AuditOutcome
from trinetra.domain.decisions import CommandId
from trinetra.domain.digests import DigestSha256
from trinetra.domain.intake_ids import QuarantineHandle
from trinetra.infrastructure.asset_admission_service import AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_repository import AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_admission import AdmissionError
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects
from trinetra.infrastructure.intake_adapters import (
    DIRNAME,
    AuditedAssetAdmission,
    IntakeQuarantine,
    StoreAssetLookup,
    SystemClock,
    SystemIdFactory,
)
from trinetra.timeutil import FixedClock


def handle(n=1):
    return QuarantineHandle.from_entropy(lambda size: n.to_bytes(size, "big"))


def cid(n):
    return CommandId.from_entropy(lambda size: bytes([n]) * size)


@pytest.fixture
def quarantine(state_root):
    return IntakeQuarantine.for_state_root(state_root)


# ------------------------------------------------------------------------------------ quarantine
def test_the_directory_is_private_and_created_once(state_root):
    first = IntakeQuarantine.for_state_root(state_root)
    again = IntakeQuarantine.for_state_root(state_root)
    info = (state_root / DIRNAME).lstat()
    assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
    assert first.entries() == again.entries() == []


def test_a_symlinked_or_shared_directory_is_refused(state_root, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    (state_root / DIRNAME).symlink_to(real)
    with pytest.raises(TransferRejectedError):
        IntakeQuarantine.for_state_root(state_root)
    (state_root / DIRNAME).unlink()
    shared = state_root / DIRNAME
    shared.mkdir(mode=0o700)
    shared.chmod(0o770)
    with pytest.raises(TransferRejectedError):
        IntakeQuarantine.for_state_root(state_root)


def test_an_entry_is_a_private_empty_file_and_cannot_be_created_twice(quarantine, state_root):
    quarantine.create_entry(handle())
    info = (state_root / DIRNAME / f"{handle()}.part").lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == 0
    with pytest.raises(FileExistsError):
        quarantine.create_entry(handle())


def test_streamed_bytes_land_intact_and_the_count_is_exact(quarantine):
    data = os.urandom(3 * 1024 * 1024 + 17)
    quarantine.create_entry(handle())
    assert quarantine.write_stream(handle(), io.BytesIO(data), max_bytes=len(data)) == len(data)
    with quarantine.open_for_read(handle()) as source:
        assert source.read() == data


def test_an_oversize_stream_is_stopped_at_the_ceiling(quarantine):
    quarantine.create_entry(handle())
    with pytest.raises(TransferTooLargeError):
        quarantine.write_stream(handle(), io.BytesIO(b"x" * 11), max_bytes=10)


def test_exactly_the_ceiling_is_allowed(quarantine):
    quarantine.create_entry(handle())
    assert quarantine.write_stream(handle(), io.BytesIO(b"x" * 10), max_bytes=10) == 10


@pytest.mark.parametrize("bad", ["text", 5, None, bytearray(b"ab")])
def test_a_stream_that_returns_non_bytes_is_rejected(quarantine, bad):
    class Bad:
        def read(self, size, /):
            return bad

    quarantine.create_entry(handle())
    with pytest.raises(TransferRejectedError):
        quarantine.write_stream(handle(), Bad(), max_bytes=10)


def test_a_source_that_returns_more_than_asked_is_rejected(quarantine):
    class Greedy:
        def read(self, size, /):
            return b"x" * (size + 1)

    quarantine.create_entry(handle())
    with pytest.raises(TransferRejectedError):
        quarantine.write_stream(handle(), Greedy(), max_bytes=10**9)


def test_writing_to_a_missing_entry_fails_instead_of_creating_one(quarantine):
    with pytest.raises(FileNotFoundError):
        quarantine.write_stream(handle(), io.BytesIO(b"x"), max_bytes=10)
    assert quarantine.entries() == []


def test_an_entry_replaced_by_a_symlink_or_hardlink_is_not_followed(quarantine, state_root, tmp_path):
    target = tmp_path / "victim"
    target.write_bytes(b"original")
    path = state_root / DIRNAME / f"{handle()}.part"
    path.symlink_to(target)
    with pytest.raises(OSError):
        quarantine.write_stream(handle(), io.BytesIO(b"overwrite"), max_bytes=100)
    assert target.read_bytes() == b"original"
    path.unlink()
    quarantine.create_entry(handle())
    os.link(path, tmp_path / "second-name")
    with pytest.raises(TransferRejectedError):
        quarantine.write_stream(handle(), io.BytesIO(b"x"), max_bytes=100)
    with pytest.raises(TransferRejectedError):
        quarantine.open_for_read(handle())


def test_reading_an_entry_replaced_by_a_symlink_is_refused(quarantine, state_root, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(b"do not read")
    (state_root / DIRNAME / f"{handle()}.part").symlink_to(secret)
    with pytest.raises(OSError):
        quarantine.open_for_read(handle())


def test_a_regular_file_standing_in_for_the_directory_is_refused(state_root):
    (state_root / DIRNAME).write_bytes(b"")
    (state_root / DIRNAME).chmod(0o600)
    with pytest.raises(TransferRejectedError, match="real directory"):
        IntakeQuarantine.for_state_root(state_root)


def test_a_directory_loosened_after_start_up_stops_new_entries(quarantine, state_root):
    (state_root / DIRNAME).chmod(0o777)
    with pytest.raises(TransferRejectedError, match="private"):
        quarantine.create_entry(handle())
    (state_root / DIRNAME).chmod(0o700)


def test_a_device_node_posing_as_an_entry_is_refused(quarantine, state_root):
    path = state_root / DIRNAME / f"{handle()}.part"
    try:
        os.mknod(path, stat.S_IFCHR | 0o600, os.makedev(1, 3))
    except PermissionError:
        pytest.skip("creating a device node needs root")
    with pytest.raises(TransferRejectedError, match="regular file"):
        quarantine.open_for_read(handle())


def test_an_entry_or_directory_owned_by_someone_else_is_refused(quarantine, state_root):
    quarantine.create_entry(handle())
    entry = state_root / DIRNAME / f"{handle()}.part"
    try:
        os.chown(entry, 4242, 4242)
    except PermissionError:
        pytest.skip("changing ownership needs root")
    with pytest.raises(TransferRejectedError, match="regular file"):
        quarantine.open_for_read(handle())
    os.chown(state_root / DIRNAME, 4242, 4242)
    with pytest.raises(TransferRejectedError, match="private"):
        quarantine.create_entry(handle(2))


def test_remove_deletes_and_is_idempotent(quarantine):
    quarantine.create_entry(handle())
    quarantine.remove(handle())
    quarantine.remove(handle())
    assert quarantine.entries() == []


def test_sweep_removes_only_old_entries(quarantine, state_root):
    for n in (1, 2):
        quarantine.create_entry(handle(n))
    old = state_root / DIRNAME / f"{handle(1)}.part"
    os.utime(old, (1000, 1000))
    removed = quarantine.sweep(older_than_seconds=3600, now=1000 + 7200)
    assert removed == [f"{handle(1)}.part"]
    assert quarantine.entries() == [f"{handle(2)}.part"]
    with pytest.raises(ValueError, match="negative"):
        quarantine.sweep(older_than_seconds=-1)


def test_sweep_includes_an_entry_exactly_at_the_cutoff_and_skips_non_files(quarantine, state_root):
    quarantine.create_entry(handle(1))
    os.utime(state_root / DIRNAME / f"{handle(1)}.part", (1000, 1000))
    (state_root / DIRNAME / f"{handle(2)}.part").mkdir()
    os.utime(state_root / DIRNAME / f"{handle(2)}.part", (1000, 1000))
    assert quarantine.sweep(older_than_seconds=3600, now=4600) == [f"{handle(1)}.part"]
    assert (state_root / DIRNAME / f"{handle(2)}.part").is_dir()


def test_sweep_leaves_foreign_files_alone(quarantine, state_root):
    (state_root / DIRNAME / "notes.txt").write_text("keep")
    assert quarantine.sweep(older_than_seconds=0, now=2**40) == []
    assert (state_root / DIRNAME / "notes.txt").exists()


# ------------------------------------------------------------------------------------ admission
class Rig:
    def __init__(self, store, state_root):
        self.store, self.root = store, state_root
        self.quarantine = IntakeQuarantine.for_state_root(state_root)
        self.cas = CasStore(CasLayout.for_state_root(state_root))
        numbers = iter(range(1, 10**6))
        self.audit = AuditRepository(
            clock=FixedClock(datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)),
            entropy=lambda n: next(numbers).to_bytes(n, "big"),
        )
        self.repository = AssetRepository()
        service = AssetAdmissionService(self.cas, self.repository)
        self.admission = AuditedAssetAdmission(store, self.audit, service, self.quarantine)
        self.lookup = StoreAssetLookup(store, self.repository)

    def stage(self, data, n=1):
        h = handle(n)
        self.quarantine.create_entry(h)
        self.quarantine.write_stream(h, io.BytesIO(data), max_bytes=10**9)
        return h

    def events(self):
        with self.store.unit_of_work() as uow:
            page = self.audit.list_page(uow, limit=100)
            report = verify_chain(uow, page.events[0].journal_id) if page.events else None
            uow.rollback()
        return page.events, report


@pytest.fixture
def rig(store, state_root):
    return Rig(store, state_root)


def asset_id(n=7):
    return AssetId.from_entropy(lambda size: n.to_bytes(size, "big"))


def admit(rig, data=b"a contributor note", *, kind=AssetKind.CONTRIBUTOR_METADATA, media="text/plain", n=1):
    return rig.admission.admit(handle=rig.stage(data, n), asset_id=asset_id(), kind=kind,
                               media_type=media, supplied_name="note.txt")  # fmt: skip


def test_admission_creates_the_record_under_the_reserved_id_and_audits_it(rig):
    assert rig.lookup.exists(asset_id()) is False
    record = admit(rig)
    assert record.record_id == asset_id()
    assert record.asset_kind is AssetKind.CONTRIBUTOR_METADATA
    assert record.size_bytes == len(b"a contributor note")
    assert rig.lookup.exists(asset_id()) is True
    events, report = rig.events()
    assert [e.event_type.text for e in events] == ["asset_ingested"]
    assert events[0].outcome is AuditOutcome.SUCCESS
    subject = events[0].subject_refs[0]
    assert subject.object_id == str(asset_id())
    assert subject.record_digest == DigestSha256.parse(record.canonical_digest())
    assert events[0].context_ids == (str(asset_id()),)
    assert report.intact


def test_rejected_bytes_become_a_typed_rejection_with_a_reason_and_leave_no_asset(rig):
    with pytest.raises(TransferRejectedError) as exc:
        admit(rig, b"not a model", kind=AssetKind.MODEL, media="text/plain")
    assert exc.value.details["reason"] == "asset_profile_rejected"
    assert rig.lookup.exists(asset_id()) is False
    assert scan_objects(rig.cas.layout).verified == 0
    events, report = rig.events()
    assert [(e.event_type.text, e.outcome) for e in events] == [("asset_rejected", AuditOutcome.FAILURE)]
    assert events[0].reason_code == "ASSET_PROFILE_REJECTED"
    assert events[0].context_ids == (str(asset_id()),)
    assert report.intact


def test_content_that_contradicts_its_claimed_type_is_rejected(rig):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    with pytest.raises(TransferRejectedError):
        admit(rig, png, kind=AssetKind.CONTRIBUTOR_METADATA, media="text/plain")
    assert rig.lookup.exists(asset_id()) is False


def test_a_cas_level_rejection_is_also_a_typed_rejection(rig):
    class Refusing:
        def admit(self, *_a, **_k):
            raise AdmissionError("the stream does not match")

    admission = AuditedAssetAdmission(rig.store, rig.audit, Refusing(), rig.quarantine)
    with pytest.raises(TransferRejectedError) as exc:
        admission.admit(handle=rig.stage(b"x"), asset_id=asset_id(), kind=AssetKind.CONTRIBUTOR_METADATA,
                        media_type="text/plain", supplied_name="x.txt")  # fmt: skip
    assert exc.value.details["reason"] == "cas_admission_rejected"


def test_an_infrastructure_failure_is_not_disguised_as_a_rejection_but_is_audited(rig):
    with pytest.raises(FileNotFoundError):
        rig.admission.admit(handle=handle(99), asset_id=asset_id(), kind=AssetKind.CONTRIBUTOR_METADATA,
                            media_type="text/plain", supplied_name="x.txt")  # fmt: skip
    events, _ = rig.events()
    assert [(e.event_type.text, e.reason_code) for e in events] == [("asset_rejected", "INTERNAL_ERROR")]


def test_reusing_a_reserved_id_for_other_bytes_is_refused_and_the_first_asset_survives(rig):
    first = admit(rig, b"first note", n=1)
    with pytest.raises(Exception, match="exist"):
        admit(rig, b"second note", n=2)
    assert rig.lookup.exists(asset_id()) is True
    with rig.store.unit_of_work() as uow:
        assert rig.repository.get(uow, asset_id()).blob_digest == first.blob_digest
        uow.rollback()


def test_identical_bytes_under_two_reserved_ids_share_one_blob(rig):
    admit(rig, b"same", n=1)
    rig.admission.admit(
        handle=rig.stage(b"same", 2),
        asset_id=asset_id(8),
        kind=AssetKind.CONTRIBUTOR_METADATA,
        media_type="text/plain",
        supplied_name="b.txt",
    )
    assert scan_objects(rig.cas.layout).verified == 1
    assert rig.lookup.exists(asset_id(7)) and rig.lookup.exists(asset_id(8))


# ------------------------------------------------------------------------------------ system ports
def test_the_system_clock_returns_whole_second_utc_timestamps():
    stamp = SystemClock().now()
    assert str(stamp).endswith("Z") and "." not in str(stamp)


def test_the_system_id_factory_draws_distinct_typed_ids():
    ids = SystemIdFactory()
    made = [ids.new_intake_id(), ids.new_intake_id(), ids.new_transfer_id(), ids.new_asset_id(),
            ids.new_case_id(), ids.new_quarantine_handle()]  # fmt: skip
    assert len({str(m) for m in made}) == len(made)
    assert [str(m).split("_")[0] for m in made] == ["intake", "intake", "transfer", "asset", "case", "quar"]


def test_the_id_factory_uses_the_injected_entropy():
    ids = SystemIdFactory(entropy=lambda n: b"\x05" * n)
    assert str(ids.new_asset_id()) == "asset_" + "05" * 16
    assert str(ids.new_case_id()) == "case_" + "05" * 16
    assert str(ids.new_intake_id()) == "intake_" + "05" * 16
    assert str(ids.new_transfer_id()) == "transfer_" + "05" * 16
    assert str(ids.new_quarantine_handle()) == "quar_" + "05" * 16


# ------------------------------------------------------------------------------------ end to end
def test_upload_then_admit_through_the_real_adapters(store, state_root):
    clock, ids, registry = SystemClock(), SystemIdFactory(), InMemoryIntakeRegistry()
    rig = Rig(store, state_root)
    receive = ReceiveTransferService(clock, ids, registry, rig.quarantine)
    admit_service = TransferAdmissionService(clock, registry, rig.quarantine, rig.admission, rig.lookup)
    creator = CreateIntakeService(clock, ids, registry)
    intake = creator.create(CreateIntakeCommand(command_id=cid(1))).intake_id
    note = b'{"contributor": "unit test"}'
    transfer = receive.receive(ReceiveTransferCommand(
        intake_id=intake, command_id=cid(2), slot=PackageSlot.CONTRIBUTOR_METADATA,
        declared_format=DeclaredFormat("json"), logical_identifier=None,
        filename=OriginalFilename(value="who.json"), media_type="application/json",
        stream=ScriptedStream(note),
    ))  # fmt: skip
    assert transfer.received_byte_size == len(note) and rig.quarantine.entries() != []
    admitted = admit_service.admit(intake, transfer.transfer_id)
    assert admitted.transfer_state is TransferState.ADMITTED
    assert rig.lookup.exists(transfer.reserved_asset_id)
    assert rig.quarantine.entries() == []
    events, report = rig.events()
    assert [e.event_type.text for e in events] == ["asset_ingested"] and report.intact
