"""Audit transaction coupling helper (IMP-0064).

`run_audited` makes an authoritative action and its audit record one atomic fact:

* success: the work and its SUCCESS event are written in the SAME unit of work and commit together; if
  either fails, neither exists (a rolled-back action leaves no success event and burns no sequence);
* failure: the work's unit of work is rolled back first, then a FAILURE event (reason = the error's
  stable code) is written in a NEW unit of work, and the ORIGINAL exception is re-raised unchanged;
* if that failure event itself cannot be written, the caller gets AuditFailureNotRecordedError (chained
  to the audit error) instead of a silent loss of the record -- an unaudited failure is never swallowed.

Only `Exception` is audited: KeyboardInterrupt / SystemExit propagate without a record because the
process is stopping and nothing can be promised. The work function must not commit; it receives the
unit of work, does its writes, and returns its value together with the success description.

PROPOSED engineering decisions (ERR-CL-018): failure events written after rollback in a second unit of
work; `INTERNAL_ERROR` as the reason for non-Trinetra exceptions; exceptions only, not BaseException.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from trinetra.domain.audit import AuditOutcome
from trinetra.errors import TrinetraError
from trinetra.infrastructure.audit_repository import AuditDraft

if TYPE_CHECKING:
    from collections.abc import Callable

    from trinetra.domain.audit import AuditEvent
    from trinetra.infrastructure.audit_repository import AuditRepository
    from trinetra.infrastructure.unit_of_work import CoreStore, UnitOfWork

INTERNAL_ERROR: Final = "INTERNAL_ERROR"


class AuditFailureNotRecordedError(TrinetraError):
    code = "AUDIT_FAILURE_NOT_RECORDED"
    default_message = "an action failed and the failure could not be written to the audit journal"


@dataclass(frozen=True, slots=True)
class AuditedResult[T]:
    value: T
    event: AuditEvent


def reason_for(error: Exception) -> str:
    """The stable code to record for an exception (never its message, which may hold data)."""
    return error.code if isinstance(error, TrinetraError) else INTERNAL_ERROR


def run_audited[T](
    store: CoreStore,
    repository: AuditRepository,
    work: Callable[[UnitOfWork], tuple[T, AuditDraft]],
    *,
    on_failure: AuditDraft | None,
) -> AuditedResult[T]:
    """Run `work` and audit it atomically. `work` returns (value, SUCCESS draft).

    `on_failure` describes the FAILURE event to record if the action fails (its outcome and reason are
    set here); pass None to opt out of failure auditing explicitly.
    """
    try:
        with store.unit_of_work() as uow:
            value, success = work(uow)
            event = repository.append(uow, replace(success, outcome=AuditOutcome.SUCCESS, reason_code=None))
            uow.commit()
        return AuditedResult(value, event)
    except Exception as error:
        if on_failure is not None:
            _record_failure(store, repository, on_failure, error)
        raise


def _record_failure(
    store: CoreStore, repository: AuditRepository, template: AuditDraft, error: Exception
) -> None:
    draft = replace(template, outcome=AuditOutcome.FAILURE, reason_code=reason_for(error))
    try:
        with store.unit_of_work() as uow:
            repository.append(uow, draft)
            uow.commit()
    except Exception as audit_error:
        msg = "the action failed and its failure could not be audited"
        raise AuditFailureNotRecordedError(
            msg, details={"original_error": type(error).__name__}
        ) from audit_error


__all__ = [
    "INTERNAL_ERROR",
    "AuditFailureNotRecordedError",
    "AuditedResult",
    "reason_for",
    "run_audited",
]
