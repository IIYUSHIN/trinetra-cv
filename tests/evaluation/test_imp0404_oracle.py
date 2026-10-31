"""Oracle binds capability and version without producing candidate conclusions."""

import json

import pytest

from trinetra_eval.hidden import private_document
from trinetra_eval.oracle import AssuranceOracle


def data():
    return {
        "schema_version": 1,
        "scenario_id": "scn_" + "a" * 32,
        "oracle_version": "synthetic-v1",
        "capability_profile_ref": "sha256:" + "0" * 64,
        "required_claim_constraints": (),
        "expected_coverage": (),
        "expected_policy": "sha256:" + "1" * 64,
        "required_limitations": ("no-intent-attribution",),
        "required_abstentions": (),
        "required_scope_refs": (),
        "forbidden_overclaims": ("malicious-actor",),
    }


def test_oracle_keeps_limitations_and_forbidden_claims_hidden():
    record = AssuranceOracle(**data())
    assert private_document(record)["forbidden_overclaims"] == ["malicious-actor"]
    assert "malicious-actor" not in repr(record)
    with pytest.raises(TypeError):
        json.dumps(record)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"expected_policy": "QUARANTINE"},
        {"risk_score": 1.0},
        {"required_claim_constraints": []},
    ],
)
def test_unbound_versions_policy_and_generic_scores_rejected(change):
    with pytest.raises(ValueError):
        AssuranceOracle(**(data() | change))


def test_capability_profile_is_required():
    fields = data()
    del fields["capability_profile_ref"]
    with pytest.raises(ValueError):
        AssuranceOracle(**fields)
