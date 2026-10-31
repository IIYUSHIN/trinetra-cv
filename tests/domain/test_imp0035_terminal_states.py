"""IMP-0035 tests: execution terminal-state family."""

from __future__ import annotations

import pytest

from trinetra.domain.execution import TERMINAL_EXECUTION_STATES, ExecutionState, is_terminal

E = ExecutionState


def test_terminal_family_is_exactly_completed_cancelled_failed_infrastructure():
    assert {E.COMPLETED, E.CANCELLED, E.FAILED_INFRASTRUCTURE} == TERMINAL_EXECUTION_STATES
    assert {s for s in E if is_terminal(s)} == set(TERMINAL_EXECUTION_STATES)


@pytest.mark.parametrize("state", [E.PENDING, E.RUNNING, E.CANCEL_REQUESTED])
def test_non_terminal_states(state):
    assert is_terminal(state) is False


def test_cancel_requested_is_not_a_cancellation():
    assert E.CANCEL_REQUESTED != E.CANCELLED and not is_terminal(E.CANCEL_REQUESTED)


@pytest.mark.parametrize("bad", ["COMPLETED", "completed", None, 1, True])
def test_is_terminal_rejects_non_members(bad):
    with pytest.raises(TypeError):
        is_terminal(bad)


def test_failed_infrastructure_is_separate_from_scientific_error():
    assert "ERROR" not in {s.name for s in E} and "FAILED" not in {s.name for s in E}
    assert E.FAILED_INFRASTRUCTURE.value == "FAILED_INFRASTRUCTURE"
