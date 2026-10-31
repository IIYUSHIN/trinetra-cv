"""IMP-0081 tests: CaseLifecycleState wire forms, closedness and the absent states."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from trinetra.domain.case_lifecycle import CaseLifecycleState as L
from trinetra.domain.enums import WireEnum

NAMES = ["PREFLIGHT", "ASSESSING", "RESULTS_AVAILABLE", "INVESTIGATING", "DECISION_READY", "DECIDED",
         "ATTESTED"]  # fmt: skip


def test_members_are_the_canonical_v1_names_in_order_with_lowercase_wire_forms():
    assert issubclass(L, WireEnum)
    assert [m.name for m in L] == NAMES
    assert [m.to_wire() for m in L] == [n.lower() for n in NAMES]


@pytest.mark.parametrize("name", NAMES)
def test_parsing_is_exact_and_closed(name):
    assert L.from_wire(name.lower()) is L[name]
    for bad in (name, name.title(), " " + name.lower(), name.lower() + " ", name.lower()[:-1]):
        with pytest.raises(ValueError):
            L.from_wire(bad)


@pytest.mark.parametrize("bad", ["", "ready", "READY", "safe", "high_risk", "status", None, 0, 1, b"x"])
def test_ready_and_verdict_like_states_do_not_exist(bad):
    with pytest.raises(ValueError):
        L.from_wire(bad)


def test_members_are_not_interchangeable_with_strings():
    assert L.PREFLIGHT != "preflight"
    assert L.PREFLIGHT != "PREFLIGHT"


def test_strict_models_accept_only_members_or_exact_wire_text():
    class M(BaseModel):
        model_config = ConfigDict(strict=True)
        state: L

    assert M(state=L.DECIDED).state is L.DECIDED
    assert M.model_validate({"state": "decided"}).state is L.DECIDED
    for bad in ("DECIDED", "Decided", 3):
        with pytest.raises(ValidationError):
            M.model_validate({"state": bad})
