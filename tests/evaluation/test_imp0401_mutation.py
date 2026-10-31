"""Mutation facts never synthesize behavior or attacker intent."""

import json

import pytest

from trinetra_eval.hidden import private_document
from trinetra_eval.mutation import MutationTruth


def data():
    return {
        "mutation_state": "APPLIED",
        "operation": "synthetic-label-flip",
        "before_digest": "sha256:" + "0" * 64,
        "after_digest": "sha256:" + "1" * 64,
        "affected_entity_refs": ("sample1",),
    }


def test_exact_changes_stay_private_and_do_not_assert_behavior():
    record = MutationTruth(**data())
    doc = private_document(record)
    assert doc["mutation_state"] == "APPLIED"
    assert "behavioural_truth" not in doc and "cause" not in doc
    with pytest.raises(TypeError):
        json.dumps(record)


@pytest.mark.parametrize(
    "change",
    [
        {"mutation_state": "ACTIVE_EFFECT"},
        {"before_digest": "bad"},
        {"affected_entity_refs": ["sample1"]},
        {"intent": "malicious"},
    ],
)
def test_malformed_or_cross_dimension_facts_are_rejected(change):
    with pytest.raises(ValueError, match="private"):
        MutationTruth(**(data() | change))


def test_failed_mutation_and_noop_are_representable():
    for value in ("NONE", "PARTIALLY_APPLIED", "ATTEMPTED_FAILED", "NOT_APPLICABLE"):
        assert (
            private_document(MutationTruth(**(data() | {"mutation_state": value})))["mutation_state"] == value
        )
