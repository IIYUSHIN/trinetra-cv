"""IMP-0041 tests: SQLite engine factory and verified connection pragmas."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import trinetra
from trinetra.infrastructure.asset_immutability import ddl_window
from trinetra.infrastructure.sqlite import (
    BUSY_TIMEOUT_MS,
    DatabaseConfig,
    DatabaseConfigError,
    DatabasePragmaError,
    TrinetraDatabase,
    apply_and_verify_pragmas,
    validate_database_path,
)

EXPECTED = {"journal_mode": "wal", "synchronous": 2, "foreign_keys": 1,
            "busy_timeout": BUSY_TIMEOUT_MS, "trusted_schema": 0}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    return state


@pytest.fixture
def db(root: Path):
    database = TrinetraDatabase.open(DatabaseConfig(root))
    yield database
    database.dispose()


# ------------------------------------------------------------------ location rules
def test_database_lives_under_the_state_root_with_the_default_name(db, root):
    assert db.path == root.resolve() / "trinetra.sqlite3" and db.path.exists()


BAD_NAMES = ["", "a", "x.db", "../x.sqlite3", "a/b.sqlite3", "A.sqlite3", "x.sqlite3 ", ".sqlite3",
             "x\x00.sqlite3", "x.SQLITE3", "a" * 60 + ".sqlite3", "é.sqlite3"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_filenames_are_refused(root, name):
    with pytest.raises(DatabaseConfigError):
        DatabaseConfig(root, name)


def test_non_path_and_non_str_inputs_are_refused(root):
    with pytest.raises(DatabaseConfigError):
        DatabaseConfig(str(root))  # type: ignore[arg-type]
    with pytest.raises(DatabaseConfigError):
        DatabaseConfig(root, 5)  # type: ignore[arg-type]


def test_relative_missing_and_non_directory_roots_are_refused(tmp_path):
    with pytest.raises(DatabaseConfigError, match="absolute"):
        validate_database_path(DatabaseConfig(Path("relative/state")))
    with pytest.raises(DatabaseConfigError, match="does not exist"):
        validate_database_path(DatabaseConfig(tmp_path / "missing"))
    afile = tmp_path / "afile"
    afile.write_text("x")
    with pytest.raises(DatabaseConfigError, match="not a directory"):
        validate_database_path(DatabaseConfig(afile))


def test_world_writable_root_is_refused(root):
    root.chmod(0o777)
    with pytest.raises(DatabaseConfigError, match="world-writable"):
        validate_database_path(DatabaseConfig(root))


def test_symlinked_root_and_symlinked_database_file_are_refused(root, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(DatabaseConfigError, match="symbolic link"):
        validate_database_path(DatabaseConfig(link))
    (root / "trinetra.sqlite3").symlink_to(tmp_path / "elsewhere.sqlite3")
    with pytest.raises(DatabaseConfigError, match="symbolic link"):
        validate_database_path(DatabaseConfig(root))


def test_database_path_that_is_a_directory_is_refused(root):
    (root / "trinetra.sqlite3").mkdir()
    with pytest.raises(DatabaseConfigError, match="regular file"):
        validate_database_path(DatabaseConfig(root))


@pytest.mark.parametrize("marker", [".git", "pyproject.toml"])
def test_state_root_inside_a_source_tree_is_refused(tmp_path, marker):
    checkout = tmp_path / "checkout"
    inner = checkout / "var" / "state"
    inner.mkdir(parents=True, mode=0o700)
    (checkout / marker).mkdir() if marker == ".git" else (checkout / marker).write_text("")
    with pytest.raises(DatabaseConfigError, match="source tree"):
        validate_database_path(DatabaseConfig(inner))


def test_state_root_inside_the_installed_package_is_refused():
    package = Path(trinetra.__file__).resolve().parent
    with pytest.raises(DatabaseConfigError):
        validate_database_path(DatabaseConfig(package / "infrastructure"))


def test_refusals_never_create_files(root, tmp_path):
    before = sorted(p.name for p in tmp_path.rglob("*"))
    with pytest.raises(DatabaseConfigError):
        TrinetraDatabase.open(DatabaseConfig(tmp_path / "missing"))
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


# ------------------------------------------------------------------ verified pragmas
def test_a_live_connection_reports_every_required_setting(db):
    assert db.verified_settings() == EXPECTED


def test_every_new_connection_gets_the_settings_not_just_the_first(db):
    connections = [db.engine.connect() for _ in range(4)]
    try:
        for conn in connections:
            raw = conn.connection.driver_connection
            names = ("foreign_keys", "synchronous", "busy_timeout")
            row = {n: raw.execute(f"PRAGMA {n}").fetchone()[0] for n in names}
            assert row == {"foreign_keys": 1, "synchronous": 2, "busy_timeout": BUSY_TIMEOUT_MS}
    finally:
        for conn in connections:
            conn.close()


def test_wal_is_really_on_disk_not_just_reported(db):
    with db.engine.begin() as conn:
        with ddl_window(conn):
            conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
    other = sqlite3.connect(db.path)
    try:
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        other.close()


def test_an_unverifiable_setting_refuses_the_connection():
    memory = sqlite3.connect(":memory:")  # cannot be WAL: a genuine failure, no mocking
    try:
        with pytest.raises(DatabasePragmaError, match="journal_mode") as info:
            apply_and_verify_pragmas(memory)
        assert info.value.code == "DB_PRAGMA_UNVERIFIED" and "memory" in str(info.value.details)
    finally:
        memory.close()


def test_a_too_old_sqlite_is_refused(monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(DatabasePragmaError, match="older"):
            apply_and_verify_pragmas(connection)
    finally:
        connection.close()


def test_open_fails_at_open_time_when_pragmas_cannot_verify(root, monkeypatch):
    from trinetra.infrastructure import sqlite as module

    def broken(_connection):
        raise DatabasePragmaError("simulated")

    monkeypatch.setattr(module, "apply_and_verify_pragmas", broken)
    with pytest.raises(DatabasePragmaError):
        TrinetraDatabase.open(DatabaseConfig(root))


# ------------------------------------------------------------------ behaviour that depends on them
def test_foreign_keys_are_actually_enforced(db):
    with db.engine.begin() as conn:
        with ddl_window(conn):
            conn.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        with ddl_window(conn):
            conn.execute(
                text("CREATE TABLE child (id INTEGER PRIMARY KEY, p INTEGER NOT NULL REFERENCES parent(id))")
            )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(text("INSERT INTO child (id, p) VALUES (1, 99)"))
    with db.engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM child")).scalar() == 0


def test_a_failed_transaction_rolls_back_completely_including_ddl(db):
    with pytest.raises(RuntimeError), db.engine.begin() as conn:
        with ddl_window(conn):
            conn.execute(text("CREATE TABLE ghost (x INTEGER)"))
        conn.execute(text("INSERT INTO ghost VALUES (1)"))
        raise RuntimeError("abort")
    with db.engine.begin() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
    assert "ghost" not in names


def test_transactions_start_immediate_so_a_second_writer_waits_instead_of_deadlocking(db):
    with db.engine.begin() as conn, ddl_window(conn):
        conn.execute(text("CREATE TABLE c (x INTEGER)"))
    first = db.engine.connect()
    first.begin()
    try:
        assert first.connection.driver_connection.in_transaction
        other = sqlite3.connect(db.path, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
    finally:
        first.rollback()
        first.close()


def test_threads_each_get_verified_connections(db):
    errors: list[BaseException] = []

    def work():
        try:
            assert db.verified_settings() == EXPECTED
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# ------------------------------------------------------------------ durability / crash
def test_committed_data_survives_dispose_and_reopen(root):
    first = TrinetraDatabase.open(DatabaseConfig(root))
    with first.engine.begin() as conn:
        with ddl_window(conn):
            conn.execute(text("CREATE TABLE keep (x INTEGER)"))
        conn.execute(text("INSERT INTO keep VALUES (42)"))
    first.dispose()
    second = TrinetraDatabase.open(DatabaseConfig(root))
    try:
        with second.engine.connect() as conn:
            assert conn.execute(text("SELECT x FROM keep")).scalar() == 42
        assert second.verified_settings() == EXPECTED
    finally:
        second.dispose()


CRASH = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from sqlalchemy import text
    from trinetra.infrastructure.asset_immutability import ddl_window
    from trinetra.infrastructure.sqlite import DatabaseConfig, TrinetraDatabase
    db = TrinetraDatabase.open(DatabaseConfig(Path(sys.argv[1])))
    with db.engine.begin() as c:
        with ddl_window(c):
            c.execute(text("CREATE TABLE ev (x INTEGER)"))
        c.execute(text("INSERT INTO ev VALUES (1)"))
    c = db.engine.connect(); c.begin()
    c.execute(text("INSERT INTO ev VALUES (2)"))   # never committed
    os._exit(9)                                      # hard crash, no cleanup
    """
)


def test_hard_crash_mid_transaction_keeps_only_committed_rows(root):
    src = str(Path(trinetra.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": src}
    command = [sys.executable, "-c", CRASH, str(root)]
    proc = subprocess.run(command, check=False, env=env, capture_output=True, timeout=60)
    assert proc.returncode == 9, proc.stderr.decode()
    db = TrinetraDatabase.open(DatabaseConfig(root))
    try:
        with db.engine.connect() as conn:
            assert [r[0] for r in conn.execute(text("SELECT x FROM ev ORDER BY x"))] == [1]
            assert conn.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    finally:
        db.dispose()
