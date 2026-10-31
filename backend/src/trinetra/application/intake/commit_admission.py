"""Commit admission step and its idempotency rules (IMP-0079).

Under the exclusive intake lock, admit every planned transfer in plan order (IMP-0078) through the
transfer admission service (IMP-0077) and return the admitted asset ids in that same order together with
the case id reserved when the intake was created. Creating the Case and marking the intake COMMITTED are
later packets; this step stops at "every asset is admitted".

Retry semantics, which are the point of this packet:
* admission facts are immutable and survive a later failure: assets already admitted stay admitted, the
  intake stays OPEN, and a retry does not admit them again (their records are only verified);
* the reserved case id never changes across attempts, so a retry builds the same Case;
* a transfer the admission rejects becomes FAILED and aborts the commit; its slot is open again, so the
  next attempt reports the intake as not ready until the client uploads a replacement;
* an infrastructure failure aborts the commit and changes nothing else, so a retry simply continues;
* nothing here fabricates a Case. If the process dies after admitting assets, those records are valid but
  unassigned (a documented walking-skeleton limit that durable coordination closes later).
"""

from __future__ import annotations

from dataclasses import dataclass

from trinetra.application.errors import ApplicationConflictError
from trinetra.application.intake.admission import TransferAdmissionService
from trinetra.application.intake.commit_plan import plan_commit
from trinetra.application.intake.enums import TransferState
from trinetra.application.intake.records import IntakeSession
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.domain.assets import AssetId
from trinetra.domain.case import CaseId
from trinetra.domain.intake_ids import IntakeId


@dataclass(frozen=True, slots=True)
class AdmittedIntake:
    session: IntakeSession  # the latest stored version; still OPEN
    case_id: CaseId  # the id reserved at creation, identical on every attempt
    asset_ids: tuple[AssetId, ...]  # in commit-plan order


class CommitAdmissionStep:
    def __init__(self, registry: InMemoryIntakeRegistry, admission: TransferAdmissionService) -> None:
        self._registry = registry
        self._admission = admission

    def admit_all(self, intake_id: IntakeId) -> AdmittedIntake:
        with self._registry.exclusive(intake_id):
            return self.admit_all_locked(self._registry.require(intake_id))

    def admit_all_locked(self, session: IntakeSession) -> AdmittedIntake:
        """Admit everything `session` plans to commit; the caller holds the intake lock."""
        plan = plan_commit(session)
        for transfer_id in plan.transfer_ids:
            current = self._registry.require(plan.intake_id)
            transfer = current.get_transfer(transfer_id)
            if transfer is None:  # unreachable while transfers are never removed
                raise ApplicationConflictError("a planned transfer disappeared")
            self._admission.admit_locked(current, transfer)
        final = self._registry.require(plan.intake_id)
        asset_ids: list[AssetId] = []
        for transfer_id in plan.transfer_ids:
            admitted = final.get_transfer(transfer_id)
            if (
                admitted is None
                or admitted.transfer_state is not TransferState.ADMITTED
                or admitted.admitted_asset_id is None
            ):
                raise ApplicationConflictError("a planned transfer is not admitted")
            asset_ids.append(admitted.admitted_asset_id)
        return AdmittedIntake(final, plan.reserved_case_id, tuple(asset_ids))


__all__ = ["AdmittedIntake", "CommitAdmissionStep"]
