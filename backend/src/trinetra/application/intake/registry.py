"""InMemoryIntakeRegistry (EIB-04): process-local intake coordination.

A deliberate walking-skeleton mechanism: OPEN intakes are NOT restart-durable (committed assets and
cases are). The limitation is explicit, bounded, corrupts no authoritative truth, and closes with the
durable coordination/idempotency migration later (LAW-GR-WI-022). Never present it as final behaviour.

Provides add / get / require / replace / exclusive(intake_id) with process-local locks, plus a small
command ledger so a repeated create-intake command id returns the same intake within this process. That
is process-local replay only; durable command replay protection does not exist yet and is not claimed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from trinetra.application.errors import ApplicationConflictError, IntakeNotFoundError
from trinetra.application.intake.records import IntakeSession
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId


class InMemoryIntakeRegistry:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._sessions: dict[IntakeId, IntakeSession] = {}
        self._locks: dict[IntakeId, threading.RLock] = {}
        self._commands: dict[CommandId, tuple[IntakeId, str | None]] = {}

    def add(self, session: IntakeSession) -> None:
        with self._guard:
            if session.intake_id in self._sessions:
                raise ApplicationConflictError(
                    "intake already exists", details={"intake_id": str(session.intake_id)}
                )
            self._sessions[session.intake_id] = session
            self._locks[session.intake_id] = threading.RLock()

    def get(self, intake_id: IntakeId) -> IntakeSession | None:
        with self._guard:
            return self._sessions.get(intake_id)

    def require(self, intake_id: IntakeId) -> IntakeSession:
        found = self.get(intake_id)
        if found is None:
            raise IntakeNotFoundError(details={"intake_id": str(intake_id)})
        return found

    def replace(self, session: IntakeSession) -> None:
        """Store a newer version of an existing intake; it must not move time backwards."""
        with self._guard:
            current = self._sessions.get(session.intake_id)
            if current is None:
                raise IntakeNotFoundError(details={"intake_id": str(session.intake_id)})
            if session.updated_at < current.updated_at or session.created_at != current.created_at:
                raise ApplicationConflictError("stale or inconsistent intake version")
            self._sessions[session.intake_id] = session

    @contextmanager
    def exclusive(self, intake_id: IntakeId) -> Iterator[None]:
        """Hold the intake's lock (re-entrant in the same thread) for the duration of the block."""
        with self._guard:
            lock = self._locks.get(intake_id)
        if lock is None:
            raise IntakeNotFoundError(details={"intake_id": str(intake_id)})
        with lock:
            yield

    # -- process-local command ledger ------------------------------------------------------------
    def recall(self, command_id: CommandId) -> tuple[IntakeId, str | None] | None:
        with self._guard:
            return self._commands.get(command_id)

    def remember(self, command_id: CommandId, intake_id: IntakeId, fingerprint: str | None) -> None:
        with self._guard:
            self._commands.setdefault(command_id, (intake_id, fingerprint))


__all__ = ["InMemoryIntakeRegistry"]
