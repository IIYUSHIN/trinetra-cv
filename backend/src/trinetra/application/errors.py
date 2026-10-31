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


class CommandValidationError(ApplicationError):
    code = "COMMAND_VALIDATION_FAILED"
    default_message = "the command is not valid"


__all__ = [
    "ApplicationConflictError",
    "ApplicationError",
    "CommandValidationError",
    "IntakeStateConflictError",
]
