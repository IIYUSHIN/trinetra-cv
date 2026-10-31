"""Transaction / session unit of work and the Core store handle (IMP-0043).

All authoritative writes happen inside a `UnitOfWork`, which can only be created by a process that
provably holds the sole-writer lock. Commit is EXPLICIT: leaving the `with` block normally without
calling `commit()` (or `rollback()` for read-only work) rolls everything back and raises, because a
silently dropped transaction is how authoritative state goes missing. One unit of work per thread at a
time (a nested one would wait on the writer lock held by its own parent), and a unit of work is bound to
the thread that created it (SQLAlchemy connections are not thread-safe).

PROPOSED engineering decisions: the strict explicit-commit rule and the thread-affinity rule.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Final, Self

from trinetra.errors import TrinetraError
from trinetra.infrastructure.sqlite import DatabaseConfig, TrinetraDatabase
from trinetra.infrastructure.writer_lock import CoreWriterLock

if TYPE_CHECKING:
    from types import TracebackType

    from sqlalchemy.engine import Connection, RootTransaction


class UnitOfWorkStateError(TrinetraError):
    code = "DB_UOW_STATE"
    default_message = "the unit of work was used in an invalid state"


_ACTIVE: Final = threading.local()


def _end_driver_transaction(connection: Connection) -> None:
    """SQLite keeps the transaction open after a COMMIT that fails a deferred constraint, while
    SQLAlchemy already considers it inactive; roll it back at the driver so the pooled connection is
    clean (found by test_a_failed_commit_leaves_nothing_behind)."""
    driver = connection.connection.driver_connection
    if driver is not None and getattr(driver, "in_transaction", False):
        driver.rollback()


class UnitOfWork:
    __slots__ = ("_connection", "_database", "_done", "_lock", "_thread", "_transaction")

    def __init__(self, database: TrinetraDatabase, lock: CoreWriterLock) -> None:
        self._database = database
        self._lock = lock
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None
        self._done = False
        self._thread = threading.get_ident()

    def __enter__(self) -> Self:
        if getattr(_ACTIVE, "open", False):
            raise UnitOfWorkStateError("a unit of work is already open on this thread")
        if threading.get_ident() != self._thread:
            raise UnitOfWorkStateError("a unit of work cannot move between threads")
        self._lock.assert_held()
        self._connection = self._database.engine.connect()
        try:
            self._transaction = self._connection.begin()
        except BaseException:
            self._connection.close()
            self._connection = None
            raise
        _ACTIVE.open = True
        return self

    @property
    def connection(self) -> Connection:
        self._check_usable()
        assert self._connection is not None  # noqa: S101 - established by _check_usable
        return self._connection

    def _check_usable(self) -> None:
        if threading.get_ident() != self._thread:
            raise UnitOfWorkStateError("a unit of work cannot be used from another thread")
        if self._connection is None or self._done:
            raise UnitOfWorkStateError("the unit of work is not open")
        self._lock.assert_held()

    def commit(self) -> None:
        self._check_usable()
        assert self._transaction is not None  # noqa: S101
        self._transaction.commit()  # on failure the exception propagates and __exit__ cleans up
        self._finish()

    def rollback(self) -> None:
        """Discard everything; also the explicit way to end a read-only unit of work."""
        if self._connection is None or self._done:
            return
        if threading.get_ident() != self._thread:
            raise UnitOfWorkStateError("a unit of work cannot be used from another thread")
        self._finish()

    def _finish(self) -> None:
        transaction, connection = self._transaction, self._connection
        self._done = True
        try:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
        finally:
            if connection is not None:
                _end_driver_transaction(connection)
                connection.close()
            self._connection = None
            self._transaction = None
            _ACTIVE.open = False

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._connection is None or self._done:
            return
        self._finish()
        if exc_type is None:
            raise UnitOfWorkStateError("unit of work left without commit() or rollback(); changes discarded")


class CoreStore:
    """The one handle through which authoritative persistence is reached."""

    __slots__ = ("_closed", "_database", "_lock")

    def __init__(self, lock: CoreWriterLock, database: TrinetraDatabase) -> None:
        self._lock = lock
        self._database = database
        self._closed = False

    @classmethod
    def open(cls, config: DatabaseConfig) -> Self:
        lock = CoreWriterLock.acquire(config)
        try:
            database = TrinetraDatabase.open(config)
        except BaseException:
            lock.release()
            raise
        return cls(lock, database)

    @property
    def database(self) -> TrinetraDatabase:
        return self._database

    @property
    def lock(self) -> CoreWriterLock:
        return self._lock

    def unit_of_work(self) -> UnitOfWork:
        if self._closed:
            raise UnitOfWorkStateError("the Core store is closed")
        return UnitOfWork(self._database, self._lock)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._database.dispose()
        finally:
            self._lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()


__all__ = ["CoreStore", "UnitOfWork", "UnitOfWorkStateError"]
