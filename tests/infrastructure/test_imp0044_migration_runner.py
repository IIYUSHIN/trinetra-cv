"""IMP-0044 tests: Alembic bootstrap and migration runner (synthetic script directories)."""

from __future__ import annotations

import ast
import shutil
import textwrap
from pathlib import Path

import pytest
from sqlalchemy import text

from trinetra.infrastructure import migration_runner as runner
from trinetra.infrastructure.migration_runner import (
    MIGRATIONS_DIR,
    MigrationError,
    SchemaNotCurrentError,
    current_revision,
    migration_files,
    require_current,
    upgrade_to_head,
    validate_scripts,
)
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.unit_of_work import CoreStore

HEADER = "from alembic import op\nimport sqlalchemy as sa\n"


def make_scripts(base: Path, specs: dict[str, tuple[str | None, str]]) -> Path:
    """specs: file stem -> (down_revision, upgrade body)."""
    location = base / "scripts"
    (location / "versions").mkdir(parents=True)
    shutil.copy(MIGRATIONS_DIR / "env.py", location / "env.py")
    for stem, (down, body) in specs.items():
        revision = stem[:4]
        source = (
            f"{HEADER}\nrevision = {revision!r}\ndown_revision = {down!r}\nbranch_labels = None\n"
            f"depends_on = None\n\n\ndef upgrade() -> None:\n"
            f"{textwrap.indent(textwrap.dedent(body), '    ')}\n"
        )
        (location / "versions" / f"{stem}.py").write_text(source)
    return location


TWO = {
    "0001_first": (None, "op.create_table('a', sa.Column('x', sa.Integer, primary_key=True))"),
    "0002_second": ("0001", "op.create_table('b', sa.Column('y', sa.Integer, primary_key=True))"),
}


