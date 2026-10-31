"""IMP-0043 tests: unit of work and Core store."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from trinetra.infrastructure.asset_immutability import ddl_window
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore, UnitOfWorkStateError
from trinetra.infrastructure.writer_lock import CoreWriterLock, WriterLockHeldError, WriterLockStateError

DEFERRED_CHILD = (
    "CREATE TABLE c (id INTEGER PRIMARY KEY, p INTEGER REFERENCES p(id) DEFERRABLE INITIALLY DEFERRED)"
)


@pytest.fixture
def store(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    with CoreStore.open(DatabaseConfig(root)) as opened:
        with opened.unit_of_work() as uow:
            with ddl_window(uow.connection):
                uow.connection.execute(text("CREATE TABLE t (x INTEGER PRIMARY KEY, y INTEGER)"))
            with ddl_window(uow.connection):
                uow.connection.execute(text("CREATE TABLE p (id INTEGER PRIMARY KEY)"))
            with ddl_window(uow.connection):
                uow.connection.execute(text(DEFERRED_CHILD))
            uow.commit()
        yield opened


QUERIES = {"t": "SELECT * FROM t ORDER BY 1", "c": "SELECT * FROM c ORDER BY 1"}


def rows(store: CoreStore, table: str = "t") -> list[tuple]:
    with store.unit_of_work() as uow:
        data = [tuple(r) for r in uow.connection.execute(text(QUERIES[table]))]
        uow.rollback()
    return data


def test_commit_persists(store):
    with store.unit_of_work() as uow:
        uow.connection.execute(text("INSERT INTO t VALUES (1, 10)"))
        uow.commit()
    assert rows(store) == [(1, 10)]


def test_an_exception_rolls_everything_back_and_propagates(store):
    with pytest.raises(RuntimeError), store.unit_of_work() as uow:
        uow.connection.execute(text("INSERT INTO t VALUES (1, 10)"))
        raise RuntimeError("boom")
    assert rows(store) == []


def test_leaving_without_a_decision_discards_and_fails_loudly(store):
    with pytest.raises(UnitOfWorkStateError, match="without commit"), store.unit_of_work() as uow:
        uow.connection.execute(text("INSERT INTO t VALUES (1, 10)"))
    assert rows(store) == []


def test_explicit_rollback_is_a_valid_decision_and_idempotent(store):
    with store.unit_of_work() as uow:
        uow.connection.execute(text("INSERT INTO t VALUES (1, 10)"))
        uow.rollback()
        uow.rollback()
    assert rows(store) == []


def test_a_finished_unit_of_work_cannot_be_used_again(store):
    with store.unit_of_work() as uow:
        uow.commit()
        with pytest.raises(UnitOfWorkStateError, match="not open"):
            _ = uow.connection
        with pytest.raises(UnitOfWorkStateError):
            uow.commit()


def test_a_unit_of_work_cannot_be_entered_twice_or_used_before_entry(store):
    uow = store.unit_of_work()
    with pytest.raises(UnitOfWorkStateError):
        _ = uow.connection
    with uow:
        uow.commit()
    with pytest.raises(UnitOfWorkStateError):
        uow.commit()


def test_nested_units_of_work_on_one_thread_are_refused_not_deadlocked(store):
    with store.unit_of_work() as outer:
        with pytest.raises(UnitOfWorkStateError, match="already open"):
            store.unit_of_work().__enter__()
        outer.rollback()
    with store.unit_of_work() as again:  # the refusal did not poison the thread state
        again.rollback()


def test_a_failed_commit_leaves_nothing_behind(store):
    with pytest.raises(IntegrityError), store.unit_of_work() as uow:
        uow.connection.execute(text("INSERT INTO c VALUES (1, 99)"))  # deferred FK fails at COMMIT
        uow.commit()
    assert rows(store, "c") == []
    with store.unit_of_work() as uow:  # and the thread is usable again
        uow.rollback()


def test_a_unit_of_work_is_bound_to_its_thread(store):
    uow = store.unit_of_work()
    errors: list[BaseException] = []

    def other() -> None:
        try:
            uow.__enter__()
        except BaseException as exc:  # noqa: BLE001 - collected
            errors.append(exc)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert len(errors) == 1 and isinstance(errors[0], UnitOfWorkStateError)


def test_using_a_unit_of_work_from_another_thread_is_refused(store):
    errors: list[BaseException] = []
    with store.unit_of_work() as uow:

        def other() -> None:
            try:
                _ = uow.connection
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        uow.rollback()
    assert len(errors) == 1 and isinstance(errors[0], UnitOfWorkStateError)


def test_connections_return_to_the_pool(store):
    pool = store.database.engine.pool
    for _ in range(5):
        with store.unit_of_work() as uow:
            uow.rollback()
    assert pool.checkedout() == 0


def test_losing_the_writer_lock_stops_further_units_of_work(store):
    store.lock.path.unlink()
    with pytest.raises(WriterLockStateError):
        store.unit_of_work().__enter__()


def test_a_store_holds_the_sole_writer_lock_for_its_lifetime(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    config = DatabaseConfig(root)
    store = CoreStore.open(config)
    with pytest.raises(WriterLockHeldError):
        CoreStore.open(config)
    store.close()
    store.close()  # idempotent
    with pytest.raises(UnitOfWorkStateError, match="closed"):
        store.unit_of_work()
    with CoreStore.open(config):  # lock released by close()
        pass


def test_open_failure_releases_the_lock(tmp_path, monkeypatch):
    from trinetra.infrastructure import unit_of_work as module
    from trinetra.infrastructure.sqlite import DatabasePragmaError

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    config = DatabaseConfig(root)

    def broken(_config):
        raise DatabasePragmaError("simulated")

    monkeypatch.setattr(module.TrinetraDatabase, "open", staticmethod(broken))
    with pytest.raises(DatabasePragmaError):
        CoreStore.open(config)
    monkeypatch.undo()
    with CoreWriterLock.acquire(config):  # was released
        pass


def test_the_store_is_a_context_manager_that_closes(tmp_path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    with CoreStore.open(DatabaseConfig(root)) as opened:
        pass
    with pytest.raises(UnitOfWorkStateError):
        opened.unit_of_work()


def test_rollback_from_another_thread_is_refused(store):
    errors: list[BaseException] = []
    with store.unit_of_work() as uow:

        def other() -> None:
            try:
                uow.rollback()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        uow.rollback()
    assert len(errors) == 1 and isinstance(errors[0], UnitOfWorkStateError)
