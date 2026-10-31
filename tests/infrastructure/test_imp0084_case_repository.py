"""IMP-0084/0085 tests: the Case repository and the Case asset-link repository against a real,
migrated database."""

from __future__ import annotations

import threading

import pytest

from persistence_support import put_asset, tamper
from trinetra.domain.assets import AssetId
from trinetra.domain.assurance_case import AssuranceCase, CaseInvariantError
from trinetra.domain.case import CaseId
from trinetra.domain.case_assets import CaseAssetRole as R
from trinetra.domain.case_assets import link_assets
from trinetra.domain.timing import UtcTimestamp
from trinetra.infrastructure.case_asset_link_repository import CaseAssetLinkRepository, CaseLinkIntegrityError
from trinetra.infrastructure.case_repository import (
    CaseExistsError,
    CaseIntegrityError,
    CaseNotFoundError,
    CaseRepository,
    CaseRevisionConflictError,
)

T0 = UtcTimestamp.parse("2026-10-31T10:00:00Z")
T1 = UtcTimestamp.parse("2026-10-31T10:00:05Z")
T2 = UtcTimestamp.parse("2026-10-31T10:01:00Z")


def cid(n: int = 1) -> CaseId:
    return CaseId.from_entropy(lambda size: bytes([n]) * size)


@pytest.fixture
def repo():
    return CaseRepository()


@pytest.fixture
def assets(raw):
    """Persisted assets: dataset, model, two references and a metadata file; ids as AssetId."""
    kinds = [("dataset", "dataset"), ("model", "model"), ("ref_a", "reference"), ("ref_b", "reference"),
             ("meta", "contributor_metadata")]  # fmt: skip
    return {name: AssetId.parse(put_asset(raw, i, kind)) for i, (name, kind) in enumerate(kinds, start=1)}


def make_case(assets, n: int = 1, *, extras=(), label=None, at=T0) -> AssuranceCase:
    entries = [(R.DATASET, assets["dataset"]), (R.MODEL, assets["model"]), *extras]
    return AssuranceCase.create(cid(n), link_assets(entries, at), at, label=label)


def stored(store, repo, case_id):
    with store.unit_of_work() as uow:
        found = repo.get(uow, case_id)
        uow.rollback()
    return found


def persist(store, repo, case):
    with store.unit_of_work() as uow:
        repo.add(uow, case)
        uow.commit()


# ------------------------------------------------------------------------------- create and read
def test_a_case_round_trips_exactly(store, repo, assets):
    case = make_case(assets, extras=[(R.REFERENCE, assets["ref_a"]), (R.REFERENCE, assets["ref_b"]),
                                     (R.CONTRIBUTOR_METADATA, assets["meta"])], label="Run 7")  # fmt: skip
    persist(store, repo, case)
    again = stored(store, repo, case.case_id)
    assert again == case and again.canonical_bytes() == case.canonical_bytes()
    assert [(lk.role, lk.ordinal) for lk in again.asset_links] == [
        (R.DATASET, 0), (R.MODEL, 0), (R.CONTRIBUTOR_METADATA, 0), (R.REFERENCE, 0), (R.REFERENCE, 1)]


def test_get_of_an_unknown_case_is_none_and_require_raises(store, repo):
    with store.unit_of_work() as uow:
        assert repo.get(uow, cid(9)) is None
        assert repo.exists(uow, cid(9)) is False
        with pytest.raises(CaseNotFoundError) as exc:
            repo.require(uow, cid(9))
        assert exc.value.details["case_id"] == str(cid(9))
        uow.rollback()


