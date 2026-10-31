"""ExecutionState enum and terminal-state family (IMP-0035).

Wire form is the exact uppercase string from the frozen contract. Each enum is its own dimension:
members never compare equal across enums and are never interchangeable with bare strings.
"""

from __future__ import annotations

from typing import Final

from trinetra.domain.enums import WireEnum


class ExecutionState(WireEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"


TERMINAL_EXECUTION_STATES: Final = frozenset(
    {
        ExecutionState.COMPLETED,
        ExecutionState.CANCELLED,
        ExecutionState.FAILED_INFRASTRUCTURE,
    }
)


def is_terminal(state: ExecutionState) -> bool:
    """True once an execution can no longer change state. CANCEL_REQUESTED is NOT terminal:
    a request is not a cancellation, and the work may still complete or fail first.

    Transition rules are deliberately absent: the corpus lists the states and the terminal family
    but no transition table, so the Core state-machine packet owns that (no local invention).
    FAILED_INFRASTRUCTURE is an execution outcome and is unrelated to the scientific
    TestVerdict.ERROR, which lives in another dimension.
    """
    if not isinstance(state, ExecutionState):
        raise TypeError("state must be an ExecutionState")
    return state in TERMINAL_EXECUTION_STATES


__all__ = ["TERMINAL_EXECUTION_STATES", "ExecutionState", "is_terminal"]
