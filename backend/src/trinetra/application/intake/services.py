"""CreateIntakeService (IMP-0074).

Creates an OPEN IntakeSession with a fresh IntakeId and a RESERVED CaseId. No database write happens and
no Case exists. A repeated command id within this process returns the original intake (process-local
replay only); the same id with a different case label is a conflict, never a silent second intake.
"""

from __future__ import annotations

import threading

from trinetra.application.errors import ApplicationConflictError
from trinetra.application.intake.commands import CreateIntakeCommand
from trinetra.application.intake.enums import IntakeState
from trinetra.application.intake.records import IntakeSession
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.ports.clock import ClockPort
from trinetra.application.ports.ids import IdFactoryPort


class CreateIntakeService:
    def __init__(self, clock: ClockPort, ids: IdFactoryPort, registry: InMemoryIntakeRegistry) -> None:
        self._clock = clock
        self._ids = ids
        self._registry = registry
        self._lock = threading.Lock()  # makes recall-then-add atomic for concurrent identical commands

    def create(self, command: CreateIntakeCommand) -> IntakeSession:
        with self._lock:
            return self._create_locked(command)

    def _create_locked(self, command: CreateIntakeCommand) -> IntakeSession:
        fingerprint = command.case_label
        previous = self._registry.recall(command.command_id)
        if previous is not None:
            intake_id, remembered = previous
            if remembered != fingerprint:
                raise ApplicationConflictError(
                    "this command id was already used with a different case label",
                    details={"command_id": str(command.command_id)},
                )
            return self._registry.require(intake_id)
        now = self._clock.now()
        session = IntakeSession(
            intake_id=self._ids.new_intake_id(),
            intake_state=IntakeState.OPEN,
            case_label=command.case_label,
            reserved_case_id=self._ids.new_case_id(),
            created_at=now,
            updated_at=now,
            transfers=(),
        )
        self._registry.add(session)
        self._registry.remember(command.command_id, session.intake_id, fingerprint)
        return session


__all__ = ["CreateIntakeService"]
