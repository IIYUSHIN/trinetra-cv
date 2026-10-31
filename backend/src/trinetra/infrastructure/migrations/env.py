"""Alembic environment: online mode only, on the connection handed in by the migration runner.

No alembic.ini, no logging reconfiguration, no URL from the environment, no offline SQL generation:
a migration runs on the Core-owned connection or not at all. Importing this module outside an Alembic
run (for example the import-everything checks) is a harmless no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import context

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        transactional_ddl=True,
        transaction_per_migration=True,
        version_table="alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def main() -> None:
    try:
        config = context.config
    except AttributeError:  # not executing inside an Alembic run
        return
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("migrations run only through trinetra.infrastructure.migration_runner")
    run(connection)


main()
