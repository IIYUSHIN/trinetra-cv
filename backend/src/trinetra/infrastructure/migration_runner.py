"""Alembic bootstrap and migration runner (IMP-0044).

Schema migration authority is distinct from runtime record authority (LAW-IMP-091): only this module
and the migration scripts may change schema; repositories never import it (enforced by a test). The
runner exposes upgrade-to-head and a fail-closed `require_current`; there is NO downgrade entry point.
Each migration runs in its own transaction (SQLite DDL is transactional), so a failing step leaves the
schema and the recorded revision unchanged.

PROPOSED engineering decisions: Alembic is driven programmatically on the Core-owned connection
(no alembic.ini, no URL from the environment), exactly one head is required, a database revision that
the code does not know (a newer database) refuses startup, and migration files must be named
`NNNN_snake_case.py` with revision id = the four-digit prefix.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from trinetra.errors import TrinetraError
from trinetra.infrastructure.asset_immutability import ddl_window

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from trinetra.infrastructure.unit_of_work import CoreStore

MIGRATIONS_DIR: Final = Path(__file__).resolve().parent / "migrations"
FILE_PATTERN: Final = re.compile(r"(?P<rev>\d{4})_[a-z][a-z0-9_]*\.py")


class MigrationError(TrinetraError):
    code = "DB_MIGRATION_FAILED"
    default_message = "a schema migration failed and was rolled back"


class SchemaNotCurrentError(TrinetraError):
    code = "DB_SCHEMA_NOT_CURRENT"
    default_message = "the database schema is not at the revision this build requires"


def _config(script_location: Path, connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(script_location))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def script_directory(script_location: Path = MIGRATIONS_DIR) -> ScriptDirectory:
    return ScriptDirectory.from_config(_config(script_location))


def migration_files(script_location: Path = MIGRATIONS_DIR) -> list[Path]:
    versions = script_location / "versions"
    return sorted(p for p in versions.glob("*.py") if p.name != "__init__.py")


def validate_scripts(script_location: Path = MIGRATIONS_DIR) -> str:
    """Return the single head revision, or raise MigrationError for any structural problem."""
    script = script_directory(script_location)
    heads = script.get_heads()
    if len(heads) != 1:
        raise MigrationError(f"expected exactly one migration head, found {len(heads)}")
    for revision in script.walk_revisions():
        file_name = Path(revision.path).name
        match = FILE_PATTERN.fullmatch(file_name)
        if match is None or match.group("rev") != revision.revision:
            raise MigrationError(f"revision id does not match its file name: {file_name}")
        if revision.branch_labels or revision.dependencies:
            raise MigrationError(f"branches and dependencies are not allowed: {file_name}")
    return heads[0]


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def upgrade_to_head(store: CoreStore, script_location: Path = MIGRATIONS_DIR) -> str | None:
    """Apply every pending migration, one transaction each. Returns the revision now current."""
    store.lock.assert_held()
    validate_scripts(script_location)
    with store.database.engine.connect() as connection:
        known = {r.revision for r in script_directory(script_location).walk_revisions()}
        current = current_revision(connection)
        connection.rollback()  # end the read's autobegin; Alembic starts its own transactions
        if current is not None and current not in known:
            raise SchemaNotCurrentError(
                "the database is at a revision this build does not know (newer database?)",
                details={"database_revision": current},
            )
        try:
            with ddl_window(connection):
                command.upgrade(_config(script_location, connection), "head")
        except Exception as exc:
            raise MigrationError(details={"error_type": type(exc).__name__}) from exc
        revision = current_revision(connection)
        connection.rollback()  # end the read; every migration already committed itself
        return revision


def require_current(store: CoreStore, script_location: Path = MIGRATIONS_DIR) -> str:
    """Fail closed unless the database is exactly at the head this build expects."""
    head = validate_scripts(script_location)
    with store.database.engine.connect() as connection:
        current = current_revision(connection)
        connection.rollback()
    if current != head:
        raise SchemaNotCurrentError(details={"database_revision": str(current), "required_revision": head})
    return head


__all__ = [
    "MIGRATIONS_DIR",
    "MigrationError",
    "SchemaNotCurrentError",
    "current_revision",
    "migration_files",
    "require_current",
    "script_directory",
    "upgrade_to_head",
    "validate_scripts",
]
