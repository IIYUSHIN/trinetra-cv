"""Benign observations can allow more than one disposition; no forced quarantine."""

import pytest

from trinetra_eval.hidden import private_document
from trinetra_eval.policy_oracle import OraclePolicyExpectation


def data():
    return {
        "decision_scope": ("sample1",),
        "policy_profile_digest": "sha256:" + "0" * 64,
        "allowed_dispositions": ("ACCEPT", "REVIEW"),
        "forbidden_dispositions": ("QUARANTINE",),
        "triggered_rule_ids": (),
        "reason_codes": ("benign-control",),
    }


def test_profile_bound_multiple_allowed_dispositions_remain_private():
    record = OraclePolicyExpectation(**data())
    assert private_document(record)["allowed_dispositions"] == ["ACCEPT", "REVIEW"]
    assert "QUARANTINE" not in repr(record)


@pytest.mark.parametrize(
    "change",
    [
        {"allowed_dispositions": ()},
        {"forbidden_dispositions": ("REVIEW",)},
        {"policy_profile_digest": "missing"},
        {"allowed_dispositions": ("SUPPORTED",)},
    ],
)
def test_inconsistent_or_unbound_policy_expectations_rejected(change):
    with pytest.raises(ValueError):
        OraclePolicyExpectation(**(data() | change))


def test_no_attack_intent_or_claim_state_is_inferred():
    doc = private_document(OraclePolicyExpectation(**data()))
    assert "cause" not in doc and "claim_state" not in doc