@pytest.fixture
def store(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    with CoreStore.open(DatabaseConfig(root)) as opened:
        yield opened


def tables(store: CoreStore) -> set[str]:
    with store.database.engine.connect() as c:
        names = {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        c.rollback()
    return names - {"alembic_version"}


def test_an_empty_packaged_chain_is_not_a_valid_head_yet():
    if not migration_files():  # until IMP-0045 lands the first revision
        with pytest.raises(MigrationError, match="found 0"):
            validate_scripts()


def test_upgrade_creates_tables_and_records_the_head(store, tmp_path):
    location = make_scripts(tmp_path, TWO)
    assert upgrade_to_head(store, location) == "0002"
    assert tables(store) == {"a", "b"}
    assert require_current(store, location) == "0002"


def test_upgrade_is_idempotent(store, tmp_path):
    location = make_scripts(tmp_path, TWO)
    upgrade_to_head(store, location)
    assert upgrade_to_head(store, location) == "0002"
    assert tables(store) == {"a", "b"}


def test_an_incremental_upgrade_applies_only_the_missing_revisions(store, tmp_path):
    location = make_scripts(tmp_path, {"0001_first": TWO["0001_first"]})
    assert upgrade_to_head(store, location) == "0001"
    (location / "versions" / "0002_second.py").write_text(
        make_scripts(tmp_path / "other", TWO).joinpath("versions", "0002_second.py").read_text()
    )
    assert upgrade_to_head(store, location) == "0002"
    assert tables(store) == {"a", "b"}


def test_require_current_fails_closed_when_behind_or_empty(store, tmp_path):
    location = make_scripts(tmp_path, TWO)
    with pytest.raises(SchemaNotCurrentError) as info:
        require_current(store, location)
    assert info.value.code == "DB_SCHEMA_NOT_CURRENT" and info.value.details["database_revision"] == "None"
    first_only = make_scripts(tmp_path / "x", {"0001_first": TWO["0001_first"]})
    upgrade_to_head(store, first_only)
    with pytest.raises(SchemaNotCurrentError) as behind:
        require_current(store, location)
    assert behind.value.details["database_revision"] == "0001"
    assert behind.value.details["required_revision"] == "0002"


def test_a_database_newer_than_the_code_refuses_startup(store, tmp_path):
    upgrade_to_head(store, make_scripts(tmp_path, TWO))
    older = make_scripts(tmp_path / "older", {"0001_first": TWO["0001_first"]})
    with pytest.raises(SchemaNotCurrentError, match="does not know"):
        upgrade_to_head(store, older)
    with pytest.raises(SchemaNotCurrentError):
        require_current(store, older)


def test_a_failing_migration_rolls_back_its_own_changes_and_the_revision(store, tmp_path):
    bad = {
        "0001_first": TWO["0001_first"],
        "0002_broken": (
            "0001",
            "op.create_table('half', sa.Column('z', sa.Integer))\nop.execute('THIS IS NOT SQL')",
        ),
    }
    location = make_scripts(tmp_path, bad)
    with pytest.raises(MigrationError) as info:
        upgrade_to_head(store, location)
    assert info.value.code == "DB_MIGRATION_FAILED"
    assert tables(store) == {"a"}  # 0001 stayed applied; the broken 0002 left nothing behind
    with store.database.engine.connect() as c:
        assert current_revision(c) == "0001"
        c.rollback()


def test_multiple_heads_are_refused(tmp_path):
    forked = {
        "0001_first": TWO["0001_first"],
        "0002_left": ("0001", "pass"),
        "0003_right": ("0001", "pass"),
    }
    with pytest.raises(MigrationError, match="exactly one"):
        validate_scripts(make_scripts(tmp_path, forked))


@pytest.mark.parametrize("stem", ["first", "1_first", "0001-first", "0001_First", "0001_first.bak"])
def test_badly_named_migration_files_are_refused(tmp_path, stem):
    location = make_scripts(tmp_path, {"0001_first": TWO["0001_first"]})
    (location / "versions" / f"{stem}.py").write_text("revision = 'x'\ndown_revision = '0001'\n")
    with pytest.raises(MigrationError):
        validate_scripts(location)


def test_a_revision_id_that_disagrees_with_its_file_name_is_refused(tmp_path):
    location = make_scripts(tmp_path, {"0001_first": TWO["0001_first"]})
    path = location / "versions" / "0001_first.py"
    path.write_text(path.read_text().replace("revision = '0001'", "revision = '0009'"))
    with pytest.raises(MigrationError, match="does not match"):
        validate_scripts(location)


def test_branch_labels_are_refused(tmp_path):
    location = make_scripts(tmp_path, {"0001_first": TWO["0001_first"]})
    path = location / "versions" / "0001_first.py"
    path.write_text(path.read_text().replace("branch_labels = None", "branch_labels = ('x',)"))
    with pytest.raises(MigrationError, match="branches"):
        validate_scripts(location)


def test_env_py_refuses_to_run_without_the_runner_connection():
    namespace_source = (MIGRATIONS_DIR / "env.py").read_text()
    assert "migrations run only through trinetra.infrastructure.migration_runner" in namespace_source
    code_names = {n.id for n in ast.walk(ast.parse(namespace_source)) if isinstance(n, ast.Name)}
    code_attrs = {n.attr for n in ast.walk(ast.parse(namespace_source)) if isinstance(n, ast.Attribute)}
    assert not {"fileConfig", "environ"} & (code_names | code_attrs)
    assert not {"is_offline_mode", "run_migrations_offline"} & (code_names | code_attrs)


def test_there_is_no_downgrade_entry_point():
    public = set(runner.__all__)
    assert not any("down" in name.lower() for name in public)
    assert not hasattr(runner, "downgrade")


def test_runtime_record_code_never_imports_the_migration_authority():
    package = Path(runner.__file__).resolve().parent.parent
    allowed = {"migration_runner.py", "unit_of_work.py"}
    offenders = []
    for path in package.rglob("*.py"):
        if "migrations" in path.parts or path.name in allowed:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("migration_runner" in n or n.endswith("infrastructure.migrations") for n in names):
                offenders.append(str(path.relative_to(package)))
    assert offenders == []


def test_env_module_imports_harmlessly_outside_an_alembic_run():
    import importlib.util

    spec = importlib.util.spec_from_file_location("migration_env_probe", MIGRATIONS_DIR / "env.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not raise or touch any database
    assert callable(module.run) and callable(module.main)
