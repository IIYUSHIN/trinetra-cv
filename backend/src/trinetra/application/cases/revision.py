"""CaseUpdateService (IMP-0096): the one way a stored Case changes, guarded by its revision.

A caller names the revision it saw. If the stored Case has moved on, nothing is written and the caller gets
`StaleCaseRevisionError` carrying both revisions, so a client can reload instead of overwriting someone
else's change. The change itself is a pure function from the stored Case and the time to its successor;
the successor must be exactly the next revision of the same Case. The same compare-and-set is enforced
again by the store, so two writers racing past this check still cannot both win: the loser also gets
`StaleCaseRevisionError`. A revision is never reused, skipped or lowered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trinetra.application.errors import CaseChangeInvalidError, StaleCaseRevisionError, UnknownCaseError

if TYPE_CHECKING:
    from collections.abc import Callable

    from trinetra.application.ports.cases import CaseStorePort
    from trinetra.application.ports.clock import ClockPort
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case import CaseId, CaseStateRevision
    from trinetra.domain.timing import UtcTimestamp

type CaseChange = Callable[[AssuranceCase, UtcTimestamp], AssuranceCase]


def stale(case_id: CaseId, expected: CaseStateRevision, actual: CaseStateRevision) -> StaleCaseRevisionError:
    return StaleCaseRevisionError(details={
        "case_id": str(case_id), "expected_revision": expected.value, "actual_revision": actual.value,
    })


class CaseUpdateService:
    def __init__(self, cases: CaseStorePort, clock: ClockPort) -> None:
        self._cases = cases
        self._clock = clock

    def update(
        self, case_id: CaseId, expected_revision: CaseStateRevision, change: CaseChange
    ) -> AssuranceCase:
        stored = self._cases.get(case_id)
        if stored is None:
            raise UnknownCaseError(details={"case_id": str(case_id)})
        if stored.case_state_revision != expected_revision:
            raise stale(case_id, expected_revision, stored.case_state_revision)
        updated = change(stored, self._clock.now())
        if updated.case_id != stored.case_id or updated.case_state_revision != expected_revision.next():
            raise CaseChangeInvalidError(
                details={"case_id": str(case_id), "expected_revision": expected_revision.value},
            )
        self._cases.save(updated, expected_revision=expected_revision)
        return updated


__all__ = ["CaseChange", "CaseUpdateService", "stale"]
