"""IMP-0074 tests: creating an intake reserves ids, writes nothing, and replays only within the process."""

from __future__ import annotations

import threading
import time

import pytest
from pydantic import ValidationError

from intake_support import T0, T1, T2, FrozenClock, SequentialIds
from trinetra.application.errors import (
    ApplicationConflictError,
    IntakeNotFoundError,
)
from trinetra.application.intake.commands import CreateIntakeCommand
from trinetra.application.intake.enums import IntakeState
from trinetra.application.intake.registry import InMemoryIntakeRegistry
from trinetra.application.intake.services import CreateIntakeService
from trinetra.domain.decisions import CommandId
from trinetra.domain.intake_ids import IntakeId
from trinetra.domain.timing import UtcTimestamp


def make():
    clock, ids, registry = FrozenClock(), SequentialIds(), InMemoryIntakeRegistry()
    return CreateIntakeService(clock, ids, registry), clock, ids, registry


def cmd(n=1, label=None):
    command_id = CommandId.from_entropy(lambda size: bytes([n]) * size)
    return CreateIntakeCommand(command_id=command_id, case_label=label)


def test_a_new_intake_is_open_empty_and_reserves_a_case_id_without_creating_a_case():
    service, _, _, registry = make()
    s = service.create(cmd(label="Night audit"))
    assert s.intake_state is IntakeState.OPEN
    assert s.transfers == ()
    assert s.case_label == "Night audit"
    assert str(s.reserved_case_id).startswith("case_")
    assert registry.require(s.intake_id) == s


def test_timestamps_come_from_the_injected_clock_and_start_equal():
    service, clock, _, _ = make()
    clock.set(T1)
    s = service.create(cmd())
    assert s.created_at == s.updated_at == T1


def test_every_intake_gets_distinct_ids():
    service, _, _, _ = make()
    a, b = service.create(cmd(1)), service.create(cmd(2))
    assert a.intake_id != b.intake_id
    assert a.reserved_case_id != b.reserved_case_id


def test_the_case_label_is_optional_and_validated():
    service, _, _, _ = make()
    assert service.create(cmd(1)).case_label is None
    with pytest.raises(ValidationError):
        cmd(2, label="bad\nlabel")
    with pytest.raises(ValidationError):
        cmd(3, label="")


def test_repeating_a_command_id_returns_the_same_intake_without_a_second_one():
    service, clock, _, registry = make()
    first = service.create(cmd(7, "A"))
    clock.set(T1)
    again = service.create(cmd(7, "A"))
    assert again == first
    assert again.created_at == T0  # nothing was re-created
    assert len(registry._sessions) == 1


def test_the_same_command_id_with_a_different_label_is_a_conflict_not_a_second_intake():
    service, _, _, registry = make()
    service.create(cmd(7, "A"))
    with pytest.raises(ApplicationConflictError, match="different case label"):
        service.create(cmd(7, "B"))
    with pytest.raises(ApplicationConflictError):
        service.create(cmd(7, None))
    assert len(registry._sessions) == 1


def test_concurrent_identical_commands_create_exactly_one_intake():
    service, _, _, registry = make()
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(service.create(cmd(9, "same")))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.intake_id for r in results}) == 1
    assert len(registry._sessions) == 1


def test_replay_is_process_local_only_a_fresh_registry_knows_nothing():
    first_service, _, _, _ = make()
    s = first_service.create(cmd(5))
    _, _, _, second_registry = make()
    assert second_registry.get(s.intake_id) is None  # open intakes are not restart-durable
    assert second_registry.recall(cmd(5).command_id) is None


def test_registry_add_get_require_replace_and_unknown_ids():
    service, _, _, registry = make()
    s = service.create(cmd())
    assert registry.get(s.intake_id) == s
    with pytest.raises(ApplicationConflictError):
        registry.add(s)
    unknown = IntakeId.from_entropy(lambda size: b"\xee" * size)
    assert registry.get(unknown) is None
    with pytest.raises(IntakeNotFoundError):
        registry.require(unknown)
    with pytest.raises(IntakeNotFoundError):
        registry.replace(s.evolve(intake_id=unknown))
    with pytest.raises(IntakeNotFoundError), registry.exclusive(unknown):
        pass


def test_replace_refuses_stale_or_rewritten_history():
    service, clock, _, registry = make()
    clock.set(T1)
    s = service.create(cmd())
    registry.replace(s.abandoned(T1))
    with pytest.raises(ApplicationConflictError, match="stale"):
        registry.replace(s.evolve(updated_at=T1, created_at=T0))  # created_at rewritten
    older = s.abandoned(T1).evolve(created_at=T0, updated_at=T0)
    with pytest.raises(ApplicationConflictError, match="stale"):
        registry.replace(older)


def test_the_exclusive_lock_is_reentrant_and_serializes_other_threads():
    service, _, _, registry = make()
    s = service.create(cmd())
    order = []

    def other():
        with registry.exclusive(s.intake_id):
            order.append("other")

    with registry.exclusive(s.intake_id):
        with registry.exclusive(s.intake_id):  # same thread may re-enter
            t = threading.Thread(target=other)
            t.start()
            t.join(timeout=0.2)
            assert order == []  # blocked while we hold it
        order.append("mine")
    t.join(timeout=2)
    assert order == ["mine", "other"]


class _SlowIds(SequentialIds):
    """Widens the window between 'did I see this command?' and 'record it'."""

    def new_intake_id(self):
        time.sleep(0.05)
        return super().new_intake_id()


class _TickingClock:
    """Every call returns a later moment, so created_at == updated_at is never an accident."""

    def __init__(self):
        self._n = 0

    def now(self):
        self._n += 1
        return UtcTimestamp.parse(f"2026-10-31T08:{self._n // 60:02d}:{self._n % 60:02d}Z")


def test_the_check_and_the_insert_are_atomic_even_when_creation_is_slow():
    registry = InMemoryIntakeRegistry()
    service = CreateIntakeService(FrozenClock(), _SlowIds(), registry)
    results, barrier = [], threading.Barrier(4)

    def worker():
        barrier.wait()
        results.append(service.create(cmd(4)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.intake_id for r in results}) == 1
    assert len(registry._sessions) == 1


def test_a_new_intake_reads_the_clock_once_so_both_stamps_agree():
    s = CreateIntakeService(_TickingClock(), SequentialIds(), InMemoryIntakeRegistry()).create(cmd())
    assert s.created_at == s.updated_at


def test_replace_refuses_an_older_updated_at_with_the_same_history():
    service, _, _, registry = make()
    s = service.create(cmd())
    newer = s.abandoned(T2)
    registry.replace(newer)
    older = s.evolve(updated_at=T1)
    with pytest.raises(ApplicationConflictError, match="stale"):
        registry.replace(older)


def test_the_first_remembered_command_wins_and_is_never_overwritten():
    service, _, _, registry = make()
    s = service.create(cmd(6, "first"))
    registry.remember(cmd(6).command_id, IntakeId.from_entropy(lambda size: b"\xaa" * size), "other")
    assert registry.recall(cmd(6).command_id) == (s.intake_id, "first")
