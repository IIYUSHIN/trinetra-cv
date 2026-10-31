"""Case repository: create, read and optimistically save AssuranceCases (IMP-0084).

Runs only inside a Core UnitOfWork. `add` stores a new Case (the database insists it is born in
PREFLIGHT at revision 1) together with its links. `get` never trusts what it reads: the row and its links
are re-validated through the domain aggregate, so a damaged row fails closed as `CaseIntegrityError`.

`save` is the only way a stored Case changes and it is an optimistic compare-and-set: the new aggregate
must carry revision `expected + 1`, the UPDATE succeeds only while the stored revision is still
`expected`, and zero affected rows is a `CaseRevisionConflictError` (or `CaseNotFoundError`), never a blind
overwrite. Links already stored may not be dropped or changed; links the aggregate adds are inserted in
the same transaction. PROPOSED (ERR-CL-022): lifecycle transitions are refused here until the lifecycle
machine exists (only creation into PREFLIGHT is implemented in EIB-04).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from trinetra.domain.assurance_case import AssuranceCase, CaseInvariantError
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.case_lifecycle import CaseLifecycleState
from trinetra.domain.timing import UtcTimestamp
from trinetra.errors import TrinetraError
from trinetra.infrastructure.case_asset_link_repository import CaseAssetLinkRepository

if TYPE_CHECKING:
    from trinetra.infrastructure.unit_of_work import UnitOfWork

_COLUMNS: Final = "case_id, case_label, lifecycle_state, state_revision, created_at, updated_at"


class CaseExistsError(TrinetraError):
    code = "CASE_ALREADY_EXISTS"
    default_message = "a case with this id already exists"


class CaseNotFoundError(TrinetraError):
    code = "CASE_NOT_FOUND"
    default_message = "no case has this id"


class CaseRevisionConflictError(TrinetraError):
    code = "CASE_REVISION_CONFLICT"
    default_message = "the case changed since it was read"


class CaseIntegrityError(TrinetraError):
    code = "CASE_RECORD_INTEGRITY"
    default_message = "a stored case does not satisfy the case invariants"


class CaseRepository:
    def __init__(self, links: CaseAssetLinkRepository | None = None) -> None:
        self._links = links or CaseAssetLinkRepository()

    def add(self, uow: UnitOfWork, case: AssuranceCase) -> None:
        if self._row(uow, case.case_id) is not None:
            raise CaseExistsError
        uow.connection.exec_driver_sql(
            f"INSERT INTO cases ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: S608
            (str(case.case_id), case.case_label, case.lifecycle_state.to_wire(),
             case.case_state_revision.value, case.created_at.to_wire(), case.updated_at.to_wire()),
        )
        self._links.add_all(uow, case.case_id, case.asset_links)

    def get(self, uow: UnitOfWork, case_id: CaseId) -> AssuranceCase | None:
        row = self._row(uow, case_id)
        return None if row is None else self._verified(uow, row)

    def require(self, uow: UnitOfWork, case_id: CaseId) -> AssuranceCase:
        case = self.get(uow, case_id)
        if case is None:
            raise CaseNotFoundError(details={"case_id": str(case_id)})
        return case

    def exists(self, uow: UnitOfWork, case_id: CaseId) -> bool:
        return self._row(uow, case_id) is not None

    def save(self, uow: UnitOfWork, updated: AssuranceCase, expected_revision: CaseStateRevision) -> None:
        if updated.case_state_revision != expected_revision.next():
            raise CaseRevisionConflictError(
                "the new case must carry the expected revision plus one",
                details={"expected": expected_revision.value, "new": updated.case_state_revision.value},
            )
        stored = self.require(uow, updated.case_id)
        if stored.case_state_revision != expected_revision:
            raise self._conflict(expected_revision, stored)
        if updated.lifecycle_state is not stored.lifecycle_state:
            raise CaseInvariantError("lifecycle transitions are not implemented yet")
        new_links = self._new_links(stored, updated)
        result = uow.connection.exec_driver_sql(
            "UPDATE cases SET case_label = ?, lifecycle_state = ?, state_revision = ?, updated_at = ? "
            "WHERE case_id = ? AND state_revision = ?",
            (updated.case_label, updated.lifecycle_state.to_wire(), updated.case_state_revision.value,
             updated.updated_at.to_wire(), str(updated.case_id), expected_revision.value),
        )
        if result.rowcount != 1:
            raise self._conflict(expected_revision, self.require(uow, updated.case_id))
        self._links.add_all(uow, updated.case_id, new_links)

    @staticmethod
    def _conflict(expected: CaseStateRevision, stored: AssuranceCase) -> CaseRevisionConflictError:
        return CaseRevisionConflictError(details={
            "expected": expected.value, "actual": stored.case_state_revision.value,
        })

    @staticmethod
    def _new_links(stored: AssuranceCase, updated: AssuranceCase) -> tuple[Any, ...]:
        before = {(lk.role, lk.ordinal): lk for lk in stored.asset_links}
        after = {(lk.role, lk.ordinal): lk for lk in updated.asset_links}
        if any(after.get(key) != link for key, link in before.items()):
            raise CaseInvariantError("stored case links can never be changed or removed")
        if updated.created_at != stored.created_at:
            raise CaseInvariantError("a case keeps its creation time")
        return tuple(link for key, link in after.items() if key not in before)

    @staticmethod
    def _row(uow: UnitOfWork, case_id: CaseId) -> tuple[Any, ...] | None:
        row = uow.connection.exec_driver_sql(
            f"SELECT {_COLUMNS} FROM cases WHERE case_id = ?",  # noqa: S608
            (str(case_id),),
        ).fetchone()
        return None if row is None else tuple(row)

    def _verified(self, uow: UnitOfWork, row: tuple[Any, ...]) -> AssuranceCase:
        case_id, label, lifecycle, revision, created_at, updated_at = row
        try:
            parsed = CaseId.parse(case_id)
            return AssuranceCase(
                case_id=parsed, case_label=label, lifecycle_state=CaseLifecycleState.from_wire(lifecycle),
                case_state_revision=CaseStateRevision(revision), created_at=UtcTimestamp.parse(created_at),
                updated_at=UtcTimestamp.parse(updated_at),
                asset_links=self._links.list_for_case(uow, parsed),
            )
        except (ValidationError, ValueError, TrinetraError) as exc:
            if isinstance(exc, TrinetraError) and exc.code == "CASE_LINK_INTEGRITY":
                raise
            raise CaseIntegrityError from exc


__all__ = [
    "CaseExistsError", "CaseIntegrityError", "CaseNotFoundError", "CaseRepository",
    "CaseRevisionConflictError",
]
