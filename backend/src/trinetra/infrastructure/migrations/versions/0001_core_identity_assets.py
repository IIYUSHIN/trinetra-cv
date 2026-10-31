"""0001 core identity / assets / audit-journal schema (IMP-0045).

Creates only what the persistence stage legitimately owns: operational BlobLocator and journal state,
authoritative AssetRecord and AuditEvent (+ their subject/context side tables). Authoritative tables are
append-only AT THE DATABASE: UPDATE, DELETE and INSERT OR REPLACE are all rejected by triggers, so a
direct SQL statement that bypasses the repositories fails too (IMP-0048/0049/0070). Columns that mirror
the canonical JSON body are CHECK-bound to it, so a row cannot disagree with its own digestable body.

SQLite note: DELETE triggers do not fire for REPLACE-resolved conflicts unless recursive_triggers is on,
so every authoritative table also has a BEFORE INSERT guard against an existing primary key.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DIGEST = "length({c}) = 71 AND substr({c}, 1, 7) = 'sha256:' AND substr({c}, 8) NOT GLOB '*[^0-9a-f]*'"
NAME_RULE = "length({c}) BETWEEN 1 AND 64 AND {c} NOT GLOB '*[^a-z0-9_]*' AND substr({c}, 1, 1) GLOB '[a-z]'"
ID32 = (
    "length({c}) = {n} AND substr({c}, 1, {p}) = '{prefix}_' "
    "AND substr({c}, {p} + 1) NOT GLOB '*[^0-9a-f]*'"
)


def digest(column: str) -> str:
    return DIGEST.format(c=column)


def token(column: str) -> str:
    return NAME_RULE.format(c=column)


def opaque(column: str, prefix: str) -> str:
    return ID32.format(c=column, n=len(prefix) + 33, p=len(prefix) + 1, prefix=prefix)


def guard(table: str, key: str, what: str) -> list[str]:
    """Append-only triggers: no UPDATE, no DELETE, no overwrite of an existing key (REPLACE)."""
    message = f"{table} is append-only ({what})"
    raise_it = f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    # table/key/message are literals defined in this file, never external input.
    return [
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} {raise_it}",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} {raise_it}",
        (
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "  # noqa: S608 - literal DDL
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}) {raise_it}"
        ),
    ]


SIDE_KEY = "event_id = NEW.event_id AND ordinal = NEW.ordinal"
STATEMENTS = [
    # ------------------------------------------------------------------ operational: blob locators
    f"""CREATE TABLE blob_locators (
        blob_digest TEXT NOT NULL PRIMARY KEY CHECK ({digest("blob_digest")}),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        backend_type TEXT NOT NULL CHECK (backend_type IN ('FILESYSTEM_CAS')),
        locator TEXT NOT NULL CHECK (length(locator) BETWEEN 1 AND 255 AND locator NOT GLOB '*[^0-9a-z/]*'
                                      AND substr(locator, 1, 1) <> '/'),
        verification_status TEXT NOT NULL
            CHECK (verification_status IN
                   ('UNVERIFIED', 'VERIFIED', 'MISSING', 'DIGEST_MISMATCH', 'IO_ERROR')),
        last_verified_at TEXT
    ) STRICT""",
    """CREATE TRIGGER blob_locators_identity_fixed BEFORE UPDATE ON blob_locators
        WHEN NEW.blob_digest <> OLD.blob_digest OR NEW.size_bytes <> OLD.size_bytes
        BEGIN SELECT RAISE(ABORT, 'blob identity (digest, size) can never change'); END""",
    # ------------------------------------------------------------------ authoritative: asset records
    f"""CREATE TABLE asset_records (
        record_id TEXT NOT NULL PRIMARY KEY CHECK ({opaque("record_id", "asset")}),
        asset_kind TEXT NOT NULL CHECK ({token("asset_kind")}),
        blob_digest TEXT NOT NULL REFERENCES blob_locators (blob_digest),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        media_type TEXT NOT NULL CHECK (length(media_type) BETWEEN 3 AND 127),
        supplied_name TEXT NOT NULL CHECK (length(supplied_name) BETWEEN 0 AND 1024),
        created_at TEXT NOT NULL CHECK (length(created_at) >= 20),
        created_by TEXT NOT NULL CHECK ({token("created_by")}),
        body_json TEXT NOT NULL,
        body_digest TEXT NOT NULL CHECK ({digest("body_digest")}),
        CHECK (json_valid(body_json)
               AND json_extract(body_json, '$.record_id') = record_id
               AND json_extract(body_json, '$.asset_kind') = asset_kind
               AND json_extract(body_json, '$.blob_digest') = blob_digest
               AND json_extract(body_json, '$.size_bytes') = size_bytes
               AND json_extract(body_json, '$.media_type') = media_type
               AND json_extract(body_json, '$.supplied_name') = supplied_name
               AND json_extract(body_json, '$.created_at') = created_at
               AND json_extract(body_json, '$.created_by') = created_by)
    ) STRICT""",
    "CREATE INDEX ix_asset_records_blob ON asset_records (blob_digest)",
    "CREATE INDEX ix_asset_records_created ON asset_records (created_at, record_id)",
    "CREATE INDEX ix_asset_records_kind ON asset_records (asset_kind, created_at, record_id)",
    """CREATE TRIGGER asset_records_size_matches_blob BEFORE INSERT ON asset_records
        WHEN NEW.size_bytes <> COALESCE(
            (SELECT size_bytes FROM blob_locators WHERE blob_digest = NEW.blob_digest), -1)
        BEGIN SELECT RAISE(ABORT, 'asset size differs from its blob identity'); END""",
    *guard("asset_records", "record_id = NEW.record_id", "authoritative record"),
    # ------------------------------------------------------------------ operational: journal state
    f"""CREATE TABLE journal_state (
        journal_id TEXT NOT NULL PRIMARY KEY CHECK ({token("journal_id")}),
        next_sequence INTEGER NOT NULL CHECK (next_sequence >= 1)
    ) STRICT""",
    """CREATE TRIGGER journal_state_monotonic BEFORE UPDATE ON journal_state
        WHEN NEW.journal_id <> OLD.journal_id OR NEW.next_sequence <> OLD.next_sequence + 1
        BEGIN SELECT RAISE(ABORT, 'journal sequence only ever advances by one'); END""",
    """CREATE TRIGGER journal_state_no_delete BEFORE DELETE ON journal_state
        BEGIN SELECT RAISE(ABORT, 'journals are never deleted'); END""",
    # ------------------------------------------------------------------ authoritative: audit events
    f"""CREATE TABLE audit_events (
        record_id TEXT NOT NULL PRIMARY KEY CHECK ({opaque("record_id", "audit")}),
        journal_id TEXT NOT NULL REFERENCES journal_state (journal_id),
        journal_sequence INTEGER NOT NULL CHECK (journal_sequence >= 1),
        event_type TEXT NOT NULL CHECK ({token("event_type")}),
        outcome TEXT NOT NULL CHECK (outcome IN ('SUCCESS', 'FAILURE')),
        created_at TEXT NOT NULL CHECK (length(created_at) >= 20),
        body_json TEXT NOT NULL,
        body_digest TEXT NOT NULL CHECK ({digest("body_digest")}),
        prev_link_digest TEXT NOT NULL CHECK ({digest("prev_link_digest")}),
        link_digest TEXT NOT NULL CHECK ({digest("link_digest")}),
        UNIQUE (journal_id, journal_sequence),
        CHECK (json_valid(body_json)
               AND json_extract(body_json, '$.record_id') = record_id
               AND json_extract(body_json, '$.journal_id') = journal_id
               AND json_extract(body_json, '$.journal_sequence') = journal_sequence
               AND json_extract(body_json, '$.event_type') = event_type
               AND json_extract(body_json, '$.outcome') = outcome
               AND json_extract(body_json, '$.created_at') = created_at)
    ) STRICT""",
    """CREATE TRIGGER audit_events_sequence_is_gapless BEFORE INSERT ON audit_events
        WHEN NEW.journal_sequence <>
             COALESCE((SELECT MAX(journal_sequence) FROM audit_events
                       WHERE journal_id = NEW.journal_id), 0) + 1
        BEGIN SELECT RAISE(ABORT, 'audit sequence must be the next one in its journal'); END""",
    """CREATE TRIGGER audit_events_sequence_was_allocated BEFORE INSERT ON audit_events
        WHEN NEW.journal_sequence <>
             COALESCE((SELECT next_sequence FROM journal_state WHERE journal_id = NEW.journal_id), 0) - 1
        BEGIN SELECT RAISE(ABORT, 'audit sequence was not allocated from journal_state'); END""",
    """CREATE TRIGGER audit_events_link_continues BEFORE INSERT ON audit_events
        WHEN NEW.journal_sequence > 1 AND NEW.prev_link_digest <>
             COALESCE((SELECT link_digest FROM audit_events
                       WHERE journal_id = NEW.journal_id AND journal_sequence = NEW.journal_sequence - 1), '')
        BEGIN SELECT RAISE(ABORT, 'audit link does not continue the previous event'); END""",
    *guard("audit_events", "record_id = NEW.record_id", "authoritative record"),
    f"""CREATE TABLE audit_event_subjects (
        event_id TEXT NOT NULL REFERENCES audit_events (record_id),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        object_type TEXT NOT NULL CHECK ({token("object_type")}),
        object_id TEXT NOT NULL CHECK (length(object_id) BETWEEN 1 AND 128),
        record_digest TEXT NOT NULL CHECK ({digest("record_digest")}),
        PRIMARY KEY (event_id, ordinal)
    ) STRICT""",
    "CREATE INDEX ix_audit_subjects_object ON audit_event_subjects (object_type, object_id)",
    *guard("audit_event_subjects", SIDE_KEY, "authoritative record"),
    """CREATE TABLE audit_event_contexts (
        event_id TEXT NOT NULL REFERENCES audit_events (record_id),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        context_id TEXT NOT NULL CHECK (length(context_id) BETWEEN 1 AND 128),
        PRIMARY KEY (event_id, ordinal)
    ) STRICT""",
    "CREATE INDEX ix_audit_contexts_id ON audit_event_contexts (context_id)",
    *guard("audit_event_contexts", SIDE_KEY, "authoritative record"),
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
