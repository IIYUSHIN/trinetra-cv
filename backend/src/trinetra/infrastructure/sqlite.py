"""SQLite engine factory and connection pragmas (IMP-0041).

The single Core-controlled database-construction boundary. Nothing else may open a SQLite
connection (workers, runners and plugins never do; the import-boundary checker already confines
`sqlite3`/`sqlalchemy` to this layer). Source requirements: one configured database path under the
state root, parent validated, never inside the source tree; WAL, synchronous=FULL and foreign_keys=ON
are requested AND READ BACK on every new connection (LAW-IMP-089: "security-relevant database
configuration must be verified, not merely requested"); a connection whose settings do not verify
is refused rather than used in authoritative mode.

PROPOSED engineering decisions (the corpus fixes the three pragmas, not the rest): filename grammar,
busy_timeout=5000ms, trusted_schema=OFF, minimum SQLite 3.37, owner-only parent directory check,
source-tree detection by `.git`/`pyproject.toml` ancestors, and BEGIN IMMEDIATE for every
transaction because this engine is the sole-writer engine (read-only engines are a later packet).
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import create_engine, event

from trinetra.errors import TrinetraError
from trinetra.infrastructure.asset_immutability import install_guard

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

MIN_SQLITE_VERSION: Final = (3, 37, 0)
BUSY_TIMEOUT_MS: Final = 5000
MAX_FILENAME_LENGTH: Final = 64
FILENAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_-]*\.sqlite3")
SOURCE_TREE_MARKERS: Final = (".git", "pyproject.toml")
DEFAULT_FILENAME: Final = "trinetra.sqlite3"
_SYNCHRONOUS_FULL: Final = 2
PRAGMA_NAMES: Final = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout", "trusted_schema")


class DatabaseConfigError(TrinetraError):
    code = "DB_CONFIG_INVALID"
    default_message = "database location or configuration is invalid"


class DatabasePragmaError(TrinetraError):
    code = "DB_PRAGMA_UNVERIFIED"
    default_message = "a required SQLite setting did not take effect"


def _fail(message: str, **details: str) -> DatabaseConfigError:
    return DatabaseConfigError(message, details=details or None)


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Validated location of the authoritative database."""

    state_root: Path
    filename: str = DEFAULT_FILENAME

    def __post_init__(self) -> None:
        if not isinstance(self.state_root, Path):
            raise _fail("state_root must be a pathlib.Path")
        if not isinstance(self.filename, str) or len(self.filename) > MAX_FILENAME_LENGTH:
            raise _fail("filename must be a short string")
        if not FILENAME_PATTERN.fullmatch(self.filename):
            raise _fail("filename must match [a-z][a-z0-9_-]*.sqlite3 and contain no path")

    @property
    def path(self) -> Path:
        return self.state_root / self.filename


def _in_source_tree(directory: Path) -> str | None:
    package_dir = Path(__file__).resolve().parent.parent
    if directory == package_dir or package_dir in directory.parents:
        return "inside the trinetra package directory"
    for candidate in (directory, *directory.parents):
        for marker in SOURCE_TREE_MARKERS:
            if (candidate / marker).exists():
                return f"inside a source tree ({marker} found in {candidate.name or '/'})"
    return None


