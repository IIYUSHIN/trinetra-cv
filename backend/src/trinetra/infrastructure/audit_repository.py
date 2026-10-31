"""Audit repository: the append-only writer and verified reader for AuditEvents (IMP-0062).

Runs only inside a Core UnitOfWork. `append` allocates the next sequence from `journal_state`, chains
the new event to its predecessor's link digest (or to the journal's genesis), and inserts the event
plus its subject and context rows; the schema triggers independently refuse a gap, a skipped
allocation or a broken link, and refuse any later UPDATE, DELETE or overwrite. There is no update or
delete method. Before chaining, the current head row is re-checked against its own digests so a
tampered tail is not extended silently.

Reads never trust storage: every event is re-parsed from its canonical body and compared with its
mirrored columns, digests, link and side rows (`check_stored_event`); a row edited behind the
repository's back raises `AuditIntegrityError` instead of being believed. Full-chain verification is
IMP-0063; this module checks one event (and the head) at a time.

PROPOSED engineering decisions (ERR-CL-018): `local_main` as the default journal, page ceiling 500,
O(1) head re-check on append, keyset paging by sequence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import (
    AuditDetail,
    AuditEvent,
    AuditEventId,
    AuditEventType,
    AuditOutcome,
    JournalId,
    ObjectRef,
)
from trinetra.domain.audit_link import body_digest, link_digest, project
from trinetra.domain.digests import DigestSha256
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError
from trinetra.timeutil import Clock, SystemClock, format_utc, truncate_to_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

    from trinetra.infrastructure.unit_of_work import UnitOfWork

DEFAULT_JOURNAL: Final = JournalId("local_main")
DEFAULT_PAGE: Final = 100
MAX_PAGE: Final = 500

EVENT_COLUMNS: Final = (
    "record_id, journal_id, journal_sequence, event_type, outcome, created_at, body_json, body_digest, "
    "prev_link_digest, link_digest"
)


class AuditExistsError(TrinetraError):
    code = "AUDIT_EVENT_EXISTS"
    default_message = "an AuditEvent with this id already exists"


class AuditIntegrityError(TrinetraError):
    code = "AUDIT_RECORD_INTEGRITY"
    default_message = "a stored audit record disagrees with itself or its chain"


class AuditQueryError(TrinetraError):
    code = "AUDIT_QUERY_INVALID"
    default_message = "the audit query is invalid"


@dataclass(frozen=True, slots=True)
class AuditDraft:
    """What a caller says happened. Identity, time, sequence and chain position are assigned here."""

    event_type: AuditEventType
    outcome: AuditOutcome
    created_by: ActorId
    subject_refs: tuple[ObjectRef, ...] = ()
    context_ids: tuple[str, ...] = ()
    reason_code: str | None = None
    details: tuple[AuditDetail, ...] = ()
    journal_id: JournalId = DEFAULT_JOURNAL


@dataclass(frozen=True, slots=True)
class AuditHead:
    journal_id: JournalId
    sequence: int
    link_digest: DigestSha256


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: tuple[AuditEvent, ...]
    next_after: int | None  # pass as `after_sequence` to continue; None when exhausted


def check_stored_event(
    row: tuple[Any, ...],
    subjects: list[tuple[Any, ...]],
    contexts: list[tuple[Any, ...]],
) -> AuditEvent:
    """Re-derive everything an audit row claims and compare. Raises AuditIntegrityError on any gap."""
    (record_id, journal, sequence, event_type, outcome, created_at, body, body_hash, prev, link) = row
    try:
        event = AuditEvent.model_validate_json(body)
    except ValidationError as exc:
        msg = "the stored body is not a valid AuditEvent"
        raise AuditIntegrityError(msg) from exc
    mirrored = (
        str(event.record_id), event.journal_id.text, event.journal_sequence, event.event_type.text,
        event.outcome.value, event.created_at.to_wire(),
    )  # fmt: skip
    if (record_id, journal, sequence, event_type, outcome, created_at) != mirrored:
        msg = "a stored column disagrees with the event body"
        raise AuditIntegrityError(msg)
    if event.body_json() != body:
        msg = "the stored body is not in canonical form"
        raise AuditIntegrityError(msg)
    if body_digest(event).to_wire() != body_hash:
        msg = "the stored body digest does not match the body"
        raise AuditIntegrityError(msg)
    if link_digest(_digest(prev), body_digest(event)).to_wire() != link:
        msg = "the stored link digest does not follow from its inputs"
        raise AuditIntegrityError(msg)
    wanted_subjects = [
        (i, ref.object_type.text, ref.object_id, ref.record_digest.to_wire())
        for i, ref in enumerate(event.subject_refs)
    ]
    if [(r[0], r[1], r[2], r[3]) for r in subjects] != wanted_subjects:
        msg = "the subject rows disagree with the event body"
        raise AuditIntegrityError(msg)
    if [(r[0], r[1]) for r in contexts] != list(enumerate(event.context_ids)):
        msg = "the context rows disagree with the event body"
        raise AuditIntegrityError(msg)
    return event


def _digest(text: object) -> DigestSha256:
    try:
        return DigestSha256.parse(text)
    except (ValueError, TrinetraError) as exc:
        msg = "a stored digest is malformed"
        raise AuditIntegrityError(msg) from exc


def load_event(uow: UnitOfWork, row: tuple[Any, ...]) -> AuditEvent:
    """Fetch an event row's side rows and run `check_stored_event` over all of it."""
    subjects = uow.connection.exec_driver_sql(
        "SELECT ordinal, object_type, object_id, record_digest FROM audit_event_subjects "
        "WHERE event_id = ? ORDER BY ordinal",
        (row[0],),
    ).fetchall()
    contexts = uow.connection.exec_driver_sql(
        "SELECT ordinal, context_id FROM audit_event_contexts WHERE event_id = ? ORDER BY ordinal",
        (row[0],),
    ).fetchall()
    return check_stored_event(row, [tuple(r) for r in subjects], [tuple(r) for r in contexts])