def test_adding_the_same_case_twice_is_refused_and_changes_nothing(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    with store.unit_of_work() as uow:
        with pytest.raises(CaseExistsError):
            repo.add(uow, case)
        uow.rollback()
    assert stored(store, repo, case.case_id) == case


def test_a_case_and_its_links_are_stored_together_or_not_at_all(store, repo, assets, raw):
    case = make_case(assets)
    with store.unit_of_work() as uow:
        repo.add(uow, case)
        uow.rollback()  # the transaction is abandoned
    assert raw.rows("SELECT count(*) FROM cases") == [(0,)]
    assert raw.rows("SELECT count(*) FROM case_asset_links") == [(0,)]


def test_a_link_to_an_unknown_asset_rolls_the_whole_case_back(store, repo, assets, raw):
    ghost = AssetId.from_entropy(lambda size: b"\x77" * size)
    bad = AssuranceCase.create(cid(), link_assets([(R.DATASET, assets["dataset"]), (R.MODEL, ghost)], T0), T0)
    with store.unit_of_work() as uow:
        with pytest.raises(Exception, match="role must equal"):
            repo.add(uow, bad)
        uow.rollback()
    assert raw.rows("SELECT count(*) FROM cases") == [(0,)]


def test_restart_preserves_ids_and_revisions(state_root):
    from trinetra.infrastructure.migration_runner import upgrade_to_head
    from trinetra.infrastructure.sqlite import DatabaseConfig
    from trinetra.infrastructure.unit_of_work import CoreStore

    repo = CaseRepository()
    with CoreStore.open(DatabaseConfig(state_root)) as first:
        upgrade_to_head(first)
        from persistence_support import RawDb

        raw = RawDb(first)
        ids = {k: AssetId.parse(put_asset(raw, i, k)) for i, k in enumerate(["dataset", "model"], start=1)}
        entries = [(R.DATASET, ids["dataset"]), (R.MODEL, ids["model"])]
        case = AssuranceCase.create(cid(), link_assets(entries, T0), T0)
        persist(first, repo, case)
        with first.unit_of_work() as uow:
            repo.save(uow, case.touch(T1), case.case_state_revision)
            uow.commit()
    with CoreStore.open(DatabaseConfig(state_root)) as second:
        again = stored(second, repo, case.case_id)
    assert again.case_id == case.case_id and again.case_state_revision.value == 2 and again.updated_at == T1
    assert again.asset_links == case.asset_links


# ------------------------------------------------------------------------------- optimistic save
def test_save_advances_the_revision_by_one(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    newer = case.touch(T1)
    with store.unit_of_work() as uow:
        repo.save(uow, newer, case.case_state_revision)
        uow.commit()
    assert stored(store, repo, case.case_id) == newer


def test_a_stale_writer_gets_a_conflict_and_changes_nothing(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    mine = case.touch(T1)
    theirs = case.touch(T2)
    with store.unit_of_work() as uow:
        repo.save(uow, mine, case.case_state_revision)
        uow.commit()
    with store.unit_of_work() as uow:
        with pytest.raises(CaseRevisionConflictError) as exc:
            repo.save(uow, theirs, case.case_state_revision)  # both started from revision 1
        assert exc.value.details == {"expected": 1, "actual": 2}
        uow.rollback()
    assert stored(store, repo, case.case_id) == mine


def test_the_new_case_must_be_exactly_one_revision_ahead(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    skipped = case.touch(T1).touch(T1)
    with store.unit_of_work() as uow:
        for bad in (skipped, case):
            with pytest.raises(CaseRevisionConflictError):
                repo.save(uow, bad, case.case_state_revision)
        uow.rollback()
    assert stored(store, repo, case.case_id).case_state_revision.value == 1


def test_saving_an_unknown_case_is_not_found(store, repo, assets):
    case = make_case(assets)
    with store.unit_of_work() as uow:
        with pytest.raises(CaseNotFoundError):
            repo.save(uow, case.touch(T1), case.case_state_revision)
        uow.rollback()


def test_a_save_that_changes_links_or_lifecycle_is_refused(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    swapped = make_case({**assets, "model": assets["ref_a"]}, at=T0).touch(T1)
    from trinetra.domain.case_lifecycle import CaseLifecycleState

    moved = case.touch(T1).evolve(lifecycle_state=CaseLifecycleState.ASSESSING)
    with store.unit_of_work() as uow:
        with pytest.raises(CaseInvariantError, match="links"):
            repo.save(uow, swapped, case.case_state_revision)
        with pytest.raises(CaseInvariantError, match="lifecycle"):
            repo.save(uow, moved, case.case_state_revision)
        uow.rollback()
    assert stored(store, repo, case.case_id) == case


def test_a_save_keeps_the_creation_time(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    forged = case.touch(T2).evolve(created_at=T1)
    with store.unit_of_work() as uow:
        with pytest.raises(CaseInvariantError, match="creation time"):
            repo.save(uow, forged, case.case_state_revision)
        uow.rollback()


def test_two_concurrent_writers_cannot_both_win(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    outcomes: list[str] = []
    barrier = threading.Barrier(2, timeout=10)

    def attempt(at):
        barrier.wait()
        try:
            with store.unit_of_work() as uow:
                current = repo.require(uow, case.case_id)
                repo.save(uow, current.touch(at), current.case_state_revision)
                uow.commit()
            outcomes.append("saved")
        except CaseRevisionConflictError:
            outcomes.append("conflict")

    # The sole-writer lock serialises the units of work, so the second one reads the first one's
    # result: no lost update is possible, and revisions stay gapless.
    threads = [threading.Thread(target=attempt, args=(t,)) for t in (T1, T1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(outcomes) == ["saved", "saved"]
    assert stored(store, repo, case.case_id).case_state_revision.value == 3


# ------------------------------------------------------------------------------- fail-closed reads
def test_a_damaged_row_fails_closed(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    tamper(store, ("UPDATE cases SET lifecycle_state = 'ready'", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(CaseIntegrityError):
            repo.get(uow, case.case_id)
        uow.rollback()


def test_a_case_missing_its_dataset_link_fails_closed(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    tamper(store, ("DELETE FROM case_asset_links WHERE role = 'dataset'", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(CaseIntegrityError):
            repo.get(uow, case.case_id)
        uow.rollback()


def test_a_damaged_link_row_fails_closed(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    tamper(store, ("UPDATE case_asset_links SET role = 'produced' WHERE role = 'model'", ()))
    with store.unit_of_work() as uow:
        with pytest.raises(CaseLinkIntegrityError):
            repo.get(uow, case.case_id)
        uow.rollback()


# ------------------------------------------------------------------------------- the link repository
def test_links_are_listed_in_canonical_order_per_case(store, repo, assets):
    links = CaseAssetLinkRepository()
    a = make_case(assets, 1, extras=[(R.REFERENCE, assets["ref_a"]), (R.REFERENCE, assets["ref_b"])])
    persist(store, repo, a)
    with store.unit_of_work() as uow:
        assert links.list_for_case(uow, a.case_id) == a.asset_links
        assert links.list_for_case(uow, cid(5)) == ()
        uow.rollback()


def test_the_cases_using_an_asset_can_be_found(store, repo, assets):
    links = CaseAssetLinkRepository()
    one = make_case(assets, 1)
    persist(store, repo, one)
    with store.unit_of_work() as uow:
        assert links.cases_using(uow, assets["dataset"]) == (cid(1),)
        assert links.cases_using(uow, assets["ref_a"]) == ()
        uow.rollback()


# ------------------------------------------------------------------------------- mutation-driven cases
def test_save_reports_the_real_stored_revision_when_the_caller_is_behind(store, repo, assets):
    case = make_case(assets)
    persist(store, repo, case)
    two = case.touch(T1)
    three = two.touch(T2)
    for newer, expected in ((two, case.case_state_revision), (three, two.case_state_revision)):
        with store.unit_of_work() as uow:
            repo.save(uow, newer, expected)
            uow.commit()
    # a caller that still believes revision 2 and brings a revision-3 aggregate: stored is already 3
    with store.unit_of_work() as uow:
        with pytest.raises(CaseRevisionConflictError) as exc:
            repo.save(uow, three, two.case_state_revision)
        assert exc.value.details == {"expected": 2, "actual": 3}
        uow.rollback()


def test_a_label_change_is_saved_and_read_back(store, repo, assets):
    case = make_case(assets, label="Before")
    persist(store, repo, case)
    renamed = case.touch(T1).evolve(case_label="After")
    with store.unit_of_work() as uow:
        repo.save(uow, renamed, case.case_state_revision)
        uow.commit()
    again = stored(store, repo, case.case_id)
    assert again.case_label == "After" and again.updated_at == T1 and again.created_at == T0
    cleared = again.touch(T2).evolve(case_label=None)
    with store.unit_of_work() as uow:
        repo.save(uow, cleared, again.case_state_revision)
        uow.commit()
    assert stored(store, repo, case.case_id).case_label is None


def test_a_row_that_moves_between_the_check_and_the_update_is_still_a_conflict(store, repo, assets):
    """Defence in depth: the writer lock serialises units of work, but the UPDATE is a compare-and-set
    anyway. Here the row is bumped inside the transaction after the revision check has passed."""
    case = make_case(assets)
    persist(store, repo, case)
    with store.unit_of_work() as uow:
        connection = uow.connection
        real = connection.exec_driver_sql

        def bump_before_update(sql, params=()):
            if sql.startswith("UPDATE cases SET case_label"):
                real("UPDATE cases SET state_revision = 2, updated_at = ?", (T1.to_wire(),))
            return real(sql, params)

        connection.exec_driver_sql = bump_before_update  # type: ignore[method-assign]
        with pytest.raises(CaseRevisionConflictError) as exc:
            repo.save(uow, case.touch(T1), case.case_state_revision)
        assert exc.value.details == {"expected": 1, "actual": 2}
        uow.rollback()


def test_links_added_with_a_save_are_stored_in_the_same_transaction(store, repo, assets, raw):
    case = make_case(assets)
    persist(store, repo, case)
    extra = make_case(assets, extras=[(R.REFERENCE, assets["ref_a"])])
    grown = case.touch(T1).evolve(asset_links=extra.asset_links)
    with store.unit_of_work() as uow:
        repo.save(uow, grown, case.case_state_revision)
        uow.commit()
    again = stored(store, repo, case.case_id)
    assert again.asset_links == extra.asset_links and again.case_state_revision.value == 2
    # and an abandoned save leaves neither the revision nor the new link behind
    more = make_case(assets, extras=[(R.REFERENCE, assets["ref_a"]), (R.REFERENCE, assets["ref_b"])])
    with store.unit_of_work() as uow:
        repo.save(uow, again.touch(T2).evolve(asset_links=more.asset_links), again.case_state_revision)
        uow.rollback()
    assert stored(store, repo, case.case_id) == again
    assert raw.rows("SELECT count(*) FROM case_asset_links") == [(3,)]


def test_cases_using_an_asset_come_back_in_id_order(store, repo, assets):
    links = CaseAssetLinkRepository()
    for n in (3, 1, 2):
        # each case needs distinct dataset/model assets? no: an asset may serve several cases
        persist(store, repo, make_case(assets, n))
    with store.unit_of_work() as uow:
        assert links.cases_using(uow, assets["dataset"]) == (cid(1), cid(2), cid(3))
        uow.rollback()


def test_each_link_keeps_its_own_linked_at(store, repo, assets):
    early = link_assets([(R.DATASET, assets["dataset"]), (R.MODEL, assets["model"])], T1)
    case = AssuranceCase.create(cid(), early, T2)
    persist(store, repo, case)
    again = stored(store, repo, case.case_id)
    assert {lk.linked_at for lk in again.asset_links} == {T1}
    assert again.created_at == T2
