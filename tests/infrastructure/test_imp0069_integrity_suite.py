"""IMP-0069 tests: persistence invariants hold over arbitrary histories, and damage is always noticed."""

from __future__ import annotations

import hashlib
import io
import itertools
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from persistence_support import tamper
from trinetra.domain.asset_admission import AdmissionProfileError, sniff_media_type
from trinetra.domain.asset_record import ActorId
from trinetra.domain.assets import AssetKind
from trinetra.domain.audit import AuditEventType, AuditOutcome
from trinetra.errors import TrinetraError
from trinetra.infrastructure.asset_admission_service import AdmissionRequest, AssetAdmissionService
from trinetra.infrastructure.asset_repository import AssetRepository
from trinetra.infrastructure.audit_coupling import run_audited
from trinetra.infrastructure.audit_repository import DEFAULT_JOURNAL, AuditDraft, AuditRepository
from trinetra.infrastructure.audit_verify import verify_chain
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import Quarantine
from trinetra.infrastructure.cas_read import read_object
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects
from trinetra.infrastructure.migration_runner import upgrade_to_head
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore

ACTOR = ActorId("core")
UNTYPED = st.binary(max_size=300).filter(lambda b: sniff_media_type(b[:16]) is None)
ZIP_AS_TEXT = b"PK\x03\x04" + b"\x00" * 24


def request(kind=AssetKind.INFERENCE_OUTPUT, media="application/octet-stream", name="blob.bin"):
    return AdmissionRequest(kind=kind, media_type=media, supplied_name=name, created_by=ACTOR)


def success_draft():
    event = AuditEventType("asset_ingested")
    return AuditDraft(event_type=event, outcome=AuditOutcome.SUCCESS, created_by=ACTOR)


def failure_draft():
    event = AuditEventType("asset_rejected")
    return AuditDraft(event_type=event, outcome=AuditOutcome.FAILURE, created_by=ACTOR)


@contextmanager
def fresh_state():
    base = Path(tempfile.mkdtemp(prefix="trinetra-prop-"))
    root = base / "state"
    root.mkdir(mode=0o700)
    try:
        yield root
    finally:
        shutil.rmtree(base, ignore_errors=True)


