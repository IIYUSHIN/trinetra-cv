"""Asset repository: create and read AssetRecords (IMP-0055).

Runs only inside a Core UnitOfWork (so only the sole writer can call it). `add` registers the blob
locator if this content is new to the database and inserts the AssetRecord whose canonical JSON body,
digest and mirrored columns the database re-checks itself (migration 0001). `get` never trusts what it
reads: it re-parses the stored body, re-derives its digest and compares every mirrored column, so a
row edited behind the repository's back fails closed instead of being believed. Records are append-only;
there is no update or delete. No local path is ever stored or returned.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trinetra.domain.asset_record import AssetRecord
from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError
from trinetra.infrastructure.cas_layout import locator_for
from trinetra.timeutil import Clock, SystemClock, format_utc, truncate_to_seconds

if TYPE_CHECKING:
    from trinetra.infrastructure.unit_of_work import UnitOfWork

BACKEND_FILESYSTEM: Final = "FILESYSTEM_CAS"

_COLUMNS: Final = (
    "record_id, asset_kind, blob_digest, size_bytes, media_type, supplied_name, created_at, created_by, "
    "body_json, body_digest"
)


class AssetExistsError(TrinetraError):
    code = "ASSET_ALREADY_EXISTS"
    default_message = "an AssetRecord with this id already exists"


class AssetIntegrityError(TrinetraError):
    code = "ASSET_RECORD_INTEGRITY"
    default_message = "a stored AssetRecord does not agree with its own digest or columns"


class AssetQueryError(TrinetraError):
    code = "ASSET_QUERY_INVALID"
    default_message = "the asset list query is invalid"


MAX_PAGE: Final = 200
MAX_CURSOR: Final = 256
DEFAULT_PAGE: Final = 50


@dataclass(frozen=True)
class AssetPage:
    items: tuple[AssetRecord, ...]
    next_cursor: str | None


def encode_cursor(created_at: str, record_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{record_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Strictly parse an opaque position token; anything else is a query error, never a guess."""
    if not cursor.isascii() or len(cursor) > MAX_CURSOR:
        msg = "the page cursor is not valid"
        raise AssetQueryError(msg)
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        created_at, record_id = text.split("|")
        UtcTimestamp.parse(created_at)
        AssetId.parse(record_id)
    except (ValueError, binascii.Error, TrinetraError) as exc:
        msg = "the page cursor is not valid"
        raise AssetQueryError(msg) from exc
    if encode_cursor(created_at, record_id) != cursor:
        msg = "the page cursor is not in canonical form"
        raise AssetQueryError(msg)
    return created_at, record_id


class AssetRepository:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or SystemClock()

    def add(self, uow: UnitOfWork, record: AssetRecord) -> None:
        connection = uow.connection
        if self._exists(uow, record.record_id):
            msg = "an AssetRecord with this id already exists"
            raise AssetExistsError(msg)
        now = format_utc(truncate_to_seconds(self._clock.now()))
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO blob_locators (blob_digest, size_bytes, backend_type, locator, "
            "verification_status, last_verified_at) VALUES (?, ?, ?, ?, 'VERIFIED', ?)",
            (record.blob_digest.to_wire(), record.size_bytes, BACKEND_FILESYSTEM,
             locator_for(record.blob_digest), now),
        )
        body = record.body_json()
        connection.exec_driver_sql(
            f"INSERT INTO asset_records ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            (str(record.record_id), record.asset_kind.value, record.blob_digest.to_wire(), record.size_bytes,
             record.media_type, record.supplied_name, record.created_at.to_wire(), str(record.created_by),
             body, record.canonical_digest()),
        )

    def get(self, uow: UnitOfWork, asset_id: AssetId) -> AssetRecord | None:
        row = uow.connection.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM asset_records WHERE record_id = ?",  # noqa: S608
            (str(asset_id),),
        ).fetchone()
        return None if row is None else self._verified(tuple(row))

    def list_page(
        self,
        uow: UnitOfWork,
        *,
        kind: AssetKind | None = None,
        blob_digest: DigestSha256 | None = None,
        limit: int = DEFAULT_PAGE,
        after: str | None = None,
    ) -> AssetPage:
        """Keyset pagination ordered by (created_at, record_id); every filter is a bound parameter."""
        if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE:
            msg = f"limit must be within [1, {MAX_PAGE}]"
            raise AssetQueryError(msg)
        clauses: list[str] = []
        params: list[Any] = []
        if kind is not None:
            clauses.append("asset_kind = ?")
            params.append(kind.value)
        if blob_digest is not None:
            clauses.append("blob_digest = ?")
            params.append(blob_digest.to_wire())
        if after is not None:
            created_at, record_id = decode_cursor(after)
            clauses.append("(created_at > ? OR (created_at = ? AND record_id > ?))")
            params.extend([created_at, created_at, record_id])
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = uow.connection.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM asset_records {where}ORDER BY created_at, record_id LIMIT ?",  # noqa: S608
            (*params, limit + 1),
        ).fetchall()
        page = [self._verified(tuple(row)) for row in rows[:limit]]
        more = len(rows) > limit
        last = page[-1] if page else None
        cursor = encode_cursor(last.created_at.to_wire(), str(last.record_id)) if more and last else None
        return AssetPage(tuple(page), cursor)

    def exists(self, uow: UnitOfWork, asset_id: AssetId) -> bool:
        return self._exists(uow, asset_id)

    def count(self, uow: UnitOfWork) -> int:
        row = uow.connection.exec_driver_sql("SELECT count(*) FROM asset_records").fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _exists(uow: UnitOfWork, asset_id: AssetId) -> bool:
        row = uow.connection.exec_driver_sql(
            "SELECT 1 FROM asset_records WHERE record_id = ?", (str(asset_id),)
        ).fetchone()
        return row is not None

    @staticmethod
    def _verified(row: tuple[Any, ...]) -> AssetRecord:
        (record_id, kind, blob, size, media, name, created_at, created_by, body, body_digest) = row
        try:
            record = AssetRecord.model_validate_json(body)
        except ValidationError as exc:
            msg = "the stored body is not a valid AssetRecord"
            raise AssetIntegrityError(msg) from exc
        expected = (
            str(record.record_id), record.asset_kind.value, record.blob_digest.to_wire(), record.size_bytes,
            record.media_type, record.supplied_name, record.created_at.to_wire(), str(record.created_by),
        )
        if (record_id, kind, blob, size, media, name, created_at, created_by) != expected:
            msg = "a stored column disagrees with the record body"
            raise AssetIntegrityError(msg)
        if record.body_json() != body:
            msg = "the stored body is not in canonical form"
            raise AssetIntegrityError(msg)
        if record.canonical_digest() != body_digest:
            msg = "the stored body digest does not match the body"
            raise AssetIntegrityError(msg)
        return record
