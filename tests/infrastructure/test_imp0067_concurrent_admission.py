"""IMP-0067 tests: concurrent admission of identical bytes converges on one immutable object."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter

import pytest

from process_support import PREAMBLE, SOURCE_ROOT
from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetKind
from trinetra.domain.audit import AuditEventType, AuditOutcome
from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_coupling import run_audited
from trinetra.infrastructure.audit_repository import AuditDraft, AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_admission import AdmissionError
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import Quarantine
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects

ACTOR = ActorId("core")
WAIT = 30


@pytest.fixture
def service(state_root):
    return AssetAdmissionService(CasStore(CasLayout.for_state_root(state_root)), AssetRepository())


def request(**kw):
    base = {
        "kind": AssetKind.CONTRIBUTOR_METADATA,
        "media_type": "text/plain",
        "supplied_name": "note.txt",
        "created_by": ACTOR,
    }
    return AdmissionRequest(**(base | kw))


def hammer(store, service, payloads, audit=None):
    """Admit every payload from its own thread, all released together. Returns (results, errors)."""
    audit = audit or AuditRepository()
    gate = threading.Barrier(len(payloads), timeout=WAIT)
    results, errors = [], []
    lock = threading.Lock()

    def worker(data, **kw):
        def work(uow):
            admitted = service.admit(uow, io.BytesIO(data), request(**kw))
            return admitted, AuditDraft(
                event_type=AuditEventType("asset_ingested"), outcome=AuditOutcome.SUCCESS, created_by=ACTOR
            )

        try:
            gate.wait()
            outcome = run_audited(store, audit, work, on_failure=None)
        except Exception as exc:  # noqa: BLE001 - collected and asserted on by the caller
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(outcome)

    threads = []
    for item in payloads:
        data, kwargs = (item, {}) if isinstance(item, bytes) else item
        threads.append(threading.Thread(target=worker, args=(data,), kwargs=kwargs))
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
    assert not any(t.is_alive() for t in threads), "a worker hung"
    return results, errors


def chain_ok(store):
    with store.unit_of_work() as uow:
        report = verify_chain(uow, AuditRepository().head(uow).journal_id)
        uow.rollback()
    return report


# ------------------------------------------------------------------------------------- threads
def test_identical_bytes_from_many_threads_make_one_object_and_many_records(store, service):
    results, errors = hammer(store, service, [b"same bytes" * 100] * 8)
    assert errors == [] and len(results) == 8
    assert Counter(r.value.blob_created for r in results) == {True: 1, False: 7}
    assert len({str(r.value.record.record_id) for r in results}) == 8
    assert len({r.value.record.blob_digest for r in results}) == 1
    layout = service._cas.layout
    report = scan_objects(layout)
    assert report.clean and report.verified == 1
    assert Quarantine(layout).orphans() == []  # every losing candidate was discarded


def test_every_concurrent_admission_has_its_own_gapless_audit_event(store, service):
    results, _ = hammer(store, service, [b"same bytes"] * 6)
    assert sorted(r.event.journal_sequence for r in results) == [1, 2, 3, 4, 5, 6]
    report = chain_ok(store)
    assert report.intact and report.events_checked == 6


def test_mixed_payloads_keep_exactly_one_object_per_distinct_content(store, service):
    payloads = [bytes([n]) * 500 for n in range(5) for _ in range(3)]
    results, errors = hammer(store, service, payloads)
    assert errors == [] and len(results) == 15
    assert scan_objects(service._cas.layout).verified == 5
    created = Counter(r.value.record.blob_digest for r in results if r.value.blob_created)
    assert set(created.values()) == {1} and len(created) == 5
    assert chain_ok(store).intact


def test_a_failing_declared_digest_does_not_disturb_the_others(store, service):
    wrong = DigestSha256.of(b"not this")
    results, errors = hammer(store, service, [b"shared"] * 4 + [(b"shared", {"expected_digest": wrong})])
    assert len(results) == 4 and len(errors) == 1 and isinstance(errors[0], AdmissionError)
    assert scan_objects(service._cas.layout).verified == 1
    with store.unit_of_work() as uow:
        assert AssetRepository().count(uow) == 4
        uow.rollback()
    assert chain_ok(store).events_checked == 4  # the refused admission wrote nothing


def test_the_admitted_object_is_read_only_and_single_linked_after_the_race(store, service):
    results, _ = hammer(store, service, [b"racing"] * 8)
    path = service._cas.layout.object_path(results[0].value.record.blob_digest)
    info = path.lstat()
    assert stat.S_IMODE(info.st_mode) == 0o444 and info.st_nlink == 1


# ----------------------------------------------------------------------------------- processes
WORKER = """
    import time
    gate = Path(sys.argv[2])
    deadline = time.time() + 30
    while not gate.exists():
        if time.time() > deadline:
            sys.exit(3)
        time.sleep(0.005)
    result = CasStore(CasLayout.for_state_root(root)).admit(io.BytesIO(b"p" * 2_000_000))
    print(result.created)
"""


def test_separate_processes_racing_on_identical_bytes_leave_one_sound_object(state_root, tmp_path):
    gate = tmp_path / "go"
    command = [sys.executable, "-c", PREAMBLE + textwrap.dedent(WORKER), str(state_root), str(gate)]
    env = {**os.environ, "PYTHONPATH": SOURCE_ROOT}
    procs = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(4)]  # fmt: skip
    time.sleep(2.0)  # let every interpreter reach the gate
    gate.touch()
    outputs = [p.communicate(timeout=WAIT) for p in procs]
    assert [p.returncode for p in procs] == [0, 0, 0, 0], [o[1] for o in outputs]
    assert Counter(o[0].strip() for o in outputs) == {"True": 1, "False": 3}
    report = scan_objects(CasLayout.for_state_root(state_root))
    assert report.clean and report.verified == 1
    assert Quarantine(CasLayout.for_state_root(state_root)).orphans() == []
