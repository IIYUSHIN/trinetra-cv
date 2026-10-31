"""Tests for WireEnum (shared by every state-dimension enum packet)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.base import DomainModel
from trinetra.domain.enums import WireEnum


class Colour(WireEnum):
    RED = "RED"
    BLUE = "BLUE"


class Shade(WireEnum):
    RED = "RED"


class Holder(DomainModel):
    colour: Colour


def test_member_and_exact_wire_string_accepted_and_serialised_to_string():
    assert Holder(colour=Colour.RED).to_wire() == {"colour": "RED"}
    assert Holder(colour="BLUE").colour is Colour.BLUE
    assert Holder.model_validate_json('{"colour":"RED"}').colour is Colour.RED
    assert Holder(colour=Colour.RED).canonical_bytes() == b'{"colour":"RED"}'


@pytest.mark.parametrize("bad", ["red", "Red", " RED", "RED ", "GREEN", "", 1, None, b"RED", Shade.RED])
def test_everything_else_is_rejected(bad):
    with pytest.raises(ValidationError):
        Holder(colour=bad)


def test_members_of_different_enums_and_bare_strings_are_not_equal():
    assert Colour.RED != Shade.RED and (Colour.RED == "RED") is False
    assert hash(Colour.RED) != hash(Shade.RED)
    assert Colour.from_wire("RED") == Colour.RED and {Colour.RED: 1}[Colour.RED] == 1


def test_from_wire_is_exact():
    for bad in ("red", "X", 3, None, Colour.RED):
        with pytest.raises(ValueError):
            Colour.from_wire(bad)


def test_members_are_unordered_so_spelling_never_ranks_a_dimension():
    import operator

    from trinetra.domain.assets import AssetKind

    a, b = AssetKind.DATASET, AssetKind.MODEL
    for op in (operator.lt, operator.le, operator.gt, operator.ge):
        with pytest.raises(TypeError):
            op(a, b)
        with pytest.raises(TypeError):
            op(a, "model")
    with pytest.raises(TypeError):
        sorted([b, a])
