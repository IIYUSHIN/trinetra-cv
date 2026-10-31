"""Case identity and state revision (IMP-0018).

`CaseId` names an Assurance Case; `CaseStateRevision` is the explicit revision of that
Case's persisted state. Projections carry both (`case_id`, `case_state_revision`);
there is no generic `source_revision` (Bible line 608).
"""

from __future__ import annotations

from trinetra.domain.counters import Counter
from trinetra.domain.ids import OpaqueId


class CaseId(OpaqueId):
    PREFIX = "case"
    __slots__ = ()


class CaseStateRevision(Counter):
    KIND = "case_state_revision"
    __slots__ = ()


__all__ = ["CaseId", "CaseStateRevision"]
