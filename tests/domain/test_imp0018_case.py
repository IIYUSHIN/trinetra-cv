"""IMP-0018 tests: CaseId and CaseStateRevision (plus the Counter base)."""

from __future__ import annotations

import copy
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.base import DomainModel
from trinetra.domain.case import CaseId, CaseStateRevision
from trinetra.domain.counters import MAX_COUNTER, Counter, CounterError
from trinetra.domain.ids import OpaqueId


def test_case_id_is_an_opaque_case_prefixed_identifier():
    ident = CaseId.generate()
    assert isinstance(ident, OpaqueId) and str(ident).startswith("case_")
    assert CaseId.PREFIX == "case"


# ------------------------------------------------------------------ revision
def test_first_is_one_and_next_steps_by_exactly_one():
    r = CaseStateRevision.first()
    assert r.value == 1 and int(r) == 1 and r.next().value == 2 and r.value == 1
    assert repr(r) == "CaseStateRevision(1)"


def test_boundaries():
    assert CaseStateRevision(1).value == 1
    assert CaseStateRevision(MAX_COUNTER).value == MAX_COUNTER
    for bad in (0, -1, MAX_COUNTER + 1, 2**64):
        with pytest.raises(CounterError, match="within"):
            CaseStateRevision(bad)
    with pytest.raises(CounterError):
        CaseStateRevision(MAX_COUNTER).next()  # never wraps


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", None, b"1", [1], CaseStateRevision(1)])
def test_only_plain_ints_accepted(bad):
    with pytest.raises(CounterError, match="expected int"):
        CaseStateRevision(bad)


def test_ordering_equality_and_hash_are_typed():
    class OtherRevision(Counter):
        KIND = "other_revision"
        __slots__ = ()

    a, b = CaseStateRevision(1), CaseStateRevision(2)
    assert a < b and b > a and a <= CaseStateRevision(1) and b >= CaseStateRevision(2)
    assert a != b and a == CaseStateRevision(1)
    assert hash(a) == hash(CaseStateRevision(1))
    assert a != OtherRevision(1) and hash(a) != hash(OtherRevision(1))
    assert a != 1 and (a == 1) is False
    with pytest.raises(TypeError, match="cannot order"):
        _ = a < OtherRevision(2)
    with pytest.raises(TypeError, match="cannot order"):
        _ = a < 2  # type: ignore[operator]


def test_not_usable_as_a_raw_integer_by_accident():
    r = CaseStateRevision(3)
    with pytest.raises(TypeError, match="not an integer"):
        [0, 1, 2, 3][r]  # type: ignore[index]
    with pytest.raises(TypeError):
        _ = r + 1  # type: ignore[operator]


def test_immutable_copy_pickle():
    r = CaseStateRevision(5)
    with pytest.raises(AttributeError):
        r._n = 6
    with pytest.raises(AttributeError):
        del r._n
    assert copy.copy(r) is r and copy.deepcopy(r) is r
    assert pickle.loads(pickle.dumps(r)) == r


def test_subclass_must_declare_kind_and_slots():
    with pytest.raises(CounterError):
        type("NoKind", (Counter,), {"__slots__": ()})
    with pytest.raises(CounterError):
        type("NoSlots", (Counter,), {"KIND": "x"})


# ----------------------------------------------- inside a projection-like model
class Projection(DomainModel):
    case_id: CaseId
    case_state_revision: CaseStateRevision


def test_projection_carries_both_fields_and_serialises_to_plain_json_types():
    p = Projection(case_id=CaseId.generate(), case_state_revision=CaseStateRevision(7))
    wire = p.to_wire()
    assert wire["case_state_revision"] == 7 and isinstance(wire["case_state_revision"], int)
    assert Projection(**wire) == p
    assert p.canonical_bytes().decode().count('"case_state_revision":7') == 1


@pytest.mark.parametrize("bad", [0, -3, True, "7", 7.0, None, MAX_COUNTER + 1])
def test_projection_rejects_bad_revisions(bad):
    with pytest.raises(ValidationError):
        Projection(case_id=CaseId.generate(), case_state_revision=bad)


def test_revision_of_another_kind_is_rejected_in_a_model():
    class AttemptLike(Counter):
        KIND = "attempt_like"
        __slots__ = ()

    with pytest.raises(ValidationError):
        Projection(case_id=CaseId.generate(), case_state_revision=AttemptLike(1))


def test_a_generic_source_revision_field_cannot_be_smuggled_in():
    with pytest.raises(ValidationError):
        Projection(case_id=CaseId.generate(), case_state_revision=1, source_revision=1)


@given(st.integers(1, MAX_COUNTER - 1))
def test_property_next_is_strictly_greater_and_wire_roundtrips(n):
    r = CaseStateRevision(n)
    assert r.next() > r and r.next().value == n + 1
    assert CaseStateRevision(r.to_wire()) == r
