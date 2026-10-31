"""Commit planning (IMP-0078): what a commit needs, in a fixed deterministic order.

`plan_commit` is pure. It requires an OPEN intake whose counted uploads satisfy every slot's range, and
lists the transfers a commit will admit: slot by slot in the contract's slot order, and within a slot in
arrival order. Failed transfers are never part of the plan. The order is part of the contract: asset links
and audit events are written in it, so a retry behaves the same way as the first attempt.

An unmet slot raises `IntakeNotReadyError` carrying, for each, the slot and its present/minimum/maximum
counts, so a client can tell exactly what is missing instead of guessing.
"""

from __future__ import annotations

from trinetra.application.errors import IntakeNotReadyError, IntakeStateConflictError
from trinetra.application.intake.enums import IntakeState
from trinetra.application.intake.records import IntakeSession
from trinetra.application.intake.slots import SLOT_CONTRACTS
from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId
from trinetra.domain.intake_ids import IntakeId, TransferId


class CommitPlan(DomainModel):
    intake_id: IntakeId
    reserved_case_id: CaseId
    transfer_ids: tuple[TransferId, ...]


def plan_commit(session: IntakeSession) -> CommitPlan:
    if session.intake_state is not IntakeState.OPEN:
        raise IntakeStateConflictError(
            f"cannot commit: the intake is {session.intake_state.value}",
            details={"intake_id": str(session.intake_id)},
        )
    readiness = session.readiness()
    if not readiness.ready:
        raise IntakeNotReadyError(
            details={
                "intake_id": str(session.intake_id),
                "unmet": [
                    {"slot": c.slot.value, "present": c.present, "minimum": c.minimum, "maximum": c.maximum}
                    for c in readiness.unmet
                ],
            }
        )
    ordered = [t.transfer_id for slot in SLOT_CONTRACTS for t in session.counted(slot)]
    return CommitPlan(
        intake_id=session.intake_id, reserved_case_id=session.reserved_case_id, transfer_ids=tuple(ordered)
    )


__all__ = ["CommitPlan", "plan_commit"]
