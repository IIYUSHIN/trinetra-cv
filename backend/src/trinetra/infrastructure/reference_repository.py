"""Reference repository: authority records, append-only state events and purpose lookup (IMP-0091).

Runs only inside a Core UnitOfWork. A Reference is created together with its first (ACTIVE) event, and
only for an existing asset of kind `reference`: the repository says so in its own words, and the database
re-checks it. Reading never trusts the rows: records and histories are re-validated through the domain
types, so a damaged row fails closed as `ReferenceIntegrityError`.

State events are appended with an expected previous sequence, so two writers cannot fork one history: the
second one gets `ReferenceSequenceConflictError`. Standing is derived from the stored history and the
trusted time passed in; EXPIRED is never written. `find_active_by_purpose` returns only references that
are usable now, never a revoked, superseded, expired or not-yet-valid one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trinetra.domain.assets import AssetId, AssetKind
from trinetra.domain.reference_records import (
    ReasonCode,
    ReferenceAssuranceRecord,
    ReferenceHistory,
    ReferenceStanding,
    ReferenceState,
    ReferenceStateEvent,
)
from trinetra.domain.references import ReferenceEventId, ReferenceId, ReferencePurpose
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from collections.abc import Collection

    from trinetra.infrastructure.unit_of_work import UnitOfWork

_RECORD: Final = "reference_id, asset_id, purpose, authority, valid_from, valid_until, created_at"
_EVENT: Final = (
    "event_id, reference_id, sequence, state, occurred_at, superseded_by_reference_id, reason_code, "
    "reason_text"
)
MAX_FIND: Final = 200


class ReferenceExistsError(TrinetraError):
    code = "REFERENCE_ALREADY_EXISTS"
    default_message = "a reference with this id already exists"


class ReferenceNotFoundError(TrinetraError):
    code = "REFERENCE_NOT_FOUND"
    default_message = "no reference has this id"


class ReferenceAssetError(TrinetraError):
    code = "REFERENCE_ASSET_INVALID"
    default_message = "a reference needs an existing asset of kind reference"


class ReferenceSequenceConflictError(TrinetraError):
    code = "REFERENCE_SEQUENCE_CONFLICT"
    default_message = "the reference history changed since it was read"


class ReferenceQueryError(TrinetraError):
    code = "REFERENCE_QUERY_INVALID"
    default_message = "the reference query is invalid"


class ReferenceIntegrityError(TrinetraError):
    code = "REFERENCE_RECORD_INTEGRITY"
    default_message = "a stored reference does not satisfy the reference invariants"


class ReferenceRepository:
    def add(
        self, uow: UnitOfWork, record: ReferenceAssuranceRecord, initial_event: ReferenceStateEvent
    ) -> None:
        if initial_event.reference_id != record.reference_id or initial_event.sequence != 1:
            raise ReferenceAssetError("the initial event must be sequence 1 of this reference")
        if self.get(uow, record.reference_id) is not None:
            raise ReferenceExistsError
        kind = uow.connection.exec_driver_sql(
            "SELECT asset_kind FROM asset_records WHERE record_id = ?", (str(record.asset_id),)
        ).fetchone()
        if kind is None or kind[0] != AssetKind.REFERENCE.value:
            raise ReferenceAssetError(details={"asset_id": str(record.asset_id)})
        uow.connection.exec_driver_sql(
            f"INSERT INTO reference_records ({_RECORD}) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            (str(record.reference_id), str(record.asset_id), record.purpose.text, record.authority,
             record.valid_from.to_wire(),
             None if record.valid_until is None else record.valid_until.to_wire(),
             record.created_at.to_wire()),
        )
        self._insert_event(uow, initial_event)

    def get(self, uow: UnitOfWork, reference_id: ReferenceId) -> ReferenceAssuranceRecord | None:
        row = uow.connection.exec_driver_sql(
            f"SELECT {_RECORD} FROM reference_records WHERE reference_id = ?",  # noqa: S608
            (str(reference_id),),
        ).fetchone()
        return None if row is None else self._record(tuple(row))

    def require(self, uow: UnitOfWork, reference_id: ReferenceId) -> ReferenceAssuranceRecord:
        record = self.get(uow, reference_id)
        if record is None:
            raise ReferenceNotFoundError(details={"reference_id": str(reference_id)})
        return record

    def history(self, uow: UnitOfWork, reference_id: ReferenceId) -> ReferenceHistory:
        rows = uow.connection.exec_driver_sql(
            f"SELECT {_EVENT} FROM reference_state_events WHERE reference_id = ? ORDER BY sequence",  # noqa: S608
            (str(reference_id),),
        ).fetchall()
        if not rows:
            if self.get(uow, reference_id) is None:
                raise ReferenceNotFoundError(details={"reference_id": str(reference_id)})
            raise ReferenceIntegrityError("a reference has no history")
        try:
            events = tuple(self._event(tuple(row)) for row in rows)
            return ReferenceHistory(reference_id=reference_id, events=events)
        except (ValidationError, ValueError, TrinetraError) as exc:
            raise ReferenceIntegrityError from exc

    def current_state(
        self, uow: UnitOfWork, reference_id: ReferenceId, now: UtcTimestamp
    ) -> ReferenceStanding:
        record = self.require(uow, reference_id)
        return self.history(uow, reference_id).standing(record, now)

    def find_active_by_purpose(
        self, uow: UnitOfWork, purpose: ReferencePurpose, now: UtcTimestamp, *, limit: int = MAX_FIND
    ) -> tuple[ReferenceAssuranceRecord, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_FIND:
            raise ReferenceQueryError(f"limit must be within [1, {MAX_FIND}]")
        rows = uow.connection.exec_driver_sql(
            f"SELECT {_RECORD} FROM reference_records WHERE purpose = ? "  # noqa: S608
            "AND valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?) "
            "ORDER BY valid_from, reference_id",
            (purpose.text, now.to_wire(), now.to_wire()),
        ).fetchall()
        usable: list[ReferenceAssuranceRecord] = []
        for row in rows:
            record = self._record(tuple(row))
            if self.history(uow, record.reference_id).is_usable(record, now):
                usable.append(record)
                if len(usable) == limit:
                    break
        return tuple(usable)

    def usable_for_assets(
        self, uow: UnitOfWork, asset_ids: Collection[AssetId], now: UtcTimestamp
    ) -> tuple[ReferenceAssuranceRecord, ...]:
        """References, usable now, whose asset is one of `asset_ids` (at most MAX_FIND distinct ids)."""
        wanted = sorted({str(asset_id) for asset_id in asset_ids})
        if len(wanted) > MAX_FIND:
            raise ReferenceQueryError(f"at most {MAX_FIND} assets may be asked about at once")
        if not wanted:
            return ()
        marks = ", ".join("?" * len(wanted))
        rows = uow.connection.exec_driver_sql(
            f"SELECT {_RECORD} FROM reference_records WHERE asset_id IN ({marks}) "  # noqa: S608
            "ORDER BY valid_from, reference_id",
            tuple(wanted),
        ).fetchall()
        records = (self._record(tuple(row)) for row in rows)
        return tuple(r for r in records if self.history(uow, r.reference_id).is_usable(r, now))

    def append_state_event(
        self, uow: UnitOfWork, event: ReferenceStateEvent, expected_previous_sequence: int
    ) -> None:
        stored = self.history(uow, event.reference_id)
        last = stored.events[-1].sequence
        if last != expected_previous_sequence or event.sequence != expected_previous_sequence + 1:
            raise ReferenceSequenceConflictError(
                details={"expected": expected_previous_sequence, "actual": last, "new": event.sequence}
            )
        try:  # the domain decides whether the event is a legal next step
            ReferenceHistory(reference_id=event.reference_id, events=(*stored.events, event))
        except ValidationError as exc:
            raise ReferenceSequenceConflictError("the event is not a legal next step") from exc
        self._insert_event(uow, event)

    @staticmethod
    def _insert_event(uow: UnitOfWork, event: ReferenceStateEvent) -> None:
        successor = event.superseded_by_reference_id
        uow.connection.exec_driver_sql(
            f"INSERT INTO reference_state_events ({_EVENT}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            (str(event.event_id), str(event.reference_id), event.sequence, event.reference_state.to_wire(),
             event.occurred_at.to_wire(), None if successor is None else str(successor),
             None if event.reason_code is None else event.reason_code.text, event.reason_text),
        )

    @staticmethod
    def _record(row: tuple[Any, ...]) -> ReferenceAssuranceRecord:
        reference_id, asset_id, purpose, authority, valid_from, valid_until, created_at = row
        try:
            return ReferenceAssuranceRecord(
                reference_id=ReferenceId.parse(reference_id), asset_id=AssetId.parse(asset_id),
                purpose=ReferencePurpose(purpose), authority=authority,
                valid_from=UtcTimestamp.parse(valid_from),
                valid_until=None if valid_until is None else UtcTimestamp.parse(valid_until),
                created_at=UtcTimestamp.parse(created_at),
            )
        except (ValidationError, ValueError, TrinetraError) as exc:
            raise ReferenceIntegrityError from exc

    @staticmethod
    def _event(row: tuple[Any, ...]) -> ReferenceStateEvent:
        event_id, reference_id, sequence, state, occurred_at, successor, reason_code, reason_text = row
        return ReferenceStateEvent(
            event_id=ReferenceEventId.parse(event_id), reference_id=ReferenceId.parse(reference_id),
            sequence=sequence, reference_state=ReferenceState.from_wire(state),
            occurred_at=UtcTimestamp.parse(occurred_at),
            superseded_by_reference_id=None if successor is None else ReferenceId.parse(successor),
            reason_code=None if reason_code is None else ReasonCode(reason_code), reason_text=reason_text,
        )


__all__ = [
    "MAX_FIND", "ReferenceAssetError", "ReferenceExistsError", "ReferenceIntegrityError",
    "ReferenceNotFoundError", "ReferenceQueryError", "ReferenceRepository", "ReferenceSequenceConflictError",
]
