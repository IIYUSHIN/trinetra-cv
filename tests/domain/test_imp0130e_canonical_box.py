"""IMP-0130E tests: the only box form in the Core is exact, finite, ordered and never clipped."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.geometry import MAX_IMAGE_DIMENSION, CanonicalBoxXYXY


def box(x1=1.0, y1=2.0, x2=3.0, y2=4.0):
    return CanonicalBoxXYXY(x1=x1, y1=y1, x2=x2, y2=y2)


def test_a_valid_box_keeps_its_values_exactly():
    b = box(0.25, 0.5, 100.125, 200.0625)
    assert (b.x1, b.y1, b.x2, b.y2) == (0.25, 0.5, 100.125, 200.0625)
    assert b.sort_key() == (0.25, 0.5, 100.125, 200.0625)


@pytest.mark.parametrize("bad", [1, True, Decimal("1.0"), "1.0", None, [1.0]])
@pytest.mark.parametrize("field", ["x1", "y1", "x2", "y2"])
def test_only_exact_floats_are_accepted_in_every_coordinate(bad, field):
    values = {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0} | {field: bad}
    with pytest.raises(ValidationError):
        CanonicalBoxXYXY(**values)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["x1", "y1", "x2", "y2"])
def test_non_finite_coordinates_are_rejected(bad, field):
    values = {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0} | {field: bad}
    with pytest.raises(ValidationError):
        CanonicalBoxXYXY(**values)


def test_numpy_style_float_subclasses_must_be_converted_by_the_adapter():
    class Scalar(float):
        pass

    with pytest.raises(ValidationError):
        box(x1=Scalar(1.0))


@pytest.mark.parametrize(
    "values",
    [
        {"x1": 3.0, "x2": 3.0},  # zero width
        {"x1": 4.0, "x2": 3.0},  # inverted x
        {"y1": 4.0, "y2": 4.0},  # zero height
        {"y1": 5.0, "y2": 4.0},  # inverted y
        {"x1": -0.5},  # left of the origin
        {"y1": -1e-9},  # above the origin
    ],
)
def test_degenerate_inverted_or_negative_boxes_are_rejected(values):
    with pytest.raises(ValidationError):
        box(**values)


def test_negative_zero_is_normalized_to_positive_zero():
    b = box(x1=-0.0, y1=-0.0)
    assert math.copysign(1.0, b.x1) == 1.0
    assert math.copysign(1.0, b.y1) == 1.0
    assert b.canonical_bytes() == box(x1=0.0, y1=0.0).canonical_bytes()


def test_fits_is_inclusive_at_the_image_edge_and_never_clips():
    b = box(0.0, 0.0, 640.0, 480.0)
    assert b.fits(640, 480)
    assert not b.fits(639, 480)
    assert not b.fits(640, 479)
    assert (b.x2, b.y2) == (640.0, 480.0)  # asking did not change it


def test_one_ulp_past_the_edge_does_not_fit():
    assert not box(0.0, 0.0, math.nextafter(640.0, math.inf), 480.0).fits(640, 480)
    assert box(0.0, 0.0, math.nextafter(640.0, 0.0), 480.0).fits(640, 480)


BAD_DIMENSIONS = [(0, 10), (10, 0), (-1, 10), (10.0, 10), (True, 10), (MAX_IMAGE_DIMENSION + 1, 10)]


@pytest.mark.parametrize("dims", BAD_DIMENSIONS)
def test_fits_refuses_invalid_image_dimensions(dims):
    with pytest.raises(ValueError, match="dimensions"):
        box().fits(*dims)


def test_the_box_is_immutable_closed_and_cannot_be_built_unvalidated():
    b = box()
    with pytest.raises(ValidationError):
        b.x1 = 0.0
    with pytest.raises(ValidationError):
        CanonicalBoxXYXY(x1=1.0, y1=2.0, x2=3.0, y2=4.0, x3=1.0)
    with pytest.raises(ValidationError):
        b.evolve(x2=0.5)


def test_wire_form_is_stable():
    assert box(1.5, 2.0, 3.0, 4.0).canonical_bytes() == b'{"x1":1.5,"x2":3,"y1":2,"y2":4}'


finite = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


@given(finite, finite, finite, finite)
def test_construction_agrees_with_the_stated_inequalities(x1, y1, x2, y2):
    valid = x1 < x2 and y1 < y2
    try:
        b = CanonicalBoxXYXY(x1=x1, y1=y1, x2=x2, y2=y2)
    except ValidationError:
        assert not valid
    else:
        assert valid
        assert b.to_wire() == {"x1": x1 + 0.0, "y1": y1 + 0.0, "x2": x2, "y2": y2}


def test_the_largest_exactly_representable_image_dimension_is_accepted():
    assert MAX_IMAGE_DIMENSION == 2**53 - 1
    assert box().fits(MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION)