class Node:
    """A running Core over one state root; can be stopped and started again (a restart)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = itertools.count(1)
        self.start()

    def start(self) -> None:
        self.store = CoreStore.open(DatabaseConfig(self.root))
        upgrade_to_head(self.store)
        self.layout = CasLayout.for_state_root(self.root)
        self.service = AssetAdmissionService(
            CasStore(self.layout), AssetRepository(), entropy=lambda n: next(self.counter).to_bytes(n, "big")
        )
        self.audit = AuditRepository(entropy=lambda n: (next(self.counter) + 10**9).to_bytes(n, "big"))

    def stop(self) -> None:
        self.store.close()

    def ingest(self, data: bytes, request_: AdmissionRequest):
        def work(uow):
            return self.service.admit(uow, io.BytesIO(data), request_), success_draft()

        return run_audited(self.store, self.audit, work, on_failure=failure_draft())


class PersistenceMachine(RuleBasedStateMachine):
    """Random histories of admissions, refusals and restarts; the invariants never change."""

    def __init__(self) -> None:
        super().__init__()
        self._base = Path(tempfile.mkdtemp(prefix="trinetra-machine-"))
        root = self._base / "state"
        root.mkdir(mode=0o700)
        self.node = Node(root)
        self.records: dict[str, bytes] = {}
        self.events = 0

    def teardown(self) -> None:
        self.node.stop()
        shutil.rmtree(self._base, ignore_errors=True)

    @rule(data=UNTYPED)
    def admit(self, data):
        result = self.node.ingest(data, request())
        self.records[str(result.value.record.record_id)] = data
        self.events += 1

    @rule()
    def admit_a_refused_upload(self):
        with pytest.raises(AdmissionProfileError):
            self.node.ingest(ZIP_AS_TEXT, request(AssetKind.CONTRIBUTOR_METADATA, "text/plain", "n.txt"))
        self.events += 1  # the failure is audited

    @rule()
    def restart(self):
        self.node.stop()
        self.node.start()

    @invariant()
    def records_match_the_model_and_read_back_exactly(self):
        with self.node.store.unit_of_work() as uow:
            repository = AssetRepository()
            assert repository.count(uow) == len(self.records)
            for record_id, data in self.records.items():
                from trinetra.domain.assets import AssetId

                record = repository.get(uow, AssetId.parse(record_id))
                assert record is not None and record.size_bytes == len(data)
                assert record.blob_digest.hex == hashlib.sha256(data).hexdigest()
                assert read_object(self.node.layout, record.blob_digest, max_bytes=1000) == data
            uow.rollback()

    @invariant()
    def the_cas_holds_exactly_the_distinct_contents_and_nothing_else(self):
        report = scan_objects(self.node.layout)
        assert report.clean
        assert report.verified == len({hashlib.sha256(d).digest() for d in self.records.values()})
        assert Quarantine(self.node.layout).orphans() == []

    @invariant()
    @precondition(lambda self: self.events > 0)
    def the_audit_chain_is_intact_and_complete(self):
        with self.node.store.unit_of_work() as uow:
            head = self.node.audit.head(uow)
            report = verify_chain(uow, head.journal_id)
            uow.rollback()
        assert report.intact and report.events_checked == self.events == head.sequence


TestPersistenceMachine = PersistenceMachine.TestCase
TestPersistenceMachine.settings = settings(
    max_examples=12, stateful_step_count=10, deadline=None, suppress_health_check=list(HealthCheck)
)


# ------------------------------------------------------------------ single-fact properties
@settings(max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
@given(data=UNTYPED)
def test_admitting_then_verifying_agrees_with_hashlib_for_any_bytes(data):
    with fresh_state() as root:
        node = Node(root)
        try:
            result = node.ingest(data, request())
            digest = result.value.record.blob_digest
            assert digest.hex == hashlib.sha256(data).hexdigest()
            assert read_object(node.layout, digest, max_bytes=1000) == data
            assert scan_objects(node.layout).clean
        finally:
            node.stop()


def _flip(column: str) -> str:
    flipped = f"CASE substr({column}, 71, 1) WHEN '0' THEN '1' ELSE '0' END"
    head = f"substr({column}, 1, 70)"
    return f"UPDATE audit_events SET {column} = {head} || {flipped} WHERE journal_sequence = ?"  # noqa: S608


CORRUPTIONS = {
    "body_json": "UPDATE audit_events SET body_json = replace(body_json, 'step_', 'stap_') "
                 "WHERE journal_sequence = ?",
    "body_digest": _flip("body_digest"),
    "prev_link": _flip("prev_link_digest"),
    "link": _flip("link_digest"),
    "event_type": "UPDATE audit_events SET event_type = 'tampered' WHERE journal_sequence = ?",
    "outcome": "UPDATE audit_events SET outcome = 'FAILURE' WHERE journal_sequence = ?",
    "created_at": "UPDATE audit_events SET created_at = '2027-01-01T00:00:00Z' "
                  "WHERE journal_sequence = ?",
    "sequence": "UPDATE audit_events SET journal_sequence = journal_sequence + 100 "
                "WHERE journal_sequence = ?",
    "delete": "DELETE FROM audit_events WHERE journal_sequence = ?",
    "context": "UPDATE audit_event_contexts SET context_id = 'forged' WHERE event_id IN "
               "(SELECT record_id FROM audit_events WHERE journal_sequence = ?)",
}  # fmt: skip


def chain_is_intact(node: Node) -> bool:
    """True only when the chain verifies; a refusal by the verifier counts as detection."""
    try:
        with node.store.unit_of_work() as uow:
            report = verify_chain(uow, DEFAULT_JOURNAL)
            uow.rollback()
    except TrinetraError:
        return False
    return report.intact


@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
@given(length=st.integers(2, 5), data=st.data(), column=st.sampled_from(sorted(CORRUPTIONS)))
def test_damaging_any_single_stored_fact_of_any_event_is_always_detected(length, data, column):
    target = data.draw(st.integers(1, length))
    with fresh_state() as root:
        node = Node(root)
        try:
            for n in range(length):
                def work(uow, n=n):
                    draft = AuditDraft(
                        event_type=AuditEventType(f"step_{n}"), outcome=AuditOutcome.SUCCESS,
                        created_by=ACTOR, context_ids=(f"ctx{n}",),
                    )  # fmt: skip
                    return None, draft

                run_audited(node.store, node.audit, work, on_failure=None)
            assert chain_is_intact(node)  # the control: undamaged histories verify
            tamper(node.store, (CORRUPTIONS[column], (target,)))
            assert not chain_is_intact(node), (column, target, length)
        finally:
            node.stop()
