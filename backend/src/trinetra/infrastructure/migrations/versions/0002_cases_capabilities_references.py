"""0002 cases / case asset links / reference records and state events (IMP-0083).

Creates only what EIB-04 persists: the Case aggregate (mutable only by an optimistic +1 revision step),
its append-only asset links, and the two append-only reference tables. There is no capability-snapshot
table: EIB-04 computes the snapshot for Preflight and persists it only when a job needs it.

Database-level laws (so a statement that bypasses the repositories fails too):
* a Case is born `preflight` at revision 1 with equal timestamps; afterwards only `state_revision` (+1),
  `updated_at` (never backwards) and the lifecycle may change - identity and creation time never; no
  Case is ever deleted;
* links, reference records and reference state events are append-only (UPDATE, DELETE and REPLACE all
  rejected); a link's role must equal its asset's kind, an asset holds one role per Case, and singular
  roles sit at ordinal 0;
* a reference record may point only at an asset of kind `reference`, and merely uploading such an asset
  creates no record: reference authority is a separate row (LAW-GR-WI-021);
* a reference's events form one gapless sequence that starts `ACTIVE` and ends at the first terminal
  state; `EXPIRED` is derived and never stored.

PROPOSED (ERR-CL-022): lifecycle is not frozen at the database (later modules add transitions by
migration); reference states use uppercase wire forms like the other state families.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TIMESTAMP = (
    "length({c}) = 20 AND {c} GLOB "
    "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'"
)
NAME_RULE = "length({c}) BETWEEN 1 AND 64 AND {c} NOT GLOB '*[^a-z0-9_]*' AND substr({c}, 1, 1) GLOB '[a-z]'"
ID32 = (
    "length({c}) = {n} AND substr({c}, 1, {p}) = '{prefix}_' "
    "AND substr({c}, {p} + 1) NOT GLOB '*[^0-9a-f]*'"
)
LIFECYCLE = (
    "'preflight', 'assessing', 'results_available', 'investigating', 'decision_ready', 'decided', 'attested'"
)
ROLES = "'dataset', 'model', 'contributor_metadata', 'reference', 'inference_record', 'assessment_profile'"
SINGULAR_ROLES = "'dataset', 'model', 'contributor_metadata', 'assessment_profile'"
MAX_COUNTER = 2**53 - 1


def stamp(column: str) -> str:
    return TIMESTAMP.format(c=column)


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


STATEMENTS = [
    # ------------------------------------------------------------------ cases (mutable by +1 only)
    f"""CREATE TABLE cases (
        case_id TEXT NOT NULL PRIMARY KEY CHECK ({opaque("case_id", "case")}),
        case_label TEXT CHECK (case_label IS NULL OR length(case_label) BETWEEN 1 AND 256),
        lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ({LIFECYCLE})),
        state_revision INTEGER NOT NULL CHECK (state_revision BETWEEN 1 AND {MAX_COUNTER}),
        created_at TEXT NOT NULL CHECK ({stamp("created_at")}),
        updated_at TEXT NOT NULL CHECK ({stamp("updated_at")}),
        CHECK (updated_at >= created_at)
    ) STRICT""",
    """CREATE TRIGGER cases_born_in_preflight BEFORE INSERT ON cases
        WHEN NEW.lifecycle_state <> 'preflight' OR NEW.state_revision <> 1
             OR NEW.updated_at <> NEW.created_at
        BEGIN SELECT RAISE(ABORT, 'a case is created in preflight at revision 1'); END""",
    """CREATE TRIGGER cases_no_replace BEFORE INSERT ON cases
        WHEN EXISTS (SELECT 1 FROM cases WHERE case_id = NEW.case_id)
        BEGIN SELECT RAISE(ABORT, 'a case already exists under this id and cannot be replaced'); END""",
    """CREATE TRIGGER cases_identity_fixed BEFORE UPDATE ON cases
        WHEN NEW.case_id <> OLD.case_id OR NEW.created_at <> OLD.created_at
        BEGIN SELECT RAISE(ABORT, 'a case keeps its identity and creation time'); END""",
    """CREATE TRIGGER cases_revision_steps_by_one BEFORE UPDATE ON cases
        WHEN NEW.state_revision <> OLD.state_revision + 1 OR NEW.updated_at < OLD.updated_at
        BEGIN SELECT RAISE(ABORT, 'a case revision advances by exactly one and time never goes back'); END""",
    """CREATE TRIGGER cases_no_delete BEFORE DELETE ON cases
        BEGIN SELECT RAISE(ABORT, 'cases are never deleted'); END""",
    # ------------------------------------------------------------------ case asset links (append-only)
    f"""CREATE TABLE case_asset_links (
        case_id TEXT NOT NULL REFERENCES cases (case_id),
        asset_id TEXT NOT NULL REFERENCES asset_records (record_id),
        role TEXT NOT NULL CHECK (role IN ({ROLES})),
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND ordinal < 10000),
        linked_at TEXT NOT NULL CHECK ({stamp("linked_at")}),
        PRIMARY KEY (case_id, role, ordinal),
        UNIQUE (case_id, asset_id),
        CHECK (role NOT IN ({SINGULAR_ROLES}) OR ordinal = 0)
    ) STRICT""",
    "CREATE INDEX ix_case_asset_links_asset ON case_asset_links (asset_id)",
    """CREATE TRIGGER case_asset_links_role_matches_kind BEFORE INSERT ON case_asset_links
        WHEN NEW.role <> COALESCE((SELECT asset_kind FROM asset_records WHERE record_id = NEW.asset_id), '')
        BEGIN SELECT RAISE(ABORT, 'the link role must equal the asset kind'); END""",
    *guard("case_asset_links", "case_id = NEW.case_id AND role = NEW.role AND ordinal = NEW.ordinal",
           "case asset history"),
    # ------------------------------------------------------------------ reference records (append-only)
    f"""CREATE TABLE reference_records (
        reference_id TEXT NOT NULL PRIMARY KEY CHECK ({opaque("reference_id", "ref")}),
        asset_id TEXT NOT NULL REFERENCES asset_records (record_id),
        purpose TEXT NOT NULL CHECK ({token("purpose")}),
        authority TEXT NOT NULL CHECK (length(authority) BETWEEN 1 AND 128),
        valid_from TEXT NOT NULL CHECK ({stamp("valid_from")}),
        valid_until TEXT CHECK (valid_until IS NULL OR ({stamp("valid_until")} AND valid_until > valid_from)),
        created_at TEXT NOT NULL CHECK ({stamp("created_at")})
    ) STRICT""",
    "CREATE INDEX ix_reference_records_asset ON reference_records (asset_id)",
    "CREATE INDEX ix_reference_records_purpose ON reference_records (purpose, valid_from)",
    """CREATE TRIGGER reference_records_asset_is_a_reference BEFORE INSERT ON reference_records
        WHEN COALESCE((SELECT asset_kind FROM asset_records
                       WHERE record_id = NEW.asset_id), '') <> 'reference'
        BEGIN SELECT RAISE(ABORT, 'a reference record needs an asset of kind reference'); END""",
    *guard("reference_records", "reference_id = NEW.reference_id", "reference authority record"),
    # ------------------------------------------------------------------ reference state events
    f"""CREATE TABLE reference_state_events (
        event_id TEXT NOT NULL PRIMARY KEY CHECK ({opaque("event_id", "refevt")}),
        reference_id TEXT NOT NULL REFERENCES reference_records (reference_id),
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'REVOKED', 'SUPERSEDED')),
        occurred_at TEXT NOT NULL CHECK ({stamp("occurred_at")}),
        superseded_by_reference_id TEXT REFERENCES reference_records (reference_id),
        reason_code TEXT CHECK (reason_code IS NULL OR ({token("reason_code")})),
        reason_text TEXT CHECK (reason_text IS NULL OR length(reason_text) BETWEEN 1 AND 2048),
        UNIQUE (reference_id, sequence),
        CHECK ((state = 'SUPERSEDED') = (superseded_by_reference_id IS NOT NULL)),
        CHECK (superseded_by_reference_id IS NULL OR superseded_by_reference_id <> reference_id),
        CHECK ((sequence = 1) = (state = 'ACTIVE'))
    ) STRICT""",
    """CREATE TRIGGER reference_state_events_continue BEFORE INSERT ON reference_state_events
        WHEN NEW.sequence > 1 AND COALESCE((SELECT state FROM reference_state_events
                                            WHERE reference_id = NEW.reference_id
                                              AND sequence = NEW.sequence - 1), '') <> 'ACTIVE'
        BEGIN SELECT RAISE(ABORT, 'a reference event must follow an ACTIVE event in sequence'); END""",
    """CREATE TRIGGER reference_state_events_time_forward BEFORE INSERT ON reference_state_events
        WHEN NEW.sequence > 1 AND NEW.occurred_at < COALESCE((SELECT occurred_at FROM reference_state_events
                                            WHERE reference_id = NEW.reference_id
                                              AND sequence = NEW.sequence - 1), '')
        BEGIN SELECT RAISE(ABORT, 'a reference event cannot predate the one before it'); END""",
    *guard("reference_state_events", "event_id = NEW.event_id", "reference state history"),
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
