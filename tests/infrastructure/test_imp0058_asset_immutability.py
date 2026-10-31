"""IMP-0058 tests: schema changes are impossible outside a migration window; guard loss is detected."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DatabaseError

from trinetra.infrastructure.asset_immutability import (
    GUARD_SUFFIXES,
    PROTECTED_TABLES,
    DdlGate,
    ImmutabilityError,
    ddl_window,
    expected_guard_names,
    gate_of,
    require_immutability,
    verify_immutability,
)


def refused(store, sql: str) -> bool:
    with store.unit_of_work() as uow:
        try:
            uow.connection.exec_driver_sql(sql)
        except DatabaseError as exc:
            uow.rollback()
            text = str(exc.orig)
            return "not authorized" in text or "may not be modified" in text
        uow.rollback()
        return False


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE evil (x INTEGER)",
        "CREATE TEMP TABLE evil (x INTEGER)",
        "CREATE INDEX ix_evil ON asset_records (supplied_name)",
        "CREATE VIEW v AS SELECT 1",
        "DROP TRIGGER asset_records_no_update",
        "DROP TABLE asset_records",
        "DROP INDEX ix_asset_records_blob",
        "ALTER TABLE asset_records RENAME TO gone",
        "ALTER TABLE journal_state ADD COLUMN extra TEXT",
        "REINDEX",
        "ATTACH DATABASE ':memory:' AS other",
        "UPDATE sqlite_master SET sql = 'x'",
        "DELETE FROM sqlite_master",
        "PRAGMA writable_schema = ON",
        "PRAGMA WRITABLE_SCHEMA = ON",
        "PRAGMA trusted_schema = ON",
        "PRAGMA ignore_check_constraints = ON",
        "PRAGMA foreign_keys = OFF",
        "PRAGMA recursive_triggers = ON",
        "PRAGMA user_version = 7",
    ],
)
def test_schema_changes_are_refused_by_default(store, sql):
    assert refused(store, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA writable_schema",
        "PRAGMA foreign_keys",
        "PRAGMA journal_mode",
        "PRAGMA table_info(asset_records)",
        "PRAGMA integrity_check",
        "SELECT count(*) FROM sqlite_master",
        "SELECT count(*) FROM asset_records",
    ],
)
def test_reading_and_ordinary_pragmas_still_work(store, sql):
    assert not refused(store, sql)


def test_the_guard_is_not_a_substitute_for_the_row_triggers(store, raw):
    # the authorizer lets row writes through to the triggers, which are the second, independent defence
    message = raw.attempt("UPDATE asset_records SET supplied_name = 'x'")
    assert "append-only" in message or "no such" in message or message == ""
    assert "not authorized" not in message


def test_a_migration_window_permits_ddl_and_then_closes(store):
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("CREATE TABLE scratch (x INTEGER)")
        assert not gate_of(uow.connection).is_open
        uow.rollback()
    assert refused(store, "CREATE TABLE scratch2 (x INTEGER)")


def test_the_window_closes_even_when_the_block_raises(store):
    with store.unit_of_work() as uow:
        with pytest.raises(RuntimeError, match="boom"), ddl_window(uow.connection):
            raise RuntimeError("boom")
        assert not gate_of(uow.connection).is_open
        uow.rollback()


def test_windows_do_not_nest(store):
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            with pytest.raises(ImmutabilityError, match="already open"), ddl_window(uow.connection):
                pass
            assert gate_of(uow.connection).is_open  # the failed nested attempt did not close the outer one
        uow.rollback()


def test_a_window_on_one_connection_does_not_open_another(store):
    with store.unit_of_work() as first:
        with ddl_window(first.connection):
            engine = store.database.engine
            with engine.connect() as second:
                assert not gate_of(second).is_open
        first.rollback()


def test_an_unguarded_connection_is_refused_not_trusted(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'plain.db'}")
    with engine.connect() as connection, pytest.raises(ImmutabilityError, match="not opened through"):
        gate_of(connection)
    engine.dispose()


def test_the_gate_object_reports_its_state():
    gate = DdlGate()
    assert not gate.is_open
    gate.begin_window()
    assert gate.is_open
    gate.end_window()
    assert not gate.is_open


def test_a_freshly_migrated_database_is_intact(store):
    with store.unit_of_work() as uow:
        report = verify_immutability(uow.connection)
        require_immutability(uow.connection)
        uow.rollback()
    assert report.intact and report.missing == ()


def test_the_expected_guard_set_matches_the_schema_exactly(store):
    names = expected_guard_names()
    assert len(names) == len(PROTECTED_TABLES) * len(GUARD_SUFFIXES) == 12
    assert len(set(names)) == 12
    with store.unit_of_work() as uow:
        rows = uow.connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")
        present = {r[0] for r in rows}
        uow.rollback()
    assert set(names) <= present


def test_a_missing_guard_is_reported_by_name_and_never_repaired(store):
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("DROP TRIGGER audit_events_no_delete")
        uow.commit()
    with store.unit_of_work() as uow:
        report = verify_immutability(uow.connection)
        assert report.missing == ("audit_events_no_delete",)
        with pytest.raises(ImmutabilityError) as caught:
            require_immutability(uow.connection)
        assert caught.value.details["missing"] == "audit_events_no_delete"
        uow.rollback()
    with store.unit_of_work() as uow:  # still missing: nothing repaired it
        assert verify_immutability(uow.connection).missing == ("audit_events_no_delete",)
        uow.rollback()


def test_several_missing_guards_are_all_listed_in_schema_order(store):
    with store.unit_of_work() as uow:
        with ddl_window(uow.connection):
            uow.connection.exec_driver_sql("DROP TRIGGER asset_records_no_replace")
            uow.connection.exec_driver_sql("DROP TRIGGER audit_event_contexts_no_update")
        uow.commit()
    with store.unit_of_work() as uow:
        assert verify_immutability(uow.connection).missing == (
            "asset_records_no_replace",
            "audit_event_contexts_no_update",
        )
        uow.rollback()