def validate_database_path(config: DatabaseConfig) -> Path:
    """Return the verified absolute database path or raise DatabaseConfigError."""
    root = config.state_root
    if not root.is_absolute():
        raise _fail("state_root must be absolute")
    if "\x00" in str(root):
        raise _fail("state_root contains a NUL byte")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise _fail("state_root does not exist or cannot be resolved") from exc
    if resolved != root.absolute() and root.is_symlink():
        raise _fail("state_root must not be a symbolic link")
    if not resolved.is_dir():
        raise _fail("state_root is not a directory")
    info = resolved.stat()
    if info.st_mode & stat.S_IWOTH:
        raise _fail("state_root must not be world-writable")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise _fail("state_root must be owned by the running user")
    reason = _in_source_tree(resolved)
    if reason is not None:
        raise _fail(f"state_root is {reason}; the database must live under the state root")
    path = resolved / config.filename
    if path.is_symlink():
        raise _fail("the database file must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise _fail("the database path exists and is not a regular file")
    return path


def _row_value(cursor: sqlite3.Cursor, pragma: str) -> object:
    row = cursor.execute(pragma).fetchone()
    return None if row is None else row[0]


def apply_and_verify_pragmas(connection: sqlite3.Connection) -> dict[str, object]:
    """Request each required setting, read it back, and refuse the connection on any mismatch."""
    if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
        raise DatabasePragmaError(
            "SQLite is older than the supported minimum",
            details={"found": sqlite3.sqlite_version, "minimum": ".".join(map(str, MIN_SQLITE_VERSION))},
        )
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL").fetchall()
        cursor.execute("PRAGMA synchronous=FULL").fetchall()
        cursor.execute("PRAGMA foreign_keys=ON").fetchall()
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}").fetchall()
        cursor.execute("PRAGMA trusted_schema=OFF").fetchall()
        observed: dict[str, object] = {
            "journal_mode": str(_row_value(cursor, "PRAGMA journal_mode")).lower(),
            "synchronous": _row_value(cursor, "PRAGMA synchronous"),
            "foreign_keys": _row_value(cursor, "PRAGMA foreign_keys"),
            "busy_timeout": _row_value(cursor, "PRAGMA busy_timeout"),
            "trusted_schema": _row_value(cursor, "PRAGMA trusted_schema"),
        }
    finally:
        cursor.close()
    expected: dict[str, object] = {
        "journal_mode": "wal",
        "synchronous": _SYNCHRONOUS_FULL,
        "foreign_keys": 1,
        "busy_timeout": BUSY_TIMEOUT_MS,
        "trusted_schema": 0,
    }
    wrong = sorted(name for name, want in expected.items() if observed[name] != want)
    if wrong:
        raise DatabasePragmaError(
            "SQLite did not report the required settings: " + ", ".join(wrong),
            details={name: str(observed[name]) for name in wrong},
        )
    return observed


class TrinetraDatabase:
    """Owner of the one authoritative SQLAlchemy engine. Construct via `open`."""

    __slots__ = ("_config", "_engine", "_path")

    def __init__(self, config: DatabaseConfig, path: Path, engine: Engine) -> None:
        self._config = config
        self._path = path
        self._engine = engine

    @classmethod
    def open(cls, config: DatabaseConfig) -> TrinetraDatabase:
        path = validate_database_path(config)
        engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)

        def _on_connect(dbapi_connection: Any, record: Any) -> None:
            dbapi_connection.isolation_level = None  # we emit BEGIN ourselves (SQLAlchemy recipe)
            apply_and_verify_pragmas(dbapi_connection)
            install_guard(dbapi_connection, record.info)

        def _on_begin(connection: Connection) -> None:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        event.listen(engine, "connect", _on_connect)
        event.listen(engine, "begin", _on_begin)
        database = cls(config, path, engine)
        with engine.connect():  # fail at open time, not at first use
            pass
        return database

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def path(self) -> Path:
        return self._path

    def verified_settings(self) -> dict[str, object]:
        """Settings as reported by a live connection right now (never cached)."""
        with self._engine.connect() as connection:
            raw = connection.connection.driver_connection
            assert raw is not None  # noqa: S101 - SQLAlchemy contract after connect()
            cursor = raw.cursor()
            try:
                return {name: _row_value(cursor, f"PRAGMA {name}") for name in PRAGMA_NAMES}
            finally:
                cursor.close()

    def dispose(self) -> None:
        self._engine.dispose()


__all__ = [
    "BUSY_TIMEOUT_MS",
    "DEFAULT_FILENAME",
    "DatabaseConfig",
    "DatabaseConfigError",
    "DatabasePragmaError",
    "TrinetraDatabase",
    "apply_and_verify_pragmas",
    "validate_database_path",
]
