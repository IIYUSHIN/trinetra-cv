"""IMP-0065 tests: assets, CAS objects and the audit chain are the same after a process restart."""

from __future__ import annotations

import json

import pytest

from process_support import run_python
from trinetra.domain.assets import AssetId
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_repository import AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_read import read_object
from trinetra.infrastructure.cas_verify import scan_objects
from trinetra.infrastructure.migration_runner import require_current, upgrade_to_head
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore

WRITER = """
    mode = sys.argv[2]
    with CoreStore.open(DatabaseConfig(root)) as store:
        upgrade_to_head(store)
        service = AssetAdmissionService(CasStore(CasLayout.for_state_root(root)), AssetRepository())
        audit = AuditRepository()
        out = []
        for data in (b"alpha", b"beta", b"alpha"):
            result = run_audited(store, audit, ingest_work(service, data), on_failure=None)
            record = result.value.record
            out.append({"id": str(record.record_id), "blob": record.blob_digest.to_wire(),
                        "created": result.value.blob_created, "seq": result.event.journal_sequence})
        with store.unit_of_work() as uow:
            head = audit.head(uow)
            uow.rollback()
        print(json.dumps({"records": out, "head": head.link_digest.to_wire(), "sequence": head.sequence}))
        sys.stdout.flush()
        if mode == "crash":
            os._exit(9)  # no close, no checkpoint: the next process must recover from the WAL
"""


def phase_one(root, mode):
    done = run_python(WRITER, root, mode)
    expected = 9 if mode == "crash" else 0
    assert done.returncode == expected, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(params=["clean", "crash"])
def written(request, state_root):
    return state_root, phase_one(state_root, request.param)


def test_the_first_process_wrote_what_the_test_expects(written):
    _, facts = written
    assert [r["created"] for r in facts["records"]] == [True, True, False]  # duplicate bytes share a blob
    assert facts["sequence"] == 3


def test_every_record_is_identical_after_restart(written):
    root, facts = written
    with CoreStore.open(DatabaseConfig(root)) as store:
        require_current(store)
        with store.unit_of_work() as uow:
            for item in facts["records"]:
                record = AssetRepository().get(uow, AssetId.parse(item["id"]))
                assert record is not None and record.blob_digest.to_wire() == item["blob"]
            assert AssetRepository().count(uow) == 3
            uow.rollback()


def test_cas_objects_are_intact_and_readable_after_restart(written):
    root, facts = written
    layout = CasLayout.for_state_root(root)
    report = scan_objects(layout)
    assert report.clean and report.verified == 2  # alpha (shared by two records) and beta
    contents = {read_object(layout, item["blob"], max_bytes=100) for item in facts["records"]}
    assert contents == {b"alpha", b"beta"}


def test_the_audit_chain_and_its_head_survive_restart(written):
    root, facts = written
    with CoreStore.open(DatabaseConfig(root)) as store, store.unit_of_work() as uow:
        report = verify_chain(uow, AuditRepository().head(uow).journal_id)
        uow.rollback()
    assert report.intact and report.events_checked == 3
    assert report.head_link_digest == facts["head"]


def test_the_journal_continues_from_where_it_stopped(written):
    root, facts = written
    again = run_python(
        """
        with CoreStore.open(DatabaseConfig(root)) as store:
            upgrade_to_head(store)  # idempotent at head
            service = AssetAdmissionService(CasStore(CasLayout.for_state_root(root)), AssetRepository())
            result = run_audited(store, AuditRepository(), ingest_work(service, b"gamma"), on_failure=None)
            print(result.event.journal_sequence)
        """,
        root,
    )
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip() == str(facts["sequence"] + 1)


def test_reopening_in_process_twice_changes_nothing(state_root):
    phase_one(state_root, "clean")
    heads = []
    for _ in range(2):
        with CoreStore.open(DatabaseConfig(state_root)) as store:
            upgrade_to_head(store)
            with store.unit_of_work() as uow:
                heads.append(AuditRepository().head(uow).link_digest)
                uow.rollback()
    assert heads[0] == heads[1]


def test_a_fresh_state_root_with_no_history_reopens_empty(state_root):
    with CoreStore.open(DatabaseConfig(state_root)) as store:
        upgrade_to_head(store)
    with CoreStore.open(DatabaseConfig(state_root)) as store, store.unit_of_work() as uow:
        assert AssetRepository().count(uow) == 0 and AuditRepository().head(uow) is None
        uow.rollback()
