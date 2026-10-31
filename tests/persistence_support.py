"""Shared persistence test support: raw-SQL access and valid-row builders (no repositories involved)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import DatabaseError

from trinetra.infrastructure.unit_of_work import CoreStore


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def opaque(prefix: str, n: int) -> str:
    return f"{prefix}_{n:032x}"


class RawDb:
    """Executes literal SQL on the real database, bypassing every repository."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    def run(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self.store.unit_of_work() as uow:
            uow.connection.exec_driver_sql(sql, params or {})
            uow.commit()

    def attempt(self, sql: str, params: dict[str, Any] | None = None) -> str:
        """Run one statement; return the database's refusal text, or '' if it was accepted."""
        try:
            self.run(sql, params)
        except DatabaseError as exc:
            return str(exc.orig)
        return ""

    def rows(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        with self.store.unit_of_work() as uow:
            data = [tuple(r) for r in uow.connection.exec_driver_sql(sql, params or {})]
            uow.rollback()
        return data


# --------------------------------------------------------------------- valid row builders
def blob_row(data: bytes = b"hello") -> dict[str, Any]:
    digest = sha(data)
    hexpart = digest[7:]
    return {
        "blob_digest": digest,
        "size_bytes": len(data),
        "backend_type": "FILESYSTEM_CAS",
        "locator": f"cas/sha256/{hexpart[:2]}/{hexpart[2:4]}/{hexpart}",
        "verification_status": "VERIFIED",
        "last_verified_at": None,
    }


INSERT_BLOB = (
    "INSERT INTO blob_locators (blob_digest, size_bytes, backend_type, locator, verification_status, "
    "last_verified_at) VALUES (:blob_digest, :size_bytes, :backend_type, :locator, :verification_status, "
    ":last_verified_at)"
)


def asset_row(n: int = 1, data: bytes = b"hello", name: str = "model.pt") -> dict[str, Any]:
    blob = blob_row(data)
    body = {
        "schema_name": "trinetra.asset_record",
        "schema_version": 1,
        "record_id": opaque("asset", n),
        "created_at": "2026-10-31T00:00:00Z",
        "created_by": "core",
        "asset_kind": "model",
        "blob_digest": blob["blob_digest"],
        "size_bytes": len(data),
        "media_type": "application/octet-stream",
        "supplied_name": name,
        "parent_asset_refs": [],
        "source_metadata_refs": [],
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {
        "record_id": body["record_id"], "asset_kind": "model", "blob_digest": blob["blob_digest"],
        "size_bytes": len(data), "media_type": "application/octet-stream", "supplied_name": name,
        "created_at": body["created_at"], "created_by": "core", "body_json": text,
        "body_digest": sha(text.encode()),
    }


INSERT_ASSET = (
    "INSERT INTO asset_records (record_id, asset_kind, blob_digest, size_bytes, media_type, supplied_name, "
    "created_at, created_by, body_json, body_digest) VALUES (:record_id, :asset_kind, :blob_digest, "
    ":size_bytes, :media_type, :supplied_name, :created_at, :created_by, :body_json, :body_digest)"
)


def audit_row(
    n: int, journal: str = "local_main", prev: str | None = None, rid: int | None = None
) -> dict[str, Any]:
    body = {
        "schema_name": "trinetra.audit_event", "schema_version": 1, "record_id": opaque("audit", rid or n),
        "created_at": "2026-10-31T00:00:00Z", "created_by": "core", "journal_id": journal,
        "journal_sequence": n, "event_type": "asset_ingested", "outcome": "SUCCESS",
        "subject_refs": [], "context_ids": [], "reason_code": None, "details": {},
    }
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {
        "record_id": body["record_id"], "journal_id": journal, "journal_sequence": n,
        "event_type": "asset_ingested", "outcome": "SUCCESS", "created_at": body["created_at"],
        "body_json": text, "body_digest": sha(text.encode()),
        "prev_link_digest": prev or sha(b"genesis:" + journal.encode()),
        "link_digest": sha(f"link:{journal}:{n}".encode()),
    }


INSERT_AUDIT = (
    "INSERT INTO audit_events (record_id, journal_id, journal_sequence, event_type, outcome, created_at, "
    "body_json, body_digest, prev_link_digest, link_digest) VALUES (:record_id, :journal_id, "
    ":journal_sequence, :event_type, :outcome, :created_at, :body_json, :body_digest, :prev_link_digest, "
    ":link_digest)"
)


def advance_journal(raw: RawDb, journal: str = "local_main") -> None:
    """What the audit writer does before an insert: create the journal on first use, then allocate."""
    if not raw.rows("SELECT 1 FROM journal_state WHERE journal_id = :j", {"j": journal}):
        raw.run("INSERT INTO journal_state (journal_id, next_sequence) VALUES (:j, 1)", {"j": journal})
    bump = "UPDATE journal_state SET next_sequence = next_sequence + 1 WHERE journal_id = :j"
    raw.run(bump, {"j": journal})


Builder = Callable[..., dict[str, Any]]


def tamper(store: CoreStore, *statements: str | tuple[str, tuple[Any, ...]]) -> None:
    """Out-of-band damage: a plain sqlite3 connection straight to the file (no Core engine, no guards).

    Models someone with write access to the database file: foreign keys and CHECKs are switched off and
    the append-only triggers dropped before the given SQL runs. Never available to production code.
    """
    guards = [
        f"{table}{suffix}"
        for table in ("audit_events", "audit_event_subjects", "audit_event_contexts", "asset_records")
        for suffix in ("_no_update", "_no_delete", "_no_replace")
    ] + ["journal_state_monotonic", "journal_state_no_delete"]
    connection = sqlite3.connect(store.database.path, isolation_level=None, timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
        for name in guards:
            connection.execute(f"DROP TRIGGER IF EXISTS {name}")
        for item in statements:
            sql, params = (item, ()) if isinstance(item, str) else item
            connection.execute(sql, params)
    finally:
        connection.close()
