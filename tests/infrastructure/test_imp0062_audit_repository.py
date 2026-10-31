"""IMP-0062 tests: appends chain correctly, reads never trust storage, nothing can be rewritten."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from persistence_support import tamper
from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import (
    AuditDetail,
    AuditEventId,
    AuditEventType,
    AuditOutcome,
    DetailKey,
    JournalId,
    ObjectRef,
    ObjectType,
)
from trinetra.domain.audit_link import body_digest, genesis_link, link_digest
from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.audit_repository import (
    DEFAULT_JOURNAL,
    DEFAULT_PAGE,
    MAX_PAGE,
    AuditDraft,
    AuditExistsError,
    AuditIntegrityError,
    AuditQueryError,
    AuditRepository,
)
from trinetra.timeutil import FixedClock

OTHER = JournalId("other_journal")


def entropy():
    numbers = itertools.count(1)
    return lambda n: next(numbers).to_bytes(n, "big")


def repo(**kw):
    clock = FixedClock(datetime(2026, 10, 31, 8, 0, 0, 999999, tzinfo=UTC))
    return AuditRepository(clock=kw.pop("clock", clock), entropy=kw.pop("entropy", entropy()))


def draft(kind="asset_ingested", **kw):
    base = {
        "event_type": AuditEventType(kind),
        "outcome": AuditOutcome.SUCCESS,
        "created_by": ActorId("core"),
    }
    return AuditDraft(**(base | kw))


def subject(n=1):
    return ObjectRef(
        object_type=ObjectType("asset_record"),
        object_id=f"asset_{n:032x}",
        record_digest=DigestSha256.of(bytes([n])),
    )


def append(store, repository, **kw):
    with store.unit_of_work() as uow:
        event = repository.append(uow, draft(**kw))
        uow.commit()
    return event


def head(store, repository, journal=DEFAULT_JOURNAL):
    with store.unit_of_work() as uow:
        value = repository.head(uow, journal)
        uow.rollback()
    return value


# --------------------------------------------------------------------------------- append
def test_the_first_event_is_sequence_one_and_chains_to_genesis(store, raw):
    event = append(store, repo())
    assert event.journal_sequence == 1 and event.journal_id == DEFAULT_JOURNAL
    assert event.created_at.to_wire() == "2026-10-31T08:00:00Z"  # whole seconds
    row = raw.rows("SELECT prev_link_digest, body_digest, link_digest FROM audit_events")[0]
    assert row[0] == genesis_link(DEFAULT_JOURNAL).to_wire()
    assert row[1] == body_digest(event).to_wire()
    assert row[2] == link_digest(genesis_link(DEFAULT_JOURNAL), body_digest(event)).to_wire()


def test_events_are_gapless_and_each_links_to_its_predecessor(store, raw):
    repository = repo()
    events = [append(store, repository, kind=f"step_{n}") for n in range(1, 6)]
    assert [e.journal_sequence for e in events] == [1, 2, 3, 4, 5]
    rows = raw.rows("SELECT journal_sequence, prev_link_digest, link_digest FROM audit_events ORDER BY 1")
    for before, after in itertools.pairwise(rows):
        assert after[1] == before[2]
    assert raw.rows("SELECT next_sequence FROM journal_state") == [(6,)]


def test_journals_are_independent_chains(store, raw):
    repository = repo()
    a1 = append(store, repository)
    b1 = append(store, repository, journal_id=OTHER)
    a2 = append(store, repository)
    assert (a1.journal_sequence, b1.journal_sequence, a2.journal_sequence) == (1, 1, 2)
    rows = dict(raw.rows("SELECT journal_id, count(*) FROM audit_events GROUP BY journal_id"))
    assert rows == {"local_main": 2, "other_journal": 1}
    assert head(store, repository, OTHER).sequence == 1


def test_subjects_contexts_details_and_failure_reason_are_stored_with_the_event(store, raw):
    repository = repo()
    event = append(
        store,
        repository,
        outcome=AuditOutcome.FAILURE,
        reason_code="CAS_COMMIT_FAILED",
        subject_refs=(subject(2), subject(1)),
        context_ids=("case_x", "case_y"),
        details=(AuditDetail(key=DetailKey("attempt"), value=3),),
    )
    with store.unit_of_work() as uow:
        assert repository.get(uow, event.record_id) == event
        uow.rollback()
    assert [r[0] for r in raw.rows("SELECT ordinal FROM audit_event_subjects ORDER BY ordinal")] == [0, 1]
    contexts = raw.rows("SELECT context_id FROM audit_event_contexts ORDER BY ordinal")
    assert contexts == [("case_x",), ("case_y",)]


def test_an_uncommitted_append_leaves_no_trace_and_does_not_burn_a_sequence(store, raw):
    repository = repo()
    with store.unit_of_work() as uow:
        repository.append(uow, draft())
        uow.rollback()
    assert raw.rows("SELECT count(*) FROM audit_events") == [(0,)]
    assert raw.rows("SELECT count(*) FROM journal_state") == [(0,)]
    assert append(store, repository).journal_sequence == 1


def test_an_event_id_collision_fails_loudly(store):
    repository = repo(entropy=lambda n: b"\x05" * n)
    append(store, repository)
    with pytest.raises(AuditExistsError):
        append(store, repository)
    assert head(store, repository).sequence == 1


# ------------------------------------------------------------------------ immutability
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_events SET outcome = 'FAILURE'",
        "DELETE FROM audit_events",
        "UPDATE audit_event_subjects SET object_id = 'x'",
        "DELETE FROM audit_event_contexts",
    ],
)
def test_stored_events_cannot_be_changed_or_removed_even_by_raw_sql(store, raw, sql):
    append(store, repo(), subject_refs=(subject(),), context_ids=("c1",))
    assert raw.attempt(sql) != ""


def test_the_repository_exposes_no_way_to_change_or_delete():
    assert not [n for n in dir(AuditRepository) if n.startswith(("update", "delete", "remove", "replace"))]


# ------------------------------------------------------------------------------ reading
def test_get_returns_none_for_an_unknown_event(store):
    with store.unit_of_work() as uow:
        assert repo().get(uow, AuditEventId.from_entropy(lambda n: b"\x09" * n)) is None
        uow.rollback()


def test_head_and_count_track_the_journal(store):
    repository = repo()
    assert head(store, repository) is None
    events = [append(store, repository) for _ in range(3)]
    value = head(store, repository)
    assert (value.sequence, value.journal_id) == (3, DEFAULT_JOURNAL)
    with store.unit_of_work() as uow:
        assert repository.count(uow) == 3 and repository.count(uow, OTHER) == 0
        uow.rollback()
    assert events[-1].journal_sequence == 3


def test_paging_walks_the_journal_in_order_without_gaps(store):
    repository = repo()
    appended = [append(store, repository) for _ in range(7)]
    seen, after = [], 0
    for _ in range(10):  # hard cap: a paging bug must fail, not hang
        with store.unit_of_work() as uow:
            page = repository.list_page(uow, after_sequence=after, limit=3)
            uow.rollback()
        seen.extend(page.events)
        if page.next_after is None:
            break
        after = page.next_after
    assert seen == appended


def test_a_full_last_page_has_no_continuation(store):
    repository = repo()
    for _ in range(3):
        append(store, repository)
    with store.unit_of_work() as uow:
        page = repository.list_page(uow, limit=3)
        uow.rollback()
    assert len(page.events) == 3 and page.next_after is None


@pytest.mark.parametrize(
    "kw",
    [
        {"limit": 0},
        {"limit": MAX_PAGE + 1},
        {"limit": True},
        {"after_sequence": -1},
        {"after_sequence": True},
    ],
)
def test_invalid_page_parameters_are_refused(store, kw):
    with store.unit_of_work() as uow:
        with pytest.raises(AuditQueryError):
            repo().list_page(uow, **kw)
        uow.rollback()


def test_the_page_ceiling_is_inclusive(store):
    with store.unit_of_work() as uow:
        assert repo().list_page(uow, limit=MAX_PAGE).events == ()
        uow.rollback()


# ------------------------------------------------------------------------------ tamper detection
def read_back(store, repository, event):
    with store.unit_of_work() as uow:
        try:
            return repository.get(uow, event.record_id)
        finally:
            uow.rollback()


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("UPDATE audit_events SET body_digest = 'sha256:' || hex(zeroblob(32))", "digest"),
        ("UPDATE audit_events SET link_digest = 'sha256:' || hex(zeroblob(32))", "link"),
        ("UPDATE audit_events SET prev_link_digest = 'sha256:' || hex(zeroblob(32))", "link"),
        ("UPDATE audit_events SET body_json = replace(body_json, 'SUCCESS', 'FAILURE')", "not a valid"),
        ("UPDATE audit_events SET body_json = replace(body_json, '08:00:00Z', '09:00:00Z')", "disagrees"),
        ("UPDATE audit_events SET body_json = body_json || ' '", "canonical"),
        ("UPDATE audit_events SET body_json = '{}'", "valid"),
        ("UPDATE audit_events SET body_digest = 'nonsense'", "digest"),
        ("UPDATE audit_events SET prev_link_digest = 'nonsense'", "malformed"),
        ("UPDATE audit_event_subjects SET object_id = 'asset_forged'", "subject"),
        ("DELETE FROM audit_event_subjects", "subject"),
        ("DELETE FROM audit_event_contexts", "context"),
        ("UPDATE audit_event_contexts SET context_id = 'case_forged'", "context"),
    ],
)
def test_a_row_edited_behind_the_repository_is_detected_on_read(store, sql, fragment):
    repository = repo()
    event = append(store, repository, subject_refs=(subject(),), context_ids=("c1",))
    tamper(store, sql)
    with pytest.raises(AuditIntegrityError, match=fragment):
        read_back(store, repository, event)


def test_a_tampered_head_is_not_silently_extended(store):
    repository = repo()
    append(store, repository)
    tamper(store, "UPDATE audit_events SET link_digest = 'sha256:' || hex(zeroblob(32))")
    with store.unit_of_work() as uow:
        with pytest.raises(AuditIntegrityError):
            repository.append(uow, draft())
        uow.rollback()


def test_journal_state_ahead_of_the_events_is_detected_on_append(store):
    repository = repo()
    append(store, repository)
    tamper(store, "UPDATE journal_state SET next_sequence = 5")
    with store.unit_of_work() as uow:
        with pytest.raises(AuditIntegrityError, match="out of step"):
            repository.append(uow, draft())
        uow.rollback()


def test_the_page_bounds_are_pinned_and_a_single_event_page_is_valid(store):
    assert (MAX_PAGE, DEFAULT_PAGE) == (500, 100)
    repository = repo()
    appended = [append(store, repository) for _ in range(2)]
    with store.unit_of_work() as uow:
        page = repository.list_page(uow, limit=1)
        uow.rollback()
    assert page.events == (appended[0],) and page.next_after == 1
