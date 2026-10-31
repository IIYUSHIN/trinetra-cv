"""IMP-0091 tests: the Reference repository against a real, migrated database."""

from __future__ import annotations

import threading

import pytest

from persistence_support import put_asset, tamper
from trinetra.domain.assets import AssetId
from trinetra.domain.reference_records import (
    ReasonCode,
    ReferenceAssuranceRecord,
    ReferenceHistory,
    ReferenceStanding,
    ReferenceState,
    ReferenceStateEvent,
)
from trinetra.domain.references import ReferenceEventId, ReferenceId, ReferencePurpose
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.reference_repository import (
    ReferenceAssetError,
    ReferenceExistsError,
    ReferenceIntegrityError,
    ReferenceNotFoundError,
    ReferenceQueryError,
    ReferenceRepository,
    ReferenceSequenceConflictError,
)

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
T2 = UtcTimestamp.parse("2026-10-31T10:01:00Z")
T3 = UtcTimestamp.parse("2026-10-31T11:00:00Z")
GT = ReferencePurpose("ground_truth")


def rid(n=1):
    return ReferenceId.from_entropy(lambda s: bytes([n]) * s)


def eid(n=1):
    return ReferenceEventId.from_entropy(lambda s: bytes([n]) * s)


@pytest.fixture
def repo():
    return ReferenceRepository()


@pytest.fixture
def ref_assets(raw):
    return [AssetId.parse(put_asset(raw, i, "reference")) for i in range(1, 5)]


def record(n, asset, *, purpose=GT, start=T0, until=None):
    return ReferenceAssuranceRecord(reference_id=rid(n), asset_id=asset, purpose=purpose,
                                    authority="Harbour QA board", valid_from=start, valid_until=until,
                                    created_at=T0)  # fmt: skip


def first_event(n, at=T0):
    return ReferenceHistory.begin(eid(n * 10), rid(n), at).events[0]


def create(store, repo, n, asset, **kw):
    with store.unit_of_work() as uow:
        repo.add(uow, record(n, asset, **kw), first_event(n))
        uow.commit()


def append(store, repo, event, expected):
    with store.unit_of_work() as uow:
        repo.append_state_event(uow, event, expected)
        uow.commit()


def read(store, fn):
    with store.unit_of_work() as uow:
        out = fn(uow)
        uow.rollback()
    return out


def revoke_event(n, at=T1, **kw):
    return ReferenceHistory.begin(eid(n * 10), rid(n), T0).revoke(eid(n * 10 + 1), at, **kw).events[1]


# ------------------------------------------------------------------------------ create and read
def test_a_reference_round_trips_with_its_history(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0], until=T3)
    got = read(store, lambda u: repo.require(u, rid(1)))
    assert got == record(1, ref_assets[0], until=T3)
    hist = read(store, lambda u: repo.history(u, rid(1)))
    assert hist.recorded_state is ReferenceState.ACTIVE and len(hist.events) == 1


def test_unknown_references_are_none_or_not_found(store, repo):
    assert read(store, lambda u: repo.get(u, rid(9))) is None
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceNotFoundError) as exc:
            repo.require(uow, rid(9))
        assert exc.value.details["reference_id"] == str(rid(9))
        with pytest.raises(ReferenceNotFoundError):
            repo.history(uow, rid(9))
        with pytest.raises(ReferenceNotFoundError):
            repo.current_state(uow, rid(9), T1)
        uow.rollback()


def test_a_duplicate_reference_is_refused(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceExistsError):
            repo.add(uow, record(1, ref_assets[1]), first_event(1))
        uow.rollback()


def test_the_asset_must_exist_and_be_of_kind_reference(store, repo, raw):
    dataset = AssetId.parse(put_asset(raw, 20, "dataset"))
    ghost = AssetId.from_entropy(lambda s: b"\x55" * s)
    with store.unit_of_work() as uow:
        for bad in (dataset, ghost):
            with pytest.raises(ReferenceAssetError) as exc:
                repo.add(uow, record(1, bad), first_event(1))
            assert exc.value.details["asset_id"] == str(bad)
        uow.rollback()
    assert raw.rows("SELECT count(*) FROM reference_records") == [(0,)]


