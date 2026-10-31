"""IMP-0033 tests: CapabilityRequirement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.capability import CapabilityName, CapabilityRequirement, CapabilityState

A, L, U = CapabilityState.AVAILABLE, CapabilityState.LIMITED, CapabilityState.UNAVAILABLE


def req(minimum=None, name="gpu_inference"):
    kwargs = {"capability": CapabilityName(name)}
    if minimum is not None:
        kwargs["minimum"] = minimum
    return CapabilityRequirement(**kwargs)


def test_default_minimum_is_available_so_limited_is_never_silently_enough():
    r = req()
    assert r.minimum is A
    assert (r.satisfied_by(A), r.satisfied_by(L), r.satisfied_by(U)) == (True, False, False)


def test_limited_minimum_accepts_available_and_limited_only():
    r = req(L)
    assert (r.satisfied_by(A), r.satisfied_by(L), r.satisfied_by(U)) == (True, True, False)


def test_unavailable_minimum_is_meaningless_and_rejected():
    with pytest.raises(ValidationError, match="means nothing"):
        req(U)


@pytest.mark.parametrize("bad", ["AVAILABLE", "available", None, 1, True])
def test_observed_must_be_a_capability_state_member(bad):
    with pytest.raises(TypeError):
        req().satisfied_by(bad)


def test_enum_itself_stays_unordered():
    with pytest.raises(TypeError):
        _ = A > L


def test_wire_roundtrip_and_name_rules():
    r = req(L)
    assert r.to_wire() == {"capability": "gpu_inference", "minimum": "LIMITED"}
    assert CapabilityRequirement(**r.to_wire()) == r
    for bad in ("GPU", "has space", "", "a" * 65):
        with pytest.raises(ValidationError):
            CapabilityRequirement(capability=bad)
    with pytest.raises(ValidationError):
        CapabilityRequirement(capability="gpu_inference", minimum="MAYBE")
    with pytest.raises(ValidationError):
        CapabilityRequirement(capability="gpu_inference", extra=1)
