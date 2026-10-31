"""IMP-0017 tests: opaque identifier base type."""

from __future__ import annotations

import copy
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain import ids
from trinetra.domain.base import DomainModel
from trinetra.domain.ids import ID_BYTES, IdentifierError, OpaqueId

HEX = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(ids, "_REGISTRY", dict(ids._REGISTRY))


class AlphaId(OpaqueId):
    PREFIX = "alpha"
    __slots__ = ()


class BetaId(OpaqueId):
    PREFIX = "beta"
    __slots__ = ()


# ------------------------------------------------------------------ happy path
def test_parse_str_roundtrip_and_value_equality():
    a = AlphaId.parse(f"alpha_{HEX}")
    assert str(a) == f"alpha_{HEX}" and a.to_wire() == str(a)
    assert repr(a) == f"AlphaId('alpha_{HEX}')"
    assert a == AlphaId.parse(str(a)) and hash(a) == hash(AlphaId.parse(str(a)))
    assert {a: 1}[AlphaId.parse(str(a))] == 1


def test_generate_is_128_bit_random_and_well_formed():
    seen = {AlphaId.generate() for _ in range(200)}
    assert len(seen) == 200
    for ident in seen:
        assert ids._HEX_RE.fullmatch(str(ident).split("_", 1)[1])


def test_from_entropy_is_deterministic_for_a_fixed_source():
    ident = AlphaId.from_entropy(lambda n: bytes(range(1, n + 1)))
    assert str(ident) == "alpha_0102030405060708090a0b0c0d0e0f10"
    assert ID_BYTES == 16


# --------------------------------------------------- types cannot be confused
def test_different_id_types_never_equal_even_with_identical_bodies():
    a, b = AlphaId.parse(f"alpha_{HEX}"), BetaId.parse(f"beta_{HEX}")
    assert a != b
    assert (a == f"alpha_{HEX}") is False and (a == HEX) is False
    assert len({a, b}) == 2 and hash(a) != hash(b)


def test_cross_type_parse_is_rejected():
    for text in (f"beta_{HEX}", f"alpha_{HEX}x", f"Alpha_{HEX}"):
        with pytest.raises(IdentifierError):
            AlphaId.parse(text)


def test_registry_maps_each_prefix_to_exactly_one_class():
    reg = ids.registered_id_types()
    assert reg["alpha"] is AlphaId and reg["beta"] is BetaId


# ------------------------------------------------------------------ negatives
@pytest.mark.parametrize(
    "text",
    ["", "alpha", "alpha_", f"alpha{HEX}", f"alpha_{HEX.upper()}", f"alpha_{HEX[:-1]}", f"alpha_{HEX}0",
     f"alpha_{HEX[:-1]}g", f" alpha_{HEX}", f"alpha_{HEX} ", f"alpha_{HEX}\n", f"alpha__{HEX}",
     f"alpha_{HEX[:16]}_{HEX[16:]}", "alpha_" + "é" * 32, "ALPHA_" + HEX],
)
def test_malformed_text_rejected(text):
    with pytest.raises(IdentifierError):
        AlphaId.parse(text)


@pytest.mark.parametrize("value", [None, 5, b"alpha_" + HEX.encode(), ["x"], AlphaId.generate()])
def test_non_str_rejected(value):
    with pytest.raises(IdentifierError, match="expected str"):
        AlphaId.parse(value)


def test_constructor_and_abstract_base_are_closed():
    with pytest.raises(IdentifierError, match="not the constructor"):
        AlphaId()
    with pytest.raises(IdentifierError, match="abstract"):
        OpaqueId.parse(f"x_{HEX}")
    with pytest.raises(IdentifierError, match="abstract"):
        OpaqueId.generate()


@pytest.mark.parametrize("raw", [b"", b"\x00" * 16, b"\x01" * 15, b"\x01" * 17, "x" * 16, None])
def test_bad_entropy_rejected(raw):
    with pytest.raises(IdentifierError):
        AlphaId.from_entropy(lambda n: raw)  # noqa: ARG005


@pytest.mark.parametrize("prefix", ["", "A", "a", "1ab", "toolongprefix", "a_b", "a-b", "é", None, 5])
def test_invalid_prefix_rejected_at_class_creation(prefix):
    with pytest.raises(IdentifierError, match="PREFIX"):
        type("Bad", (OpaqueId,), {"PREFIX": prefix, "__slots__": ()})


def test_duplicate_prefix_rejected_and_inherited_prefix_does_not_count():
    with pytest.raises(IdentifierError, match="already used"):
        type("Again", (OpaqueId,), {"PREFIX": "alpha", "__slots__": ()})
    with pytest.raises(IdentifierError, match="PREFIX"):
        type("Child", (AlphaId,), {"__slots__": ()})


def test_subclass_without_slots_is_rejected():
    with pytest.raises(IdentifierError, match="__slots__"):
        type("Dicty", (OpaqueId,), {"PREFIX": "dicty"})


def test_instances_are_immutable_and_have_no_dict():
    a = AlphaId.generate()
    with pytest.raises(AttributeError):
        a._hex = "0" * 32
    with pytest.raises(AttributeError):
        a.extra = 1
    with pytest.raises(AttributeError):
        del a._hex
    assert not hasattr(a, "__dict__")


def test_copy_and_pickle_preserve_type_and_value():
    a = AlphaId.generate()
    assert copy.copy(a) is a and copy.deepcopy(a) is a
    clone = pickle.loads(pickle.dumps(a))
    assert clone == a and type(clone) is AlphaId


def test_unpickling_a_forged_identifier_is_rejected():
    with pytest.raises(IdentifierError):
        ids._rebuild("nope", HEX)
    with pytest.raises(IdentifierError):
        ids._rebuild("alpha", "not-hex")


# ----------------------------------------------------- inside domain models
class Holder(DomainModel):
    a: AlphaId
    b: BetaId | None = None


def test_domain_model_accepts_instances_and_wire_strings_and_serialises_to_str():
    a, b = AlphaId.generate(), BetaId.generate()
    held = Holder(a=a, b=b)
    assert held.to_wire() == {"a": str(a), "b": str(b)}
    assert Holder(**held.to_wire()) == held
    assert held.canonical_bytes() == f'{{"a":"{a}","b":"{b}"}}'.encode()


def test_domain_model_rejects_wrong_type_or_wrong_prefix():
    a = AlphaId.generate()
    for bad in ({"a": BetaId.generate()}, {"a": f"beta_{HEX}"}, {"a": HEX}, {"a": 5}, {"a": None}):
        with pytest.raises(ValidationError):
            Holder(**bad)
    with pytest.raises(ValidationError):
        Holder(a=a, b=a)


def test_json_mode_validation_parses_strings():
    a = AlphaId.generate()
    assert Holder.model_validate_json(f'{{"a":"{a}","b":null}}').a == a
    with pytest.raises(ValidationError):
        Holder.model_validate_json('{"a":5}')


# ------------------------------------------------------------------ property
@given(st.binary(min_size=16, max_size=16).filter(any))
def test_property_entropy_roundtrips_through_text(raw):
    ident = AlphaId.from_entropy(lambda _n: raw)
    assert AlphaId.parse(str(ident)) == ident and str(ident) == f"alpha_{raw.hex()}"


@given(st.text(max_size=60))
def test_property_parse_accepts_only_canonical_form(text):
    try:
        ident = AlphaId.parse(text)
    except IdentifierError:
        return
    assert str(ident) == text
