"""No unavailable capability is silently upgraded to successful coverage."""

import pytest

from trinetra_eval.coverage_oracle import OracleCoverageExpectation
from trinetra_eval.hidden import private_document


def data():
    return {
        "coverage_obligation_id": "white-box-activation",
        "applicable": True,
        "capability_available": False,
        "reference_available": True,
        "expected_status": "UNAVAILABLE",
        "reason": "black-box-only",
    }


def test_unavailable_obligation_remains_distinct_from_resolution():
    doc = private_document(OracleCoverageExpectation(**data()))
    assert doc["expected_status"] == "UNAVAILABLE" and doc["capability_available"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"applicable": 1},
        {"capability_available": "false"},
        {"expected_status": "SUPPORTED"},
        {"extra": "secret"},
    ],
)
def test_non_boolean_and_cross_dimension_states_rejected(change):
    with pytest.raises(ValueError):
        OracleCoverageExpectation(**(data() | change))


def test_not_applicable_is_representable_without_fabricated_observations():
    doc = private_document(
        OracleCoverageExpectation(**(data() | {"applicable": False, "expected_status": "NOT_APPLICABLE"}))
    )
    assert doc["expected_status"] == "NOT_APPLICABLE"