class AuditRepository:
    def __init__(
        self, clock: Clock | None = None, entropy: Callable[[int], bytes] = os.urandom
    ) -> None:
        self._clock: Clock = clock or SystemClock()
        self._entropy = entropy

    # ----------------------------------------------------------------------------------- write
    def append(self, uow: UnitOfWork, draft: AuditDraft) -> AuditEvent:
        connection = uow.connection
        journal = draft.journal_id.text
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO journal_state (journal_id, next_sequence) VALUES (?, 1)", (journal,)
        )
        head = self.head(uow, draft.journal_id)
        connection.exec_driver_sql(
            "UPDATE journal_state SET next_sequence = next_sequence + 1 WHERE journal_id = ?", (journal,)
        )
        allocated = connection.exec_driver_sql(
            "SELECT next_sequence - 1 FROM journal_state WHERE journal_id = ?", (journal,)
        ).scalar_one()
        expected = 1 if head is None else head.sequence + 1
        if allocated != expected:
            msg = "the journal state is out of step with its audit events"
            raise AuditIntegrityError(msg, details={"allocated": int(allocated), "expected": expected})
        event = AuditEvent(
            record_id=AuditEventId.from_entropy(self._entropy),
            created_at=UtcTimestamp.parse(format_utc(truncate_to_seconds(self._clock.now()))),
            created_by=draft.created_by,
            journal_id=draft.journal_id,
            journal_sequence=expected,
            event_type=draft.event_type,
            outcome=draft.outcome,
            subject_refs=draft.subject_refs,
            context_ids=draft.context_ids,
            reason_code=draft.reason_code,
            details=draft.details,
        )
        exists = connection.exec_driver_sql(
            "SELECT 1 FROM audit_events WHERE record_id = ?", (str(event.record_id),)
        ).fetchone()
        if exists is not None:
            raise AuditExistsError
        projection = project(event, None if head is None else head.link_digest)
        connection.exec_driver_sql(
            f"INSERT INTO audit_events ({EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            (
                str(event.record_id), journal, event.journal_sequence, event.event_type.text,
                event.outcome.value, event.created_at.to_wire(), projection.body_json,
                projection.body_digest.to_wire(), projection.prev_link_digest.to_wire(),
                projection.link_digest.to_wire(),
            ),
        )  # fmt: skip
        for ordinal, ref in enumerate(event.subject_refs):
            connection.exec_driver_sql(
                "INSERT INTO audit_event_subjects (event_id, ordinal, object_type, object_id, record_digest) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(event.record_id), ordinal, ref.object_type.text, ref.object_id,
                    ref.record_digest.to_wire(),
                ),
            )
        for ordinal, context in enumerate(event.context_ids):
            connection.exec_driver_sql(
                "INSERT INTO audit_event_contexts (event_id, ordinal, context_id) VALUES (?, ?, ?)",
                (str(event.record_id), ordinal, context),
            )
        return event

    # ------------------------------------------------------------------------------------ read
    def head(self, uow: UnitOfWork, journal_id: JournalId = DEFAULT_JOURNAL) -> AuditHead | None:
        """The newest event's position, after re-checking that row against its own digests."""
        row = uow.connection.exec_driver_sql(
            f"SELECT {EVENT_COLUMNS} FROM audit_events WHERE journal_id = ? "  # noqa: S608
            "ORDER BY journal_sequence DESC LIMIT 1",
            (journal_id.text,),
        ).fetchone()
        if row is None:
            return None
        event = self._load(uow, tuple(row))
        return AuditHead(journal_id, event.journal_sequence, _digest(row[9]))

    def get(self, uow: UnitOfWork, event_id: AuditEventId) -> AuditEvent | None:
        row = uow.connection.exec_driver_sql(
            f"SELECT {EVENT_COLUMNS} FROM audit_events WHERE record_id = ?",  # noqa: S608
            (str(event_id),),
        ).fetchone()
        return None if row is None else self._load(uow, tuple(row))

    def count(self, uow: UnitOfWork, journal_id: JournalId = DEFAULT_JOURNAL) -> int:
        row = uow.connection.exec_driver_sql(
            "SELECT count(*) FROM audit_events WHERE journal_id = ?", (journal_id.text,)
        ).fetchone()
        return int(row[0]) if row else 0

    def list_page(
        self,
        uow: UnitOfWork,
        journal_id: JournalId = DEFAULT_JOURNAL,
        *,
        after_sequence: int = 0,
        limit: int = DEFAULT_PAGE,
    ) -> AuditPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE:
            msg = f"limit must be within [1, {MAX_PAGE}]"
            raise AuditQueryError(msg)
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            msg = "after_sequence must be a non-negative integer"
            raise AuditQueryError(msg)
        rows = uow.connection.exec_driver_sql(
            f"SELECT {EVENT_COLUMNS} FROM audit_events WHERE journal_id = ? AND journal_sequence > ? "  # noqa: S608
            "ORDER BY journal_sequence LIMIT ?",
            (journal_id.text, after_sequence, limit + 1),
        ).fetchall()
        events = tuple(self._load(uow, tuple(row)) for row in rows[:limit])
        more = len(rows) > limit and bool(events)
        return AuditPage(events, events[-1].journal_sequence if more else None)

    @staticmethod
    def _load(uow: UnitOfWork, row: tuple[Any, ...]) -> AuditEvent:
        return load_event(uow, row)


__all__ = [
    "DEFAULT_JOURNAL",
    "MAX_PAGE",
    "AuditDraft",
    "AuditExistsError",
    "AuditHead",
    "AuditIntegrityError",
    "AuditPage",
    "AuditQueryError",
    "AuditRepository",
    "check_stored_event",
    "load_event",
]
