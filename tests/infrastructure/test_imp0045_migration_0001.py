"""IMP-0045 tests: migration 0001 (identity / assets / audit-journal schema), exercised with raw SQL."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from persistence_support import (
    INSERT_ASSET,
    INSERT_AUDIT,
    INSERT_BLOB,
    advance_journal,
    asset_row,
    audit_row,
    blob_row,
    sha,
)
from trinetra.infrastructure.migration_runner import (
    MIGRATIONS_DIR,
    current_revision,
    migration_files,
    require_current,
    validate_scripts,
)

GOLDEN = Path(__file__).parent / "golden" / "migration_manifest.json"
EXPECTED_TABLES = {
    "alembic_version", "blob_locators", "asset_records", "journal_state", "audit_events",
    "audit_event_subjects", "audit_event_contexts",
}
AUTHORITATIVE = ["asset_records", "audit_events", "audit_event_subjects", "audit_event_contexts"]


# ------------------------------------------------------------------------ migration identity
def test_the_chain_has_exactly_one_head_named_0001():
    assert validate_scripts() == "0001"
    assert [p.name for p in migration_files()] == ["0001_core_identity_assets.py"]


def test_migration_identity_is_committed_to_source():
    recorded = json.loads(GOLDEN.read_text())
    actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in migration_files()}
    assert recorded == actual, "a committed migration changed: that needs an explicit REV, not an edit"


def test_upgrade_records_the_head(store):
    assert require_current(store) == "0001"
    with store.database.engine.connect() as connection:
        assert current_revision(connection) == "0001"
        connection.rollback()


def test_no_unexpected_tables_exist(raw):
    names = {r[0] for r in raw.rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert names == EXPECTED_TABLES


def test_every_table_is_strict(raw):
    query = "SELECT name, strict FROM pragma_table_list WHERE schema = 'main' AND name NOT LIKE 'sqlite_%'"
    flags = dict(raw.rows(query))
    assert all(flags[name] == 1 for name in EXPECTED_TABLES - {"alembic_version"})


# ------------------------------------------------------------------------ happy path
def test_valid_rows_are_accepted(raw):
    raw.run(INSERT_BLOB, blob_row())
    raw.run(INSERT_ASSET, asset_row())
    advance_journal(raw)
    raw.run(INSERT_AUDIT, audit_row(1))
    assert raw.rows("SELECT count(*) FROM asset_records") == [(1,)]
    assert raw.rows("SELECT count(*) FROM audit_events") == [(1,)]


# ------------------------------------------------------------------------ append-only at the database
@pytest.fixture
def populated(raw):
    raw.run(INSERT_BLOB, blob_row())
    raw.run(INSERT_ASSET, asset_row())
    advance_journal(raw)
    raw.run(INSERT_AUDIT, audit_row(1))
    raw.run("INSERT INTO audit_event_subjects VALUES (:e, 0, 'asset_record', :o, :d)",
            {"e": audit_row(1)["record_id"], "o": asset_row()["record_id"], "d": sha(b"d")})
    raw.run("INSERT INTO audit_event_contexts VALUES (:e, 0, 'ingest_1')", {"e": audit_row(1)["record_id"]})
    return raw


@pytest.mark.parametrize("table", AUTHORITATIVE)
def test_update_is_rejected_on_every_authoritative_table(populated, table):
    column = {"asset_records": "supplied_name", "audit_events": "event_type",
              "audit_event_subjects": "object_id", "audit_event_contexts": "context_id"}[table]
    statement = "UPDATE " + table + " SET " + column + " = 'x'"  # noqa: S608 - fixed test table names
    assert "append-only" in populated.attempt(statement)


@pytest.mark.parametrize("table", AUTHORITATIVE)
def test_delete_is_rejected_on_every_authoritative_table(populated, table):
    assert "append-only" in populated.attempt("DELETE FROM " + table)  # noqa: S608 - fixed test table names


def test_insert_or_replace_cannot_overwrite_an_asset_record(populated):
    replacement = {**asset_row(), "supplied_name": "evil.pt"}
    forged = {**json.loads(asset_row()["body_json"]), "supplied_name": "evil.pt"}
    replacement["body_json"] = json.dumps(forged, sort_keys=True, separators=(",", ":"))
    sql = INSERT_ASSET.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "append-only" in populated.attempt(sql, replacement)
    assert populated.rows("SELECT supplied_name FROM asset_records") == [("model.pt",)]


def test_insert_or_replace_cannot_overwrite_an_audit_event(populated):
    sql = INSERT_AUDIT.replace("INSERT INTO", "INSERT OR REPLACE INTO")
    assert "append-only" in populated.attempt(sql, {**audit_row(1), "outcome": "FAILURE"})
    assert populated.rows("SELECT outcome FROM audit_events") == [("SUCCESS",)]


def test_replace_cannot_overwrite_side_table_rows(populated):
    sql = "INSERT OR REPLACE INTO audit_event_contexts VALUES (:e, 0, 'forged')"
    assert "append-only" in populated.attempt(sql, {"e": audit_row(1)["record_id"]})
    assert populated.rows("SELECT context_id FROM audit_event_contexts") == [("ingest_1",)]


def test_dropping_a_parent_is_blocked_by_foreign_keys(populated):
    assert "FOREIGN KEY" in populated.attempt("DELETE FROM blob_locators")


# ------------------------------------------------------------------------ constraints
@pytest.mark.parametrize(("field", "value"), [
    ("blob_digest", "sha256:" + "A" * 64), ("blob_digest", "sha256:" + "a" * 63),
    ("blob_digest", "md5:" + "a" * 64), ("blob_digest", "sha256:" + "a" * 65),
    ("blob_digest", "sha256:" + "g" * 64), ("size_bytes", -1), ("backend_type", "S3"),
    ("locator", "/abs/path"), ("locator", "../x"), ("locator", "cas/UP"), ("verification_status", "OK"),
])
def test_blob_locator_checks(raw, field, value):
    assert "CHECK" in raw.attempt(INSERT_BLOB, {**blob_row(), field: value})


@pytest.mark.parametrize(("field", "value"), [
    ("record_id", "asset_" + "A" * 32), ("record_id", "asset_" + "a" * 31),
    ("record_id", "model_" + "a" * 32),
    ("asset_kind", "Model"), ("asset_kind", ""), ("created_by", "Core"), ("media_type", "x"),
    ("body_digest", "sha256:zz"), ("created_at", "2026"),
])
def test_asset_record_checks(raw, field, value):
    raw.run(INSERT_BLOB, blob_row())
    assert "CHECK" in raw.attempt(INSERT_ASSET, {**asset_row(), field: value})


@pytest.mark.parametrize(("field", "value"), [
    ("record_id", "asset_" + "0" * 32), ("asset_kind", "dataset"), ("size_bytes", 99),
    ("media_type", "text/plain"), ("supplied_name", "other.pt"), ("created_by", "intruder"),
    ("created_at", "2027-01-01T00:00:00Z"),
])
def test_columns_cannot_disagree_with_the_digestable_body(raw, field, value):
    raw.run(INSERT_BLOB, blob_row())
    assert raw.attempt(INSERT_ASSET, {**asset_row(), field: value}) != ""


def test_body_must_be_json(raw):
    raw.run(INSERT_BLOB, blob_row())
    assert "CHECK" in raw.attempt(INSERT_ASSET, {**asset_row(), "body_json": "{not json"})


def test_an_asset_cannot_reference_a_missing_blob(raw):
    assert "blob identity" in raw.attempt(INSERT_ASSET, asset_row())


def test_an_asset_size_must_equal_its_blob_size(raw):
    raw.run(INSERT_BLOB, blob_row(b"hello"))
    row = asset_row(data=b"hello")
    body = {**json.loads(row["body_json"]), "size_bytes": 7}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    lie = {**row, "size_bytes": 7, "body_json": text, "body_digest": sha(text.encode())}
    assert "blob identity" in raw.attempt(INSERT_ASSET, lie)


def test_a_blob_identity_can_never_change_but_status_can(raw):
    raw.run(INSERT_BLOB, blob_row())
    digest = blob_row()["blob_digest"]
    resize = "UPDATE blob_locators SET size_bytes = 99 WHERE blob_digest = :d"
    assert "never change" in raw.attempt(resize, {"d": digest})
    assert "never change" in raw.attempt(
        "UPDATE blob_locators SET blob_digest = :n WHERE blob_digest = :d", {"d": digest, "n": sha(b"other")}
    )
    restatus = "UPDATE blob_locators SET verification_status = 'MISSING' WHERE blob_digest = :d"
    assert raw.attempt(restatus, {"d": digest}) == ""


def test_one_blob_may_back_many_asset_records(raw):
    raw.run(INSERT_BLOB, blob_row())
    raw.run(INSERT_ASSET, asset_row(1, name="a.pt"))
    raw.run(INSERT_ASSET, asset_row(2, name="b.pt"))
    assert raw.rows("SELECT count(*), count(DISTINCT blob_digest) FROM asset_records") == [(2, 1)]


# ------------------------------------------------------------------------ audit journal rules
def test_audit_sequence_must_be_allocated_first(raw):
    raw.run("INSERT INTO journal_state VALUES ('local_main', 1)")
    assert "not allocated" in raw.attempt(INSERT_AUDIT, audit_row(1))


def test_audit_sequence_cannot_skip(raw):
    advance_journal(raw)
    advance_journal(raw)  # two allocations, nothing written: neither 1 (stale) nor 2 (gap) may be inserted
    assert "not allocated" in raw.attempt(INSERT_AUDIT, audit_row(1))
    assert raw.attempt(INSERT_AUDIT, audit_row(2)) != ""
    assert raw.rows("SELECT count(*) FROM audit_events") == [(0,)]


def test_audit_sequence_cannot_be_reused(raw):
    advance_journal(raw)
    raw.run(INSERT_AUDIT, audit_row(1))
    assert raw.attempt(INSERT_AUDIT, {**audit_row(1), "record_id": "audit_" + "9" * 32}) != ""


def test_audit_link_must_continue_the_previous_event(raw):
    advance_journal(raw)
    raw.run(INSERT_AUDIT, audit_row(1))
    advance_journal(raw)
    assert "does not continue" in raw.attempt(INSERT_AUDIT, audit_row(2, prev=sha(b"forged")))
    assert raw.attempt(INSERT_AUDIT, audit_row(2, prev=audit_row(1)["link_digest"])) == ""


def test_journal_state_only_advances_by_one_and_never_dies(raw):
    advance_journal(raw)
    assert "advances by one" in raw.attempt("UPDATE journal_state SET next_sequence = next_sequence + 5")
    assert "advances by one" in raw.attempt("UPDATE journal_state SET next_sequence = 1")
    assert "advances by one" in raw.attempt("UPDATE journal_state SET journal_id = 'other'")
    assert "never deleted" in raw.attempt("DELETE FROM journal_state")


def test_an_audit_event_needs_an_existing_journal(raw):
    assert "not allocated" in raw.attempt(INSERT_AUDIT, audit_row(1, journal="nowhere"))


@pytest.mark.parametrize(("field", "value"), [
    ("outcome", "OK"), ("event_type", "Bad Type"), ("record_id", "audit_" + "G" * 32),
    ("journal_sequence", 0), ("prev_link_digest", "sha256:short"),
])
def test_audit_event_checks(raw, field, value):
    advance_journal(raw)
    assert raw.attempt(INSERT_AUDIT, {**audit_row(1), field: value}) != ""


def test_audit_columns_cannot_disagree_with_the_body(raw):
    advance_journal(raw)
    body = json.loads(audit_row(1)["body_json"])
    body["outcome"] = "FAILURE"
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    lie = {**audit_row(1), "body_json": text, "body_digest": sha(text.encode())}
    assert "CHECK" in raw.attempt(INSERT_AUDIT, lie)


def test_two_journals_are_independent(raw):
    advance_journal(raw, "journal_a")
    advance_journal(raw, "journal_b")
    raw.run(INSERT_AUDIT, audit_row(1, "journal_a"))
    assert raw.attempt(INSERT_AUDIT, audit_row(1, "journal_b", rid=7)) == ""


def test_the_migration_is_pure_ddl_for_the_declared_tables():
    source = (MIGRATIONS_DIR / "versions" / "0001_core_identity_assets.py").read_text()
    for forbidden in ("DROP ", "ALTER TABLE", "INSERT INTO", "DELETE FROM"):
        assert forbidden not in source
