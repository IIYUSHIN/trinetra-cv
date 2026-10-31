"""ApprovedReferencesPort: which reference purposes are actually approved for a Case (EIB-04).

A purpose counts as approved for a Case only when a Core-created reference authority record exists for an
asset the Case links in the reference role, and that record is usable at the given trusted time (ACTIVE,
its window open, not revoked, superseded or expired). Uploading a file into the reference slot creates an
asset and a link, never an approval: an upload alone never adds a purpose to the answer.
"""

from __future__ import annotations

from typing import Protocol

from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.references import ReferencePurpose
from trinetra.domain.timing import UtcTimestamp


class ApprovedReferencesPort(Protocol):
    def approved_purposes(self, case: AssuranceCase, now: UtcTimestamp) -> frozenset[ReferencePurpose]: ...


__all__ = ["ApprovedReferencesPort"]
