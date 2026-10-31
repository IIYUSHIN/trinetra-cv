"""IMP-0016 tests: immutable domain base-model conventions."""

from __future__ import annotations

import json
import pickle
from typing import Annotated, ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ConfigDict, Field, ValidationError

from trinetra.domain.base import DomainConventionError, DomainModel
from trinetra.jcs import canonicalize


class Point(DomainModel):
    x: int
    y: int = 0
    tags: tuple[str, ...] = ()


# ------------------------------------------------------------------ happy path
def test_instances_are_frozen_hashable_and_equal_by_value():
    a = Point(x=1, tags=("a",))
    assert a == Point(x=1, tags=("a",)) and hash(a) == hash(Point(x=1, tags=("a",)))
    assert {a: 1}[Point(x=1, tags=("a",))] == 1
    with pytest.raises(ValidationError):
        a.x = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        del a.x


def test_wire_form_is_json_safe_complete_and_canonical():
    p = Point(x=1, tags=("b", "a"))
    assert p.to_wire() == {"x": 1, "y": 0, "tags": ["b", "a"]}
    assert p.canonical_bytes() == b'{"tags":["b","a"],"x":1,"y":0}'
    assert p.canonical_bytes() == canonicalize(json.loads(json.dumps(p.to_wire())))
    assert p.canonical_digest().startswith("sha256:") and len(p.canonical_digest()) == 71


def test_canonical_form_is_independent_of_field_declaration_and_kwarg_order():
    assert Point(y=2, x=1).canonical_bytes() == Point(x=1, y=2).canonical_bytes()


def test_evolve_revalidates_and_leaves_the_original_untouched():
    a = Point(x=1)
    b = a.evolve(x=5)
    assert (a.x, b.x) == (1, 5) and b.y == 0
    with pytest.raises(ValidationError):
        a.evolve(x="5")
    with pytest.raises(DomainConventionError, match="unknown"):
        a.evolve(z=1)


# ------------------------------------------------------------- strictness
@pytest.mark.parametrize("bad", ["1", 1.0, True, None, b"1"])
def test_no_type_coercion(bad):
    with pytest.raises(ValidationError):
        Point(x=bad)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Point(x=1, z=2)


def test_non_finite_floats_rejected():
    class Measured(DomainModel):
        value: float

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            Measured(value=bad)
    assert Measured(value=1.5).to_wire() == {"value": 1.5}


def test_nested_models_are_revalidated_not_trusted():
    class Outer(DomainModel):
        inner: Point

    assert Outer(inner=Point(x=1)).to_wire() == {"inner": {"x": 1, "y": 0, "tags": []}}
    corrupted = Point(x=1)
    object.__setattr__(corrupted, "x", "not-an-int")  # simulate a bypass of the frozen guard
    with pytest.raises(ValidationError):
        Outer(inner=corrupted)
    with pytest.raises(ValidationError):
        Outer(inner={"x": "1"})  # raw data is validated strictly, never coerced


# ------------------------------------------------- bypass entry points closed
def test_model_copy_and_construct_are_disabled():
    a = Point(x=1)
    with pytest.raises(DomainConventionError, match="bypasses"):
        a.model_copy(update={"x": -1})
    with pytest.raises(DomainConventionError, match="bypasses"):
        Point.model_construct(x="not-an-int")


def test_object_setattr_bypass_is_not_part_of_the_contract_but_pickle_roundtrip_revalidates():
    a = Point(x=3, tags=("t",))
    assert pickle.loads(pickle.dumps(a)) == a


# ----------------------------------------------- conventions at class creation
def test_subclass_cannot_loosen_frozen_extra_or_strict():
    for key, bad in (("frozen", False), ("extra", "allow"), ("strict", False), ("allow_inf_nan", True)):
        with pytest.raises(DomainConventionError, match=key):
            type("Loose", (DomainModel,), {"__annotations__": {"x": int}, "model_config": {key: bad}})


def test_subclass_may_add_unrelated_config():
    class Named(DomainModel):
        model_config: ClassVar[ConfigDict] = ConfigDict(title="Named")
        x: int

    assert Named(x=1).x == 1


@pytest.mark.parametrize("annotation", [list[int], dict[str, int], set[int], list[list[int]],
                                        tuple[list[int], ...], int | list[int],
                                        Annotated[dict[str, int], Field(max_length=3)]])
def test_mutable_containers_in_annotations_are_rejected(annotation):
    with pytest.raises(DomainConventionError, match="mutable container"):
        type("Mutable", (DomainModel,), {"__annotations__": {"items": annotation}})


@pytest.mark.parametrize("annotation", [tuple[int, ...], frozenset[str], tuple[tuple[str, int], ...],
                                        int | None, Annotated[int, Field(ge=0)]])
def test_immutable_annotations_are_accepted(annotation):
    type("Fine", (DomainModel,), {"__annotations__": {"items": annotation}})


@pytest.mark.parametrize(
    "name",
    ["status", "state", "risk", "confidence", "score", "risk_score", "trust_score", "confidence_score",
     "assurance_score", "integrity_score", "safety_score", "quality_score", "score_total", "x_score"],
)
def test_generic_status_and_magic_score_fields_are_rejected(name):
    with pytest.raises(DomainConventionError, match="forbidden"):
        type("Bad", (DomainModel,), {"__annotations__": {name: int}})


@pytest.mark.parametrize(
    "name",
    ["claim_state", "verdict", "coverage_status", "scoreboard_name", "ascore_like", "disposition",
     "readiness", "freshness", "case_state_revision"],
)
def test_specific_dimension_names_are_allowed(name):
    type("Okay", (DomainModel,), {"__annotations__": {name: int}})


def test_convention_violations_are_registered_stable_errors():
    assert DomainConventionError.code == "DOMAIN_CONVENTION"


# ----------------------------------------------------------------- property
@given(st.integers(-(2**53 - 1), 2**53 - 1), st.lists(st.text(max_size=8), max_size=5))
def test_property_wire_round_trip_is_lossless_and_digest_stable(x, tags):
    p = Point(x=x, tags=tuple(tags))
    again = Point(**{**p.to_wire(), "tags": tuple(p.to_wire()["tags"])})
    assert again == p and again.canonical_digest() == p.canonical_digest()


def test_wire_form_keeps_none_so_absence_is_explicit():
    class Maybe(DomainModel):
        value: int | None = None

    assert Maybe().to_wire() == {"value": None}
    assert Maybe().canonical_bytes() == b'{"value":null}'
