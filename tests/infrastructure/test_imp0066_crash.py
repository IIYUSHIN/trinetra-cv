"""IMP-0066 tests: a process killed mid-admission leaves no partial object, record or audit event."""

from __future__ import annotations

import hashlib
import time

import pytest

from process_support import run_python
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_repository import AuditRepository
from trinetra.infrastructure.audit_verify import verify_all
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import Quarantine, is_stage_name
from trinetra.infrastructure.cas_verify import scan_objects
from trinetra.infrastructure.migration_runner import upgrade_to_head
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore

PAYLOAD = b"x" * 5000

PREPARE = """
    store = CoreStore.open(DatabaseConfig(root))
    upgrade_to_head(store)
    service = AssetAdmissionService(CasStore(CasLayout.for_state_root(root)), AssetRepository())
"""

CRASH_MID_STREAM = PREPARE + """
    class Dies:
        def __init__(self):
            self.sent = 0
        def read(self, n):
            if self.sent >= 2000:
                os._exit(9)                      # killed while the quarantine file is half written
            self.sent += 1000
            return b"x" * 1000
    with store.unit_of_work() as uow:
        service.admit(uow, Dies(), AdmissionRequest(kind=AssetKind.CONTRIBUTOR_METADATA,
                      media_type="text/plain", supplied_name="n.txt", created_by=ACTOR))
"""

CRASH_AFTER_CAS_COMMIT = PREPARE + """
    run_audited(store, AuditRepository(), ingest_work(service, b"x" * 5000, hook=lambda: os._exit(9)),
                on_failure=None)
"""

CRASH_BEFORE_DB_COMMIT = PREPARE + """
    with store.unit_of_work() as uow:
        admitted = service.admit(uow, io.BytesIO(b"x" * 5000), AdmissionRequest(
            kind=AssetKind.CONTRIBUTOR_METADATA, media_type="text/plain", supplied_name="n.txt",
            created_by=ACTOR))
        AuditRepository().append(uow, AuditDraft(event_type=AuditEventType("asset_ingested"),
                                 outcome=AuditOutcome.SUCCESS, created_by=ACTOR))
        os._exit(9)                              # record and event inserted, never committed
"""


def crash(root, script):
    done = run_python(script, root)
    assert done.returncode == 9, done.stderr


def db_counts(root):
    with CoreStore.open(DatabaseConfig(root)) as store:
        upgrade_to_head(store)
        with store.unit_of_work() as uow:
            counts = (AssetRepository().count(uow), AuditRepository().count(uow))
            reports = verify_all(uow)
            uow.rollback()
    assert all(r.intact for r in reports)
    return counts


def reingest(root):
    done = run_python(
        """
        with CoreStore.open(DatabaseConfig(root)) as store:
            upgrade_to_head(store)
            service = AssetAdmissionService(CasStore(CasLayout.for_state_root(root)), AssetRepository())
            result = run_audited(store, AuditRepository(), ingest_work(service, b"x" * 5000), on_failure=None)
            print(result.value.blob_created, result.event.journal_sequence)
        """,
        root,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def test_a_crash_mid_stream_leaves_no_object_only_a_sweepable_stage_file(state_root):
    crash(state_root, CRASH_MID_STREAM)
    layout = CasLayout.for_state_root(state_root)
    assert scan_objects(layout).verified == 0 and scan_objects(layout).clean
    quarantine = Quarantine(layout)
    leftovers = quarantine.orphans()
    assert len(leftovers) == 1 and is_stage_name(leftovers[0])
    assert (layout.quarantine / leftovers[0]).stat().st_size <= 2000  # partial, and only partial
    assert db_counts(state_root) == (0, 0)
    removed = quarantine.sweep(older_than_seconds=1, now=time.time() + 3600)
    assert removed == leftovers and quarantine.orphans() == []


def test_after_a_mid_stream_crash_the_same_bytes_admit_normally(state_root):
    crash(state_root, CRASH_MID_STREAM)
    created, sequence = reingest(state_root)
    assert (created, sequence) == ("True", "1")
    assert scan_objects(CasLayout.for_state_root(state_root)).verified == 1


def test_a_crash_after_the_cas_commit_leaves_a_valid_orphan_and_no_record(state_root):
    crash(state_root, CRASH_AFTER_CAS_COMMIT)
    layout = CasLayout.for_state_root(state_root)
    report = scan_objects(layout)
    assert report.clean and report.verified == 1  # a whole, verifiable object -- just unreferenced
    digest = "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()
    assert layout.object_path(digest).exists()
    assert db_counts(state_root) == (0, 0)


def test_the_orphan_is_reused_not_duplicated_when_the_bytes_come_back(state_root):
    crash(state_root, CRASH_AFTER_CAS_COMMIT)
    created, sequence = reingest(state_root)
    assert (created, sequence) == ("False", "1")  # blob already there; the record and event are new
    assert scan_objects(CasLayout.for_state_root(state_root)).verified == 1


def test_a_crash_before_the_database_commit_persists_neither_record_nor_event(state_root):
    crash(state_root, CRASH_BEFORE_DB_COMMIT)
    assert db_counts(state_root) == (0, 0)
    with CoreStore.open(DatabaseConfig(state_root)) as store, store.unit_of_work() as uow:
        assert uow.connection.exec_driver_sql("SELECT count(*) FROM journal_state").scalar_one() == 0
        uow.rollback()


@pytest.mark.parametrize("script", [CRASH_MID_STREAM, CRASH_AFTER_CAS_COMMIT, CRASH_BEFORE_DB_COMMIT])
def test_the_database_passes_its_own_integrity_check_after_every_crash(state_root, script):
    crash(state_root, script)
    with CoreStore.open(DatabaseConfig(state_root)) as store, store.unit_of_work() as uow:
        assert uow.connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert uow.connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        uow.rollback()


def test_repeated_crashes_accumulate_nothing_but_sweepable_stage_files(state_root):
    for _ in range(3):
        crash(state_root, CRASH_MID_STREAM)
    layout = CasLayout.for_state_root(state_root)
    assert len(Quarantine(layout).orphans()) == 3 and scan_objects(layout).verified == 0
    assert db_counts(state_root) == (0, 0)