def test_the_initial_event_must_be_sequence_one_of_the_same_reference(store, repo, ref_assets):
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceAssetError, match="sequence 1"):
            repo.add(uow, record(1, ref_assets[0]), first_event(2))  # another reference's event
        with pytest.raises(ReferenceAssetError, match="sequence 1"):
            repo.add(uow, record(1, ref_assets[0]), revoke_event(1))  # sequence 2
        uow.rollback()


def test_uploading_a_reference_asset_alone_creates_no_reference(store, repo, ref_assets, raw):
    assert raw.rows("SELECT count(*) FROM asset_records WHERE asset_kind = 'reference'") == [(4,)]
    assert read(store, lambda u: repo.find_active_by_purpose(u, GT, T1)) == ()


def test_record_and_first_event_are_stored_atomically(store, repo, ref_assets, raw):
    with store.unit_of_work() as uow:
        repo.add(uow, record(1, ref_assets[0]), first_event(1))
        uow.rollback()
    assert raw.rows("SELECT count(*) FROM reference_records") == [(0,)]
    assert raw.rows("SELECT count(*) FROM reference_state_events") == [(0,)]


# ------------------------------------------------------------------------------ state events
def test_a_reference_can_be_revoked_and_the_history_shows_it(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    append(store, repo, revoke_event(1, reason_code=ReasonCode("withdrawn"), reason_text="board decision"), 1)
    hist = read(store, lambda u: repo.history(u, rid(1)))
    assert hist.recorded_state is ReferenceState.REVOKED
    assert hist.events[1].reason_text == "board decision"
    assert hist.events[1].reason_code == ReasonCode("withdrawn")
    assert read(store, lambda u: repo.current_state(u, rid(1), T2)) is ReferenceStanding.REVOKED


def test_supersession_stores_the_successor(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    create(store, repo, 2, ref_assets[1])
    event = ReferenceHistory.begin(eid(10), rid(1), T0).supersede(eid(11), T1, rid(2)).events[1]
    append(store, repo, event, 1)
    hist = read(store, lambda u: repo.history(u, rid(1)))
    assert hist.events[1].superseded_by_reference_id == rid(2)
    assert read(store, lambda u: repo.current_state(u, rid(1), T2)) is ReferenceStanding.SUPERSEDED


def test_a_stale_writer_cannot_fork_the_history(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    append(store, repo, revoke_event(1), 1)
    again = revoke_event(1)
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceSequenceConflictError) as exc:
            repo.append_state_event(uow, again, 1)  # still believes sequence 1 is the last
        assert exc.value.details == {"expected": 1, "actual": 2, "new": 2}
        uow.rollback()
    assert len(read(store, lambda u: repo.history(u, rid(1))).events) == 2


def test_the_event_must_be_exactly_the_next_sequence(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceSequenceConflictError):
            repo.append_state_event(uow, revoke_event(1), 0)  # wrong expectation
        with pytest.raises(ReferenceSequenceConflictError):
            repo.append_state_event(uow, first_event(1), 1)  # sequence 1 again
        uow.rollback()


def test_nothing_can_be_appended_after_a_terminal_event(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    append(store, repo, revoke_event(1), 1)
    # a third event cannot even be built (history is capped at ACTIVE plus one terminal event)
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceSequenceConflictError):
            repo.append_state_event(uow, revoke_event(1, at=T2), 2)
        uow.rollback()


def test_an_event_that_predates_the_history_is_refused(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0], start=T0)
    with store.unit_of_work() as uow:
        stale = ReferenceHistory.begin(eid(10), rid(1), T2).revoke(eid(11), T2).events[1]
        repo.append_state_event(uow, stale, 1)  # first event is at T0, so T2 is fine
        uow.commit()
    create(store, repo, 2, ref_assets[1])
    early = ReferenceStateEvent(event_id=eid(31), reference_id=rid(2), sequence=2,
                                reference_state=ReferenceState.REVOKED,
                                occurred_at=UtcTimestamp.parse("2026-10-31T09:00:00Z"))  # fmt: skip
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceSequenceConflictError, match="legal next step"):
            repo.append_state_event(uow, early, 1)
        uow.rollback()


