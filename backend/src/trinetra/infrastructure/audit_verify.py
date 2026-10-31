"""Audit chain verifier (IMP-0063).

Walks a journal from its first event to its last and reports, never repairs. Per event it re-derives
everything the row claims (`load_event`); between events it checks that sequences are gapless and that
each event's previous-link digest equals its predecessor's link digest (the first event chains to the
journal's genesis); at the end it checks that `journal_state` allocates exactly the next sequence.
Problems are collected (bounded) rather than raised, so a damaged journal yields a precise report
instead of a stack trace. A clean report proves internal consistency and order of the stored chain
only. It cannot detect a wholesale rewrite of the whole database by someone who also recomputes every
digest (the chain is unkeyed; anchoring the head digest elsewhere is a later, signed concern), and it
says nothing about whether an audited action was legitimate.

PROPOSED engineering decisions (ERR-CL-018): problem kinds, the 100-problem cap, page size of 200.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from trinetra.domain.audit import JournalId
from trinetra.domain.audit_link import genesis_link
from trinetra.domain.digests import DigestSha256
from trinetra.errors import TrinetraError
from trinetra.infrastructure.audit_repository import (
    EVENT_COLUMNS,
    AuditIntegrityError,
    load_event,
)

if TYPE_CHECKING:
    from trinetra.infrastructure.unit_of_work import UnitOfWork

PAGE: Final = 200
MAX_PROBLEMS: Final = 100

EVENT_INVALID: Final = "event_invalid"
GAP: Final = "gap"
LINK_BROKEN: Final = "link_broken"
STATE_MISMATCH: Final = "state_mismatch"
STATE_MISSING: Final = "state_missing"


@dataclass(frozen=True, slots=True)
class ChainProblem:
    kind: str
    sequence: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class ChainReport:
    journal_id: JournalId
    events_checked: int
    head_sequence: int  # last sequence seen in storage (0 if none); not trusted if problems exist
    head_link_digest: str | None
    problems: tuple[ChainProblem, ...]
    truncated: bool  # True when more problems existed than MAX_PROBLEMS

    @property
    def intact(self) -> bool:
        return not self.problems


def verify_chain(uow: UnitOfWork, journal_id: JournalId) -> ChainReport:
    connection = uow.connection
    problems: list[ChainProblem] = []
    truncated = False

    def note(kind: str, sequence: int | None, detail: str) -> None:
        nonlocal truncated
        if len(problems) >= MAX_PROBLEMS:
            truncated = True
            return
        problems.append(ChainProblem(kind, sequence, detail))

    expected_sequence = 1
    previous_link: str = genesis_link(journal_id).to_wire()
    checked = 0
    last_sequence = 0
    head_link: str | None = None
    after = 0
    while True:
        rows = connection.exec_driver_sql(
            f"SELECT {EVENT_COLUMNS} FROM audit_events WHERE journal_id = ? AND journal_sequence > ? "  # noqa: S608
            "ORDER BY journal_sequence LIMIT ?",
            (journal_id.text, after, PAGE),
        ).fetchall()
        if not rows:
            break
        for raw in rows:
            row = tuple(raw)
            sequence = int(row[2])
            if sequence != expected_sequence:
                note(GAP, sequence, f"expected sequence {expected_sequence}")
            try:
                load_event(uow, row)
            except AuditIntegrityError as exc:
                note(EVENT_INVALID, sequence, exc.args[0] if exc.args else str(exc))
            if row[8] != previous_link:
                note(LINK_BROKEN, sequence, "previous link does not equal the predecessor's link")
            checked += 1
            previous_link = row[9]
            head_link = row[9]
            last_sequence = sequence
            expected_sequence = sequence + 1
            after = sequence
    state = connection.exec_driver_sql(
        "SELECT next_sequence FROM journal_state WHERE journal_id = ?", (journal_id.text,)
    ).fetchone()
    if state is None:
        if checked:
            note(STATE_MISSING, None, "events exist but the journal has no state row")
    elif int(state[0]) != last_sequence + 1:
        note(STATE_MISMATCH, None, f"state allocates {int(state[0])}, events end at {last_sequence}")
    return ChainReport(journal_id, checked, last_sequence, head_link, tuple(problems), truncated)


def journal_ids(uow: UnitOfWork) -> tuple[JournalId, ...]:
    """Every journal that has state or events, in name order."""
    rows = uow.connection.exec_driver_sql(
        "SELECT journal_id FROM journal_state UNION SELECT journal_id FROM audit_events ORDER BY 1"
    ).fetchall()
    try:
        return tuple(JournalId(r[0]) for r in rows)
    except TrinetraError as exc:
        msg = "a stored journal id is not a valid token"
        raise AuditIntegrityError(msg) from exc


def verify_all(uow: UnitOfWork) -> tuple[ChainReport, ...]:
    return tuple(verify_chain(uow, journal) for journal in journal_ids(uow))


def head_digest(report: ChainReport) -> DigestSha256 | None:
    """The head link as a typed digest, only for an intact report."""
    if not report.intact or report.head_link_digest is None:
        return None
    return DigestSha256.parse(report.head_link_digest)


__all__ = [
    "EVENT_INVALID",
    "GAP",
    "LINK_BROKEN",
    "MAX_PROBLEMS",
    "STATE_MISMATCH",
    "STATE_MISSING",
    "ChainProblem",
    "ChainReport",
    "head_digest",
    "journal_ids",
    "verify_all",
    "verify_chain",
]
