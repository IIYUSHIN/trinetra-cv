"""CaseStorePort: the application's way to create and read Cases (EIB-04).

`create` stores a new Case with all of its asset links as ONE atomic fact together with its audit record;
either the whole Case exists afterwards or nothing of it does. It refuses to overwrite: a Case id that
already exists raises `CaseAlreadyStoredError`, and the caller decides what an existing Case means.
`get` returns the stored Case or None.
"""

from __future__ import annotations

from typing import Protocol

from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.case import CaseId
from trinetra.domain.intake_ids import IntakeId


class CaseStorePort(Protocol):
    def create(self, case: AssuranceCase, *, intake_id: IntakeId) -> None:
        """Store `case` (born from `intake_id`) atomically with its audit fact."""

    def get(self, case_id: CaseId) -> AssuranceCase | None: ...


__all__ = ["CaseStorePort"]
