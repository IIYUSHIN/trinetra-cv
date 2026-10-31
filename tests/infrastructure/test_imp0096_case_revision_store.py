"""IMP-0096 tests: revision conflicts over the real database - one writer wins, the loser changes nothing."""

from __future__ import annotations

import threading

import pytest

from commit_support import commit_minimal
from trinetra.application.cases.revision import CaseUpdateService
from trinetra.application.errors import StaleCaseRevisionError, UnknownCaseError
from trinetra.domain.assurance_case import CaseInvariantError
from trinetra.domain.audit import AuditOutcome
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.case_repository import CaseRepository
from trinetra.infrastructure.case_store import AuditedCaseStore


def touch(case, now):
    return case.touch(now)


def started(rig):
    _d, _m, result = commit_minimal(rig)
    return result.case, CaseUpdateService(rig.cases, rig.clock)


def test_an_update_persists_the_next_revision_and_audits_it(rig):
    case, service = started(rig)
    updated = service.update(case.case_id, case.case_state_revision, touch)
    assert rig.cases.get(case.case_id) == updated
    assert updated.case_state_revision.value == 2
    events, report = rig.events()
    last = events[-1]
    assert (last.event_type.text, last.outcome) == ("case_revised", AuditOutcome.SUCCESS)
    assert last.subject_refs[0].record_digest == DigestSha256.parse(updated.canonical_digest())
    assert last.context_ids == (str(case.case_id),)
    assert [(d.key.text, d.value) for d in last.details] == [("revision", 2)]
    assert report.intact


def test_links_and_identity_survive_a_revision_unchanged(rig):
    case, service = started(rig)
    updated = service.update(case.case_id, case.case_state_revision, touch)
    stored = rig.cases.get(case.case_id)
    assert stored.asset_links == case.asset_links
    assert stored.created_at == case.created_at
    assert stored.lifecycle_state is CaseLifecycleState.PREFLIGHT
    assert stored == updated


def test_a_stale_caller_gets_both_revisions_and_the_database_keeps_the_winner(rig):
    case, service = started(rig)
    winner = service.update(case.case_id, case.case_state_revision, touch)
    with pytest.raises(StaleCaseRevisionError) as error:
        service.update(case.case_id, case.case_state_revision, touch)
    assert error.value.details == {
        "case_id": str(case.case_id), "expected_revision": 1, "actual_revision": 2}
    assert rig.cases.get(case.case_id) == winner


def test_the_store_itself_refuses_a_stale_save_even_without_the_service_check(rig):
    case, _ = started(rig)
    first = case.touch(rig.clock.now())
    rig.cases.save(first, expected_revision=case.case_state_revision)
    competing = case.touch(rig.clock.now())  # also built from revision 1
    with pytest.raises(StaleCaseRevisionError) as error:
        rig.cases.save(competing, expected_revision=case.case_state_revision)
    assert error.value.details["actual_revision"] == 2
    assert error.value.details["expected_revision"] == 1
    assert rig.cases.get(case.case_id) == first
    events, report = rig.events()
    last = events[-1]
    assert (last.event_type.text, last.outcome) == ("case_revision_failed", AuditOutcome.FAILURE)
    assert last.reason_code == "CASE_REVISION_CONFLICT"
    assert [(d.key.text, d.value) for d in last.details] == [("revision", 1)]
    assert report.intact


def test_saving_a_case_that_does_not_exist_is_reported_as_unknown(rig):
    case, _ = started(rig)
    ghost = case.evolve(case_id=CaseId.from_entropy(lambda size: b"\x77" * size)).touch(rig.clock.now())
    with pytest.raises(UnknownCaseError):
        rig.cases.save(ghost, expected_revision=case.case_state_revision)


def test_a_lifecycle_change_is_refused_and_audited(rig):
    case, _ = started(rig)
    moved = case.touch(rig.clock.now()).evolve(lifecycle_state=CaseLifecycleState.ASSESSING)
    with pytest.raises(CaseInvariantError) as error:
        rig.cases.save(moved, expected_revision=case.case_state_revision)
    assert error.value.code == "CASE_INVARIANT_VIOLATED"
    assert rig.cases.get(case.case_id) == case
    events, _ = rig.events()
    assert events[-1].event_type.text == "case_revision_failed"
    assert events[-1].reason_code == "CASE_INVARIANT_VIOLATED"


def test_a_revision_that_skips_a_number_is_refused_by_the_store(rig):
    case, _ = started(rig)
    skipped = case.touch(rig.clock.now()).touch(rig.clock.now())
    with pytest.raises(StaleCaseRevisionError):
        rig.cases.save(skipped, expected_revision=case.case_state_revision)
    assert rig.cases.get(case.case_id) == case


def test_many_writers_racing_from_the_same_revision_produce_exactly_one_winner(rig):
    case, service = started(rig)
    outcomes = []
    barrier = threading.Barrier(8)

    def writer():
        barrier.wait()
        try:
            outcomes.append(("won", service.update(case.case_id, case.case_state_revision, touch)))
        except StaleCaseRevisionError as exc:
            outcomes.append(("lost", exc))

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(kind for kind, _ in outcomes) == ["lost"] * 7 + ["won"]
    assert rig.cases.get(case.case_id).case_state_revision == CaseStateRevision(2)
    events, report = rig.events()
    assert [e.event_type.text for e in events].count("case_revised") == 1
    assert report.intact


def test_revisions_advance_one_at_a_time_across_many_updates(rig):
    case, service = started(rig)
    current = case
    for expected in range(2, 8):
        current = service.update(case.case_id, current.case_state_revision, touch)
        assert current.case_state_revision.value == expected
    assert rig.cases.get(case.case_id).case_state_revision.value == 7


def test_the_repository_and_the_store_agree_on_what_is_stored(rig, store):
    case, service = started(rig)
    service.update(case.case_id, case.case_state_revision, touch)
    with store.unit_of_work() as uow:
        direct = CaseRepository().require(uow, case.case_id)
        uow.rollback()
    assert direct == rig.cases.get(case.case_id)
    assert isinstance(rig.cases, AuditedCaseStore)
