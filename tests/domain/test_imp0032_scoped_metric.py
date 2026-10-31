"""IMP-0032 tests: ScopedMetric value object."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trinetra.domain.base import DomainConventionError, DomainModel
from trinetra.domain.entity import EntityRef, EntityType
from trinetra.domain.limitations import LimitationId
from trinetra.domain.metrics import MetricId
from trinetra.domain.references import ReferenceId
from trinetra.domain.scoped_metrics import MAX_LIMITATIONS, MetricUnit, ScopedMetric

SCOPE = (EntityRef(entity_type=EntityType("dataset"), entity_id="asset_1", display_label="Dataset 1"),)


def make(**over):
    base = {"metric_id": MetricId.generate(), "label": "Mean IoU", "value": 0.5, "unit": MetricUnit("ratio"),
            "method_ref": ReferenceId.generate(), "scope_refs": SCOPE}
    return ScopedMetric(**{**base, **over})


def test_minimal_metric_has_optionals_absent_and_roundtrips():
    m = make()
    wire = m.to_wire()
    assert set(wire) == {"metric_id", "label", "value", "unit", "method_ref", "scope_refs",
                         "reference_ref", "calibration_ref", "interpretation_label", "limitations"}
    assert wire["reference_ref"] is None and wire["limitations"] == []
    assert m.canonical_digest() == make(metric_id=m.metric_id, method_ref=m.method_ref).canonical_digest()


def test_full_metric_accepts_all_optionals():
    lims = (LimitationId.generate(), LimitationId.generate())
    m = make(reference_ref=ReferenceId.generate(), calibration_ref=ReferenceId.generate(),
             interpretation_label="Within the calibrated range", limitations=lims)
    assert m.limitations == lims and m.interpretation_label


def test_int_stays_int_and_float_stays_float():
    assert type(make(value=3).value) is int
    assert type(make(value=3.0).value) is float
    assert make(value=-0.0).value == 0.0


BAD_VALUES = [float("nan"), float("inf"), float("-inf"), True, False, "0.5", None, 2**53, -(2**53)]


@pytest.mark.parametrize("bad", BAD_VALUES)
def test_value_rejects_non_numbers_non_finite_and_huge_ints(bad):
    with pytest.raises(ValidationError):
        make(value=bad)


def test_value_boundary_ints_accepted():
    assert make(value=2**53 - 1).value == 2**53 - 1


def test_scope_required_and_unit_is_token():
    with pytest.raises(ValidationError):
        make(scope_refs=())
    with pytest.raises(ValidationError):
        make(unit="Not A Token")


def test_limitations_unique_bounded_and_typed():
    one = LimitationId.generate()
    with pytest.raises(ValidationError, match="duplicates"):
        make(limitations=(one, one))
    full = make(limitations=tuple(LimitationId.generate() for _ in range(MAX_LIMITATIONS)))
    assert len(full.limitations) == 64
    with pytest.raises(ValidationError):
        make(limitations=tuple(LimitationId.generate() for _ in range(MAX_LIMITATIONS + 1)))
    with pytest.raises(ValidationError):
        make(limitations=(ReferenceId.generate(),))


def test_refs_must_be_reference_ids():
    with pytest.raises(ValidationError):
        make(method_ref=MetricId.generate())
    for field in ("reference_ref", "calibration_ref"):
        with pytest.raises(ValidationError):
            make(**{field: str(MetricId.generate())})
        with pytest.raises(ValidationError):
            make(**{field: "ref_not-hex"})


EXTRAS = ["trust_score", "score", "risk", "confidence", "status", "verdict", "passed"]


@pytest.mark.parametrize("extra", EXTRAS)
def test_no_score_trust_status_or_verdict_can_exist(extra):
    with pytest.raises(ValidationError):
        make(**{extra: 1})


@pytest.mark.parametrize("name", ["trust_score", "risk_score", "confidence", "status", "state"])
def test_a_subclass_cannot_add_a_magic_score_field(name):
    with pytest.raises(DomainConventionError):
        type("Sneaky", (DomainModel,), {"__annotations__": {name: int}})


def test_metric_id_prefix_and_label_rules():
    assert str(make().metric_id).startswith("metric_")
    for bad in ["", " x", "x\n"]:
        with pytest.raises(ValidationError):
            make(label=bad)
    with pytest.raises(ValidationError):
        make(interpretation_label="")
