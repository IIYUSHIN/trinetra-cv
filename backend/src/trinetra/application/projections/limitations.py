"""Standing limitations a Case view must show (IMP-0092).

While assurance reasoning is not AVAILABLE for a Case, the view carries one decision-relevant limitation:
no assurance conclusion exists. It is derived from the snapshot, never hard-wired, so the day reasoning is
built the limitation disappears by itself. Its identity is derived from the Case and the limitation code,
so rebuilding the same view yields byte-identical output (PROPOSED, ERR-CL-022).
"""

from __future__ import annotations

import hashlib
from typing import Final

from trinetra.application.preflight.catalog import ASSURANCE_REASONING
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.capability import CapabilityState
from trinetra.domain.capability_snapshot import CapabilitySnapshot
from trinetra.domain.entity import EntityRef, EntityType
from trinetra.domain.limitations import Limitation, LimitationCode, LimitationId

NO_CONCLUSION_CODE: Final = LimitationCode("assessment_not_implemented")
NO_CONCLUSION_TEXT: Final = (
    "Assessment capabilities are not yet implemented. No assurance conclusion has been produced."
)
_CASE_REF_LABEL: Final = "Assurance case"


def _limitation_id(case: AssuranceCase, code: LimitationCode) -> LimitationId:
    seed = hashlib.sha256(f"{case.case_id}|{code.text}".encode()).digest()
    return LimitationId.from_entropy(lambda size: seed[:size])


def critical_limitations(case: AssuranceCase, snapshot: CapabilitySnapshot) -> tuple[Limitation, ...]:
    reasoning = snapshot.get(ASSURANCE_REASONING)
    if reasoning is not None and reasoning.capability_state is CapabilityState.AVAILABLE:
        return ()
    scope = EntityRef(entity_type=EntityType("case"), entity_id=str(case.case_id),
                      display_label=case.case_label or _CASE_REF_LABEL)
    return (Limitation(
        limitation_id=_limitation_id(case, NO_CONCLUSION_CODE), code=NO_CONCLUSION_CODE,
        summary=NO_CONCLUSION_TEXT, scope_refs=(scope,), decision_relevant=True, detail_available=False,
    ),)


__all__ = ["NO_CONCLUSION_CODE", "NO_CONCLUSION_TEXT", "critical_limitations"]
