"""IMP-0105 tests: wire forms and exact parsing for ExecutionAssuranceClass."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import trinetra.domain.execution_assurance as m
from trinetra.domain.enums import WireEnum

GOLDEN = {
    "ExecutionAssuranceClass": [
        "DATAFLOW_GRAPH",
        "SCRIPTED_GRAPH",
        "TRUSTED_ARCHITECTURE_WEIGHTS_ONLY",
        "SANDBOXED_CODE_BEARING_PACKAGE",
    ],
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_members_and_wire_values_are_exactly_the_contract(name):
    cls = getattr(m, name)
    assert issubclass(cls, WireEnum)
    assert [x.name for x in cls] == GOLDEN[name]  # order is part of the contract
    assert [x.value for x in cls] == GOLDEN[name]
    assert [x.to_wire() for x in cls] == GOLDEN[name]


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_parsing_is_exact_and_closed(name):
    cls = getattr(m, name)
    for member in GOLDEN[name]:
        assert cls.from_wire(member) is cls(member)
        for bad in (member.lower(), member.title(), " " + member, member + " ", member + "\n", member[:-1]):
            with pytest.raises(ValueError):
                cls.from_wire(bad)
    for bad in ("", "NOT_A_MEMBER", "status", None, 0, 1, b"X", ["X"]):
        with pytest.raises(ValueError):
            cls.from_wire(bad)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_members_are_typed_not_bare_strings_and_hashable(name):
    cls = getattr(m, name)
    first = next(iter(cls))
    assert first != first.value
    assert cls(first.value) == first and len({*cls}) == len(GOLDEN[name])
    assert {first: 1}[first] == 1


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_pydantic_roundtrip_accepts_member_or_exact_wire_only(name):
    cls = getattr(m, name)

    class Holder(BaseModel):
        model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
        value: cls  # type: ignore[valid-type]

    for member in cls:
        assert Holder(value=member).value is member
        assert Holder(value=member.value).value is member
    for bad in ("", "nope", GOLDEN[name][0].lower(), None, 3):
        with pytest.raises(ValidationError):
            Holder(value=bad)


def test_no_member_is_a_generic_status_or_a_score():
    for name in GOLDEN:
        for member in getattr(m, name):
            assert member.name.upper() == member.name and "SCORE" not in member.name
