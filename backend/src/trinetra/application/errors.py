"""Application error root (EIB-04).

Domain errors describe invalid domain truth; application errors describe invalid use-case or workflow
execution; persistence errors describe storage failure. Transport maps these and never collapses them.
Only the errors a packet actually raises are defined; later packets add theirs.
"""

from __future__ import annotations

from trinetra.errors import TrinetraError


class ApplicationError(TrinetraError):
    code = "APPLICATION_ERROR"
    default_message = "application error"


class ApplicationConflictError(ApplicationError):
    code = "APPLICATION_CONFLICT"
    default_message = "the request conflicts with the current state"


class IntakeStateConflictError(ApplicationConflictError):
    code = "INTAKE_STATE_CONFLICT"
    default_message = "the intake cannot accept this operation in its current state"


class IntakeNotReadyError(IntakeStateConflictError):
    code = "INTAKE_NOT_READY"
    default_message = "the intake does not yet hold what a commit needs"


class CaseAlreadyStoredError(ApplicationConflictError):
    code = "CASE_ALREADY_STORED"
    default_message = "a case with this id is already stored"


class CommandValidationError(ApplicationError):
    code = "COMMAND_VALIDATION_FAILED"
    default_message = "the command is not valid"


class IntakeNotFoundError(ApplicationError):
    code = "INTAKE_NOT_FOUND"
    default_message = "no such intake"


class TransferTooLargeError(ApplicationError):
    code = "TRANSFER_TOO_LARGE"
    default_message = "the upload is larger than the allowed size"


class TransferRejectedError(ApplicationError):
    code = "TRANSFER_REJECTED"
    default_message = "the upload was rejected"


__all__ = [
    "ApplicationConflictError",
    "ApplicationError",
    "CaseAlreadyStoredError",
    "CommandValidationError",
    "IntakeNotFoundError",
    "IntakeNotReadyError",
    "IntakeStateConflictError",
    "TransferRejectedError",
    "TransferTooLargeError",
]
