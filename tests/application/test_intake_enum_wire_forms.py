"""Exact, closed wire forms of the intake dimensions (they are application enums, so the domain golden
manifest does not cover them)."""

from __future__ import annotations

import pytest

from trinetra.application.intake.enums import IntakeState, PackageSlot, TransferState

GOLDEN = {
    IntakeState: ["OPEN", "COMMITTED", "ABANDONED"],
    TransferState: ["RECEIVED", "ADMITTED", "FAILED"],
    PackageSlot: ["dataset", "model", "contributor_metadata", "reference", "inference_records",
                  "assessment_profile"],
}  # fmt: skip


@pytest.mark.parametrize("cls", list(GOLDEN))
def test_members_and_wire_values_are_exactly_the_contract(cls):
    assert [m.value for m in cls] == GOLDEN[cls]
    assert [m.to_wire() for m in cls] == GOLDEN[cls]


@pytest.mark.parametrize("cls", list(GOLDEN))
def test_parsing_is_exact(cls):
    for wire in GOLDEN[cls]:
        assert cls.from_wire(wire) is cls(wire)
        for bad in (wire.upper() if wire.islower() else wire.lower(), " " + wire, wire + " ", wire[:-1]):
            with pytest.raises(ValueError):
                cls.from_wire(bad)


def test_the_dimensions_are_never_interchangeable():
    assert IntakeState.OPEN != "OPEN"
    assert IntakeState.OPEN != TransferState.RECEIVED
    assert {IntakeState, TransferState, PackageSlot}.isdisjoint({str})
