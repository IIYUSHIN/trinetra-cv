"""Tests for the identifiers in trinetra.domain.references (catalog-wide checks live in test_id_catalog)."""

from __future__ import annotations

import pytest

from trinetra.domain.ids import IdentifierError, OpaqueId
from trinetra.domain.references import ReferenceId, ReferencePurpose
from trinetra.domain.tokens import MAX_TOKEN_LENGTH, CanonicalToken, TokenError

CASES = [('ReferenceId', 'ref')]

CLASSES = {"ReferenceId": ReferenceId}


@pytest.mark.parametrize(("name", "prefix"), CASES)
def test_prefix_and_basic_behaviour(name, prefix):
    cls = CLASSES[name]
    assert issubclass(cls, OpaqueId) and prefix == cls.PREFIX and cls.__name__ == name
    ident = cls.generate()
    assert str(ident).startswith(prefix + "_") and cls.parse(str(ident)) == ident
    with pytest.raises(IdentifierError):
        cls.parse("x_" + str(ident).split("_", 1)[1])


# ------------------------------------------------------------ ReferencePurpose
@pytest.mark.parametrize(
    "text", ["a", "clean_reference", "iou_check_2", "a1", "a" * MAX_TOKEN_LENGTH, "x_y_z"]
)
def test_valid_purposes(text):
    p = ReferencePurpose(text)
    assert p.text == text == str(p) == p.to_wire() and p == ReferencePurpose(text)


@pytest.mark.parametrize(
    "text",
    ["", "A", "Clean", "1a", "_a", "a_", "a__b", "a-b", "a b", " a", "a ", "a\n", "é",
     "a" * (MAX_TOKEN_LENGTH + 1), "a.b", None, 5, b"a", ["a"], ReferencePurpose("a")],
)
def test_invalid_purposes(text):
    with pytest.raises(TokenError):
        ReferencePurpose(text)


def test_tokens_of_different_kinds_are_not_equal_and_immutable():
    class AccessName(CanonicalToken):
        KIND = "access_name"
        __slots__ = ()

    assert ReferencePurpose("x") != AccessName("x") and hash(ReferencePurpose("x")) != hash(AccessName("x"))
    assert (ReferencePurpose("x") == "x") is False
    p = ReferencePurpose("x")
    with pytest.raises(AttributeError):
        p._text = "y"
    with pytest.raises(TokenError):
        type("NoKind", (CanonicalToken,), {"__slots__": ()})


def test_purpose_in_a_domain_model_is_strict_and_wire_is_a_string():
    from pydantic import ValidationError

    from trinetra.domain.base import DomainModel

    class Ref(DomainModel):
        reference_id: ReferenceId
        purpose: ReferencePurpose

    ref = Ref(reference_id=ReferenceId.generate(), purpose=ReferencePurpose("clean_reference"))
    assert ref.to_wire()["purpose"] == "clean_reference" and Ref(**ref.to_wire()) == ref
    for bad in ("Clean", "", 3, None, "a b"):
        with pytest.raises(ValidationError):
            Ref(reference_id=ReferenceId.generate(), purpose=bad)
