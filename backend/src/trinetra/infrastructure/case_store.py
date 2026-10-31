"""AuditedCaseStore: the application's CaseStorePort over the Core database (IMP-0095).

`create` writes the Case, its links and a `case_created` audit event in ONE unit of work, so there is never
a Case without its audit fact or an audit fact for a Case that does not exist. A Case id that is already
stored raises `CaseAlreadyStoredError` and leaves the existing Case untouched; a failure is audited as
`case_creation_failed` after the rollback. The audit event names the Case (with its record digest) as its
subject and the originating intake, the dataset and the model as context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from trinetra.application.errors import CaseAlreadyStoredError
from trinetra.domain.asset_record import ActorId
from trinetra.domain.audit import AuditEventType, AuditOutcome, ObjectRef, ObjectType
from trinetra.domain.case_assets import CaseAssetRole
from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.audit_coupling import run_audited
from trinetra.infrastructure.audit_repository import AuditDraft
from trinetra.infrastructure.case_repository import CaseExistsError

if TYPE_CHECKING:
    from trinetra.domain.assurance_case import AssuranceCase
    from trinetra.domain.case import CaseId
    from trinetra.domain.intake_ids import IntakeId
    from trinetra.infrastructure.audit_repository import AuditRepository
    from trinetra.infrastructure.case_repository import CaseRepository
    from trinetra.infrastructure.unit_of_work import CoreStore, UnitOfWork

CASE: Final = ObjectType("case")
CORE_ACTOR: Final = ActorId("core")
EVENT_CREATED: Final = AuditEventType("case_created")
EVENT_FAILED: Final = AuditEventType("case_creation_failed")


class AuditedCaseStore:
    def __init__(
        self, store: CoreStore, cases: CaseRepository, audit: AuditRepository, *, actor: ActorId = CORE_ACTOR
    ) -> None:
        self._store = store
        self._cases = cases
        self._audit = audit
        self._actor = actor

    def create(self, case: AssuranceCase, *, intake_id: IntakeId) -> None:
        def work(uow: UnitOfWork) -> tuple[None, AuditDraft]:
            self._cases.add(uow, case)
            subject = ObjectRef(object_type=CASE, object_id=str(case.case_id),
                                record_digest=DigestSha256.parse(case.canonical_digest()))
            primary = [str(next(lk for lk in case.asset_links if lk.role is role).asset_id)
                       for role in (CaseAssetRole.DATASET, CaseAssetRole.MODEL)]
            draft = AuditDraft(event_type=EVENT_CREATED, outcome=AuditOutcome.SUCCESS, created_by=self._actor,
                               subject_refs=(subject,), context_ids=(str(intake_id), *primary))
            return None, draft

        failure = AuditDraft(event_type=EVENT_FAILED, outcome=AuditOutcome.FAILURE, created_by=self._actor,
                             context_ids=(str(case.case_id), str(intake_id)))
        try:
            run_audited(self._store, self._audit, work, on_failure=failure)
        except CaseExistsError as exc:
            raise CaseAlreadyStoredError(details={"case_id": str(case.case_id)}) from exc

    def get(self, case_id: CaseId) -> AssuranceCase | None:
        with self._store.unit_of_work() as uow:
            found = self._cases.get(uow, case_id)
            uow.rollback()
        return found


__all__ = ["AuditedCaseStore"]
