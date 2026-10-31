"""Truth value and assessability never become automatic production verdicts."""

import pytest

from trinetra_eval.claim_oracle import OracleClaimStateExpectation
from trinetra_eval.hidden import private_document


def data():
    return {
        "claim_definition_id": "C-MODEL",
        "scope_refs": ("model1",),
        "truth_value": "TRUE",
        "assessability": "UNASSESSABLE",
        "required_claim_states": (),
        "allowed_claim_states": ("NOT_ASSESSED",),
        "forbidden_claim_states": ("SUPPORTED",),
        "reason": "no-white-box-access",
        "supporting_truth_refs": ("truth1",),
    }


def test_true_but_unassessable_is_not_automatically_supported():
    doc = private_document(OracleClaimStateExpectation(**data()))
    assert doc["truth_value"] == "TRUE" and doc["allowed_claim_states"] == ["NOT_ASSESSED"]


@pytest.mark.parametrize(
    "change",
    [
        {"required_claim_states": ("SUPPORTED",)},
        {"forbidden_claim_states": ("NOT_ASSESSED",)},
        {"allowed_claim_states": ()},
        {"allowed_claim_states": ("TRUE",)},
        {"truth_value": "SUPPORTED"},
    ],
)
def test_inconsistent_and_cross_dimension_constraints_rejected(change):
    with pytest.raises(ValueError):
        OracleClaimStateExpectation(**(data() | change))


def test_multiple_acceptable_states_are_preserved_without_ranking():
    doc = private_document(
        OracleClaimStateExpectation(**(data() | {"allowed_claim_states": ("NOT_ASSESSED", "UNRESOLVED")}))
    )
    assert doc["allowed_claim_states"] == ["NOT_ASSESSED", "UNRESOLVED"]
