"""Shared persistence test support: raw-SQL access and valid-row builders (no repositories involved)."""

from __future__ import annotations

import hashlib
import json
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
