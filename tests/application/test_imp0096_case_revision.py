"""IMP-0096 tests: a Case changes only against the revision the caller saw; a stale caller loses cleanly."""

from __future__ import annotations

import pytest

from intake_support import FrozenClock, MemoryCaseStore
from projection_support import CASE_ID, T0, T1, make_case
from trinetra.application.cases.revision import CaseUpdateService
from trinetra.application.errors import (
    ApplicationConflictError,
    CaseChangeInvalidError,
    StaleCaseRevisionError,
    UnknownCaseError,
)
from trinetra.domain.case import CaseId, CaseStateRevision


def touch(case, now):
    return case.touch(now)


def rig(case=None, now=T1):
    store = MemoryCaseStore()
    case = case or make_case()
    store.cases[case.case_id] = case
    return store, CaseUpdateService(store, FrozenClock(now)), case


def test_a_fresh_caller_advances_the_revision_by_exactly_one():
    store, service, case = rig()
    updated = service.update(CASE_ID, case.case_state_revision, touch)
    assert updated.case_state_revision.value == 2
    assert updated.updated_at == T1
    assert store.cases[CASE_ID] == updated
    assert store.saves == [(updated, case.case_state_revision)]


def test_the_change_receives_the_stored_case_and_the_clock_time():
    seen = []
    _, service, case = rig()

    def change(stored, now):
        seen.append((stored, now))
        return stored.touch(now)

    service.update(CASE_ID, case.case_state_revision, change)
    assert seen == [(case, T1)]


def test_successive_updates_each_need_the_new_revision():
    _, service, case = rig()
    second = service.update(CASE_ID, case.case_state_revision, touch)
    third = service.update(CASE_ID, second.case_state_revision, touch)
    assert (second.case_state_revision.value, third.case_state_revision.value) == (2, 3)


def test_a_stale_caller_is_refused_with_both_revisions_and_nothing_is_written():
    store, service, case = rig()
    service.update(CASE_ID, case.case_state_revision, touch)
    saved_before = list(store.saves)
    with pytest.raises(StaleCaseRevisionError) as error:
        service.update(CASE_ID, case.case_state_revision, touch)  # still holds revision 1
    assert error.value.code == "CASE_REVISION_STALE"
    assert error.value.details == {"case_id": str(CASE_ID), "expected_revision": 1, "actual_revision": 2}
    assert store.saves == saved_before
    assert store.cases[CASE_ID].case_state_revision.value == 2


def test_a_caller_claiming_a_future_revision_is_refused_too():
    store, service, _ = rig()
    with pytest.raises(StaleCaseRevisionError) as error:
        service.update(CASE_ID, CaseStateRevision(5), touch)
    assert error.value.details["expected_revision"] == 5
    assert error.value.details["actual_revision"] == 1
    assert store.saves == []


def test_a_stale_refusal_is_an_application_conflict():
    assert issubclass(StaleCaseRevisionError, ApplicationConflictError)


def test_an_unknown_case_is_reported_as_unknown_not_as_stale():
    store, service, _ = rig()
    other = CaseId.from_entropy(lambda size: b"\x44" * size)
    with pytest.raises(UnknownCaseError) as error:
        service.update(other, CaseStateRevision.first(), touch)
    assert error.value.details == {"case_id": str(other)}
    assert store.saves == []


def test_a_writer_that_slips_in_after_the_check_makes_this_caller_lose_at_the_store():
    store, service, case = rig()

    def change(stored, now):
        store.cases[CASE_ID] = stored.touch(T0)  # another writer commits while we compute
        return stored.touch(now)

    with pytest.raises(StaleCaseRevisionError) as error:
        service.update(CASE_ID, case.case_state_revision, change)
    assert error.value.details["actual_revision"] == 2
    assert store.cases[CASE_ID].case_state_revision.value == 2  # their write stands; ours did not land


@pytest.mark.parametrize("bad", ["same", "skip", "other_case"])
def test_a_change_that_does_not_produce_the_next_revision_of_the_same_case_is_refused(bad):
    store, service, case = rig()

    def change(stored, now):
        if bad == "same":
            return stored
        if bad == "skip":
            return stored.touch(now).touch(now)
        return make_case().evolve(case_id=CaseId.from_entropy(lambda size: b"\x44" * size)).touch(now)

    with pytest.raises(CaseChangeInvalidError):
        service.update(CASE_ID, case.case_state_revision, change)
    assert store.saves == []
    assert store.cases[CASE_ID] == case


def test_an_exception_in_the_change_writes_nothing():
    store, service, case = rig()

    def change(_stored, _now):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        service.update(CASE_ID, case.case_state_revision, change)
    assert store.saves == []
