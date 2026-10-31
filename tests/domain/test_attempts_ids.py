"""Tests for the identifiers in trinetra.domain.attempts (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.attempts import AttemptNumber, ExecutionAttemptId
from trinetra.domain.case import CaseStateRevision
from trinetra.domain.counters import CounterError
from trinetra.domain.ids import IdentifierError, OpaqueId

CASES = [('ExecutionAttemptId', 'attempt')]

CLASSES = {"ExecutionAttemptId": ExecutionAttemptId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


def test_attempt_number_is_a_one_based_counter_distinct_from_revisions():
    n = AttemptNumber.first()
    assert n.value == 1 and n.next().value == 2 and n.to_wire() == 1
    assert n != CaseStateRevision(1) and hash(n) != hash(CaseStateRevision(1))
    with pytest.raises(CounterError):
        AttemptNumber(0)
    with pytest.raises(TypeError, match="cannot order"):
        _ = n < CaseStateRevision(2)  # type: ignore[operator]


def test_attempt_identity_is_the_id_not_the_number():
    from pydantic import ValidationError

    from trinetra.domain.base import DomainModel

    class Attempt(DomainModel):
        attempt_id: ExecutionAttemptId
        attempt_number: AttemptNumber

    a = Attempt(attempt_id=ExecutionAttemptId.generate(), attempt_number=AttemptNumber(2))
    b = Attempt(attempt_id=ExecutionAttemptId.generate(), attempt_number=AttemptNumber(2))
    assert a != b and a.to_wire()["attempt_number"] == 2 and Attempt(**a.to_wire()) == a
    with pytest.raises(ValidationError):
        Attempt(attempt_id=ExecutionAttemptId.generate(), attempt_number=CaseStateRevision(2))
