"""IMP-0130F tests: a Detection is a class index, a unit-interval score and a canonical box."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from trinetra.domain.base import SCORE_FIELD_EXEMPTIONS, DomainConventionError, DomainModel
from trinetra.domain.detection import MAX_CLASS_INDEX, Detection
from trinetra.domain.geometry import CanonicalBoxXYXY

BOX = CanonicalBoxXYXY(x1=1.0, y1=2.0, x2=3.0, y2=4.0)


def det(class_index=0, score=0.5, box=BOX):
    return Detection(class_index=class_index, score=score, box=box)


def test_a_detection_keeps_its_fields():
    d = det(7, 0.875)
    assert (d.class_index, d.score, d.box) == (7, 0.875, BOX)


@pytest.mark.parametrize("score", [0.0, 1.0, 5e-324, math.nextafter(1.0, 0.0)])
def test_scores_at_the_edges_of_the_unit_interval_are_valid(score):
    assert det(score=score).score == score


OUT_OF_RANGE = [-1e-12, math.nextafter(1.0, 2.0), 2.0, -0.5, math.nan, math.inf, -math.inf]


@pytest.mark.parametrize("score", OUT_OF_RANGE)
def test_scores_outside_the_unit_interval_or_non_finite_are_rejected(score):
    with pytest.raises(ValidationError):
        det(score=score)


@pytest.mark.parametrize("score", [1, 0, True, "0.5", None])
def test_a_score_must_be_an_exact_float(score):
    with pytest.raises(ValidationError):
        det(score=score)


def test_negative_zero_score_is_normalized():
    assert math.copysign(1.0, det(score=-0.0).score) == 1.0
    assert det(score=-0.0).canonical_bytes() == det(score=0.0).canonical_bytes()


@pytest.mark.parametrize("index", [-1, MAX_CLASS_INDEX + 1, 1.0, True, "1", None])
def test_class_index_is_a_strict_uint32(index):
    with pytest.raises(ValidationError):
        det(class_index=index)


def test_class_index_extremes_are_valid():
    assert det(class_index=0).class_index == 0
    assert det(class_index=MAX_CLASS_INDEX).class_index == MAX_CLASS_INDEX


def test_the_box_must_be_a_canonical_box_not_a_loose_mapping_of_ints():
    with pytest.raises(ValidationError):
        Detection(class_index=0, score=0.5, box={"x1": 1, "y1": 2, "x2": 3, "y2": 4})


def test_the_detection_is_closed_and_immutable():
    d = det()
    with pytest.raises(ValidationError):
        d.score = 0.1
    with pytest.raises(ValidationError):
        Detection(class_index=0, score=0.5, box=BOX, label="cat")


def test_sort_key_is_class_then_descending_score_then_box():
    assert det(2, 0.75).sort_key() == (2, -0.75, 1.0, 2.0, 3.0, 4.0)


def test_only_detection_score_is_exempt_from_the_magic_score_guard():
    assert {("trinetra.domain.detection.Detection", "score")} == SCORE_FIELD_EXEMPTIONS
    with pytest.raises(DomainConventionError):

        class Sneaky(DomainModel):
            score: float

    with pytest.raises(DomainConventionError):

        class Detection(DomainModel):  # same short name, different module: not exempt
            score: float

    with pytest.raises(DomainConventionError):

        class Other(DomainModel):
            trust_score: float