def test_concurrent_revokes_produce_exactly_one_winner(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    results: list[str] = []
    gate = threading.Barrier(2, timeout=10)

    def worker(n):
        gate.wait()
        event = ReferenceHistory.begin(eid(10), rid(1), T0).revoke(eid(40 + n), T1).events[1]
        try:
            append(store, repo, event, 1)
            results.append("won")
        except ReferenceSequenceConflictError:
            results.append("lost")

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(results) == ["lost", "won"]
    assert len(read(store, lambda u: repo.history(u, rid(1))).events) == 2


# ------------------------------------------------------------------------------ standing and lookup
def test_expiry_is_derived_from_the_trusted_time_and_never_stored(store, repo, ref_assets, raw):
    create(store, repo, 1, ref_assets[0], until=T2)
    assert read(store, lambda u: repo.current_state(u, rid(1), T1)) is ReferenceStanding.ACTIVE
    assert read(store, lambda u: repo.current_state(u, rid(1), T3)) is ReferenceStanding.EXPIRED
    assert raw.rows("SELECT state FROM reference_state_events") == [("ACTIVE",)]


def test_find_returns_only_references_usable_now_for_that_purpose(store, repo, ref_assets):
    other = ReferencePurpose("calibration")
    create(store, repo, 1, ref_assets[0])  # usable
    create(store, repo, 2, ref_assets[1], until=T1)  # expires before the query time
    create(store, repo, 3, ref_assets[2], start=T3)  # not started
    create(store, repo, 4, ref_assets[3], purpose=other)  # another purpose
    found = read(store, lambda u: repo.find_active_by_purpose(u, GT, T2))
    assert [r.reference_id for r in found] == [rid(1)]
    calibration = read(store, lambda u: repo.find_active_by_purpose(u, other, T2))
    assert [r.reference_id for r in calibration] == [rid(4)]
    assert [r.reference_id for r in read(store, lambda u: repo.find_active_by_purpose(u, GT, T3))] == [
        rid(1), rid(3)]  # fmt: skip


def test_revoked_and_superseded_references_are_not_found(store, repo, ref_assets):
    for n in (1, 2, 3):
        create(store, repo, n, ref_assets[n - 1])
    append(store, repo, revoke_event(1), 1)
    event = ReferenceHistory.begin(eid(20), rid(2), T0).supersede(eid(21), T1, rid(3)).events[1]
    append(store, repo, event, 1)
    assert [r.reference_id for r in read(store, lambda u: repo.find_active_by_purpose(u, GT, T2))] == [rid(3)]


def test_find_orders_by_start_then_id_and_honours_the_limit(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0], start=T1)
    create(store, repo, 3, ref_assets[1], start=T0)
    create(store, repo, 2, ref_assets[2], start=T0)
    got = read(store, lambda u: repo.find_active_by_purpose(u, GT, T2))
    assert [r.reference_id for r in got] == [rid(2), rid(3), rid(1)]  # start first, then id
    two = read(store, lambda u: repo.find_active_by_purpose(u, GT, T2, limit=2))
    assert [r.reference_id for r in two] == [rid(2), rid(3)]
    for bad in (0, 201, True):
        with store.unit_of_work() as uow:
            with pytest.raises(ReferenceQueryError):
                repo.find_active_by_purpose(uow, GT, T2, limit=bad)
            uow.rollback()


def test_find_includes_a_window_that_ends_exactly_now(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0], until=T2)
    assert len(read(store, lambda u: repo.find_active_by_purpose(u, GT, T2))) == 1
    assert len(read(store, lambda u: repo.find_active_by_purpose(u, GT, T3))) == 0


# ------------------------------------------------------------------------------ fail closed
def test_a_damaged_record_row_fails_closed(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    tamper(store, ("UPDATE reference_records SET authority = ''", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceIntegrityError):
            repo.get(uow, rid(1))
        uow.rollback()


def test_a_history_with_a_gap_fails_closed(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    tamper(store, ("UPDATE reference_state_events SET sequence = 2", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceIntegrityError):
            repo.history(uow, rid(1))
        uow.rollback()


def test_a_reference_whose_events_vanished_fails_closed(store, repo, ref_assets):
    create(store, repo, 1, ref_assets[0])
    tamper(store, ("DELETE FROM reference_state_events", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(ReferenceIntegrityError, match="no history"):
            repo.history(uow, rid(1))
        uow.rollback()
