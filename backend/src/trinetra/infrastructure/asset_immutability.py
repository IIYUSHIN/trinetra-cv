"""Asset metadata immutability rules (IMP-0058).

Authoritative rows are immutable by two independent mechanisms: the append-only triggers of the
schema (IMP-0045) and, here, a connection-level authorizer that refuses every schema change and every
change of a protected pragma (writable_schema included, which is what would make catalogue writes
possible) unless a migration has explicitly opened a DDL window. The authorizer means a compromised or
buggy writer cannot drop the triggers and then rewrite history on the same connection.
`verify_immutability` re-reads the live catalogue and reports any protected table whose guard triggers
are missing, so tampering done out of band is detected (it is never repaired).

PROPOSED engineering decisions (ERR-CL-016): deny-by-default DDL window per connection, the set of
protected pragmas, and the fixed trigger-name suffixes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection

PROTECTED_TABLES: Final = (
    "asset_records",
    "audit_events",
    "audit_event_subjects",
    "audit_event_contexts",
)
GUARD_SUFFIXES: Final = ("_no_update", "_no_delete", "_no_replace")
PROTECTED_PRAGMAS: Final = frozenset(
    {
        "writable_schema", "trusted_schema", "foreign_keys", "journal_mode", "synchronous",
        "ignore_check_constraints", "defer_foreign_keys", "recursive_triggers", "query_only",
        "schema_version", "user_version", "application_id", "locking_mode", "secure_delete",
    }
)  # fmt: skip
_DDL_ACTIONS: Final = frozenset(
    getattr(sqlite3, name)
    for name in (
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_CREATE_VTABLE", "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW", "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW", "SQLITE_DROP_VTABLE", "SQLITE_ALTER_TABLE", "SQLITE_ATTACH",
        "SQLITE_DETACH", "SQLITE_REINDEX",
    )
)  # fmt: skip
_INFO_KEY: Final = "trinetra.ddl_gate"


class ImmutabilityError(TrinetraError):
    code = "IMMUTABILITY_VIOLATED"
    default_message = "an immutability guard is missing or the connection is not guarded"


class DdlGate:
    """Per-connection switch. Closed unless a `ddl_window` is active."""

    __slots__ = ("_open",)

    def __init__(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def begin_window(self) -> None:
        if self._open:
            raise ImmutabilityError("a DDL window is already open on this connection")
        self._open = True

    def end_window(self) -> None:
        self._open = False

    def authorize(self, action: int, arg1: str | None, arg2: str | None, *_rest: Any) -> int:
        if self._open:
            return sqlite3.SQLITE_OK
        if action in _DDL_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in PROTECTED_PRAGMAS and arg2 is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


def install_guard(dbapi_connection: sqlite3.Connection, info: dict[str, Any]) -> DdlGate:
    """Attach the authorizer to a new connection; `info` is the pool record's info dict."""
    gate = DdlGate()
    dbapi_connection.set_authorizer(gate.authorize)
    info[_INFO_KEY] = gate
    return gate


def gate_of(connection: Connection) -> DdlGate:
    gate = connection.connection.info.get(_INFO_KEY)
    if not isinstance(gate, DdlGate):
        raise ImmutabilityError("this connection was not opened through the guarded engine")
    return gate


@contextmanager
def ddl_window(connection: Connection) -> Iterator[None]:
    """Allow schema changes on this connection for the duration of the block (migrations only)."""
    gate = gate_of(connection)
    gate.begin_window()
    try:
        yield
    finally:
        gate.end_window()


@dataclass(frozen=True, slots=True)
class ImmutabilityReport:
    missing: tuple[str, ...]

    @property
    def intact(self) -> bool:
        return not self.missing


def expected_guard_names() -> tuple[str, ...]:
    return tuple(f"{table}{suffix}" for table in PROTECTED_TABLES for suffix in GUARD_SUFFIXES)


def verify_immutability(connection: Connection) -> ImmutabilityReport:
    """Report the guard triggers absent from the live catalogue. Never repairs anything."""
    rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    present = {row[0] for row in rows}
    return ImmutabilityReport(tuple(name for name in expected_guard_names() if name not in present))


def require_immutability(connection: Connection) -> None:
    report = verify_immutability(connection)
    if not report.intact:
        raise ImmutabilityError(details={"missing": ",".join(report.missing)})


__all__ = [
    "PROTECTED_TABLES",
    "DdlGate",
    "ImmutabilityError",
    "ImmutabilityReport",
    "ddl_window",
    "expected_guard_names",
    "gate_of",
    "install_guard",
    "require_immutability",
    "verify_immutability",
]
