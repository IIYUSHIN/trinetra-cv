"""IMP-0063 tests: the verifier reports exactly what is wrong, bounded, and never repairs."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from persistence_support import tamper
from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import AuditEventType, AuditOutcome, JournalId, ObjectRef, ObjectType
from trinetra.domain.audit_link import genesis_link
from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure import audit_verify
from trinetra.infrastructure.audit_repository import (
    DEFAULT_JOURNAL,
    AuditDraft,
    AuditIntegrityError,
    AuditRepository,
)
from trinetra.infrastructure.audit_verify import (
    EVENT_INVALID,
    GAP,
    LINK_BROKEN,
    MAX_PROBLEMS,
    STATE_MISMATCH,
    STATE_MISSING,
    head_digest,
    journal_ids,
    verify_all,
    verify_chain,
)
from trinetra.timeutil import FixedClock

OTHER = JournalId("other_journal")
ZERO = "sha256:" + "0" * 64


NUMBERS = itertools.count(1)  # shared so separate repositories never mint the same event id


def repo():
    clock = FixedClock(datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC))
    return AuditRepository(clock=clock, entropy=lambda n: next(NUMBERS).to_bytes(n, "big"))


def fill(store, count, journal=DEFAULT_JOURNAL, repository=None):
    repository = repository or repo()
    subject = ObjectRef(
        object_type=ObjectType("asset_record"), object_id="asset_1", record_digest=DigestSha256.of(b"1")
    )
    events = []
    for n in range(count):
        with store.unit_of_work() as uow:
            events.append(
                repository.append(
                    uow,
                    AuditDraft(
                        event_type=AuditEventType(f"step_{n}"),
                        outcome=AuditOutcome.SUCCESS,
                        created_by=ActorId("core"),
                        subject_refs=(subject,),
                        context_ids=(f"ctx{n}",),
                        journal_id=journal,
                    ),
                )
            )
            uow.commit()
    return events


def verify(store, journal=DEFAULT_JOURNAL):
    with store.unit_of_work() as uow:
        report = verify_chain(uow, journal)
        uow.rollback()
    return report


def kinds(report):
    return [p.kind for p in report.problems]


# ------------------------------------------------------------------------------- intact chains
def test_an_empty_journal_with_no_state_is_intact(store):
    report = verify(store)
    assert report.intact and report.events_checked == 0 and report.head_sequence == 0
    assert report.head_link_digest is None


def test_an_intact_chain_reports_its_head(store, raw):
    fill(store, 5)
    report = verify(store)
    assert report.intact and report.events_checked == 5 and report.head_sequence == 5
    stored = raw.rows("SELECT link_digest FROM audit_events WHERE journal_sequence = 5")
    assert report.head_link_digest == stored[0][0]
    assert report.problems == () and report.truncated is False
    assert head_digest(report).to_wire() == report.head_link_digest


def test_paging_inside_the_verifier_does_not_skip_or_repeat(store, monkeypatch):
    monkeypatch.setattr(audit_verify, "PAGE", 2)
    fill(store, 7)
    report = verify(store)
    assert report.intact and report.events_checked == 7 and report.head_sequence == 7


def test_journals_verify_independently(store):
    fill(store, 3)
    fill(store, 2, journal=OTHER)
    assert verify(store).events_checked == 3 and verify(store, OTHER).events_checked == 2
    with store.unit_of_work() as uow:
        assert journal_ids(uow) == (DEFAULT_JOURNAL, OTHER)  # name order: local_main < other_journal
        reports = verify_all(uow)
        uow.rollback()
    assert [r.events_checked for r in reports] == [3, 2] and all(r.intact for r in reports)


def test_an_unknown_journal_is_empty_not_an_error(store):
    fill(store, 1)
    assert verify(store, JournalId("never_used")).intact


# ------------------------------------------------------------------------------- damage reports
def test_a_missing_middle_event_is_a_gap(store):
    fill(store, 5)
    tamper(store, "DELETE FROM audit_event_subjects WHERE event_id IN "
                  "(SELECT record_id FROM audit_events WHERE journal_sequence = 3)",
           "DELETE FROM audit_event_contexts WHERE event_id IN "
           "(SELECT record_id FROM audit_events WHERE journal_sequence = 3)",
           "DELETE FROM audit_events WHERE journal_sequence = 3")  # fmt: skip
    report = verify(store)
    assert not report.intact
    assert GAP in kinds(report) and LINK_BROKEN in kinds(report)
    assert [p.sequence for p in report.problems if p.kind == GAP] == [4]  # reported once, then resynced
    assert report.events_checked == 4 and report.head_sequence == 5


def test_a_dropped_tail_is_a_state_mismatch(store):
    fill(store, 4)
    tamper(store, "DELETE FROM audit_event_subjects WHERE event_id IN "
                  "(SELECT record_id FROM audit_events WHERE journal_sequence = 4)",
           "DELETE FROM audit_event_contexts WHERE event_id IN "
           "(SELECT record_id FROM audit_events WHERE journal_sequence = 4)",
           "DELETE FROM audit_events WHERE journal_sequence = 4")  # fmt: skip
    report = verify(store)
    assert kinds(report) == [STATE_MISMATCH] and report.head_sequence == 3


def test_an_edited_event_is_reported_with_its_sequence(store):
    fill(store, 3)
    tamper(store, ("UPDATE audit_events SET body_digest = ? WHERE journal_sequence = 2", (ZERO,)))
    report = verify(store)
    assert [(p.kind, p.sequence) for p in report.problems] == [(EVENT_INVALID, 2)]


def test_a_rewritten_link_breaks_the_next_event(store):
    fill(store, 3)
    tamper(store, ("UPDATE audit_events SET link_digest = ? WHERE journal_sequence = 1", (ZERO,)))
    report = verify(store)
    assert (EVENT_INVALID, 1) in [(p.kind, p.sequence) for p in report.problems]
    assert (LINK_BROKEN, 2) in [(p.kind, p.sequence) for p in report.problems]


def test_a_first_event_not_chained_to_genesis_is_reported(store):
    fill(store, 2)
    other = genesis_link(JournalId("someone_else")).to_wire()
    tamper(store, ("UPDATE audit_events SET prev_link_digest = ? WHERE journal_sequence = 1", (other,)))
    report = verify(store)
    assert (LINK_BROKEN, 1) in [(p.kind, p.sequence) for p in report.problems]


def test_state_ahead_of_events_and_state_behind_events_are_both_mismatches(store):
    fill(store, 3)
    tamper(store, "UPDATE journal_state SET next_sequence = 9")
    assert kinds(verify(store)) == [STATE_MISMATCH]
    tamper(store, "UPDATE journal_state SET next_sequence = 2")
    assert kinds(verify(store)) == [STATE_MISMATCH]


def test_events_without_a_state_row_are_reported(store):
    fill(store, 2)
    tamper(store, "DELETE FROM journal_state")
    assert kinds(verify(store)) == [STATE_MISSING]


def test_state_without_events_is_a_mismatch(store):
    fill(store, 1)
    tamper(store, "DELETE FROM audit_event_subjects", "DELETE FROM audit_event_contexts",
           "DELETE FROM audit_events")  # fmt: skip
    assert kinds(verify(store)) == [STATE_MISMATCH]


def test_a_tampered_side_row_is_an_invalid_event(store):
    fill(store, 2)
    tamper(store, "UPDATE audit_event_contexts SET context_id = 'forged' WHERE ordinal = 0")
    assert {p.kind for p in verify(store).problems} == {EVENT_INVALID}


def test_the_verifier_never_repairs_what_it_reports(store):
    fill(store, 3)
    tamper(store, "UPDATE journal_state SET next_sequence = 9")
    first, second = verify(store), verify(store)
    assert first == second and not first.intact


def test_a_damaged_journal_does_not_taint_another(store):
    fill(store, 2)
    fill(store, 2, journal=OTHER)
    tamper(store, "UPDATE audit_events SET body_json = '{}' WHERE journal_id = 'local_main'")
    assert not verify(store).intact and verify(store, OTHER).intact


def test_head_digest_is_withheld_for_a_damaged_journal(store):
    fill(store, 2)
    tamper(store, "UPDATE journal_state SET next_sequence = 9")
    assert head_digest(verify(store)) is None


def test_the_problem_list_is_bounded_and_says_so(store, monkeypatch):
    fill(store, 6)
    monkeypatch.setattr(audit_verify, "MAX_PROBLEMS", 2)
    tamper(store, "UPDATE audit_events SET body_digest = 'sha256:' || hex(zeroblob(32))")
    report = verify(store)
    assert len(report.problems) == 2 and report.truncated is True
    assert report.events_checked == 6  # the walk still completed


def test_the_proposed_limits_are_pinned():
    assert (MAX_PROBLEMS, audit_verify.PAGE) == (100, 200)


def test_a_malformed_journal_name_in_storage_is_a_typed_error(store):
    fill(store, 1)
    tamper(store, "UPDATE journal_state SET journal_id = 'bad__name'",
           "UPDATE audit_events SET journal_id = 'bad__name'")  # fmt: skip
    with store.unit_of_work() as uow:
        with pytest.raises(AuditIntegrityError, match="journal id"):
            journal_ids(uow)
        uow.rollback()
