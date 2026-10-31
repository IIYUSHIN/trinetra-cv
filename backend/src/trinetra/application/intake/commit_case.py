"""CommitIntakeService (IMP-0095): turn a ready intake into one persistent PREFLIGHT Case.

Under the exclusive intake lock:
1. admit every planned transfer (IMP-0079; already-admitted assets are only verified);
2. build the Case with the id reserved when the intake was created and links in commit-plan order
   (one dataset, one model, then the optional extras), or recognise a Case that an earlier attempt already
   stored;
3. mark the intake COMMITTED.

Retry and recovery follow from that order. If the process dies after step 1 the assets stay admitted and the
intake stays OPEN; a retry stores the Case. If it dies after step 2, the Case exists while the intake is
still OPEN; a retry finds the Case, checks that it holds exactly the links this intake plans, and only
then marks the intake COMMITTED. A stored Case that disagrees with the intake is never overwritten and
never adopted: the commit stops with a conflict. Committing an already COMMITTED intake changes nothing
and returns the same Case. A Case is born in PREFLIGHT at revision 1 and nothing here claims any finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from trinetra.application.errors import ApplicationConflictError, CaseAlreadyStoredError
from trinetra.application.intake.commit_admission import CommitAdmissionStep
from trinetra.application.intake.enums import IntakeState
from trinetra.application.intake.records import IntakeSession
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.slots import SLOT_CONTRACTS
from trinetra.application.ports.cases import CaseStorePort
from trinetra.application.ports.clock import ClockPort
from trinetra.domain.assets import AssetId
from trinetra.domain.assurance_case import AssuranceCase
from trinetra.domain.case_assets import CaseAssetRole, link_assets, role_for_kind
from trinetra.domain.intake_ids import IntakeId


@dataclass(frozen=True, slots=True)
class CommittedIntake:
    case: AssuranceCase
    session: IntakeSession  # COMMITTED
    case_created: bool  # True only for the call that stored the Case


def link_entries(session: IntakeSession) -> tuple[tuple[CaseAssetRole, AssetId], ...]:
    """(role, asset) pairs for every admitted counted transfer, in commit-plan order."""
    entries: list[tuple[CaseAssetRole, AssetId]] = []
    for slot, contract in SLOT_CONTRACTS.items():
        role = role_for_kind(contract.asset_kind)
        for transfer in session.counted(slot):
            if transfer.admitted_asset_id is None:
                raise ApplicationConflictError("a planned transfer is not admitted")
            entries.append((role, transfer.admitted_asset_id))
    return tuple(entries)


def _shape(case: AssuranceCase) -> tuple[tuple[str, int, str], ...]:
    return tuple((link.role.to_wire(), link.ordinal, str(link.asset_id)) for link in case.asset_links)


class CommitIntakeService:
    def __init__(
        self, step: CommitAdmissionStep, registry: InMemoryIntakeRegistry, cases: CaseStorePort,
        clock: ClockPort,
    ) -> None:
        self._step = step
        self._registry = registry
        self._cases = cases
        self._clock = clock

    def commit(self, intake_id: IntakeId) -> CommittedIntake:
        with self._registry.exclusive(intake_id):
            session = self._registry.require(intake_id)
            if session.intake_state is IntakeState.COMMITTED:
                return CommittedIntake(self._stored(session), session, case_created=False)
            admitted = self._step.admit_all_locked(session)
            now = self._clock.now()
            planned = AssuranceCase.create(
                admitted.case_id, link_assets(link_entries(admitted.session), now), now,
                label=admitted.session.case_label,
            )
            existing = self._cases.get(planned.case_id)
            created = existing is None
            case = planned if existing is None else self._same_links(existing, planned)
            if existing is None:
                self._create(planned, intake_id)
            committed = admitted.session.committed(now)
            self._registry.replace(committed)
            return CommittedIntake(case, committed, case_created=created)

    def _create(self, case: AssuranceCase, intake_id: IntakeId) -> None:
        try:
            self._cases.create(case, intake_id=intake_id)
        except CaseAlreadyStoredError as exc:  # lost a race with a writer outside this process lock
            raise ApplicationConflictError(
                "the case for this intake appeared while it was being stored",
                details={"case_id": str(case.case_id)},
            ) from exc

    def _stored(self, session: IntakeSession) -> AssuranceCase:
        stored = self._cases.get(session.reserved_case_id)
        if stored is None:
            raise ApplicationConflictError(
                "the intake is committed but its case is not stored",
                details={"case_id": str(session.reserved_case_id)},
            )
        return stored

    @staticmethod
    def _same_links(existing: AssuranceCase, planned: AssuranceCase) -> AssuranceCase:
        if existing.case_label != planned.case_label or _shape(existing) != _shape(planned):
            raise ApplicationConflictError(
                "a case with the reserved id exists but does not match this intake",
                details={"case_id": str(planned.case_id)},
            )
        return existing


__all__ = ["CommitIntakeService", "CommittedIntake", "link_entries"]
