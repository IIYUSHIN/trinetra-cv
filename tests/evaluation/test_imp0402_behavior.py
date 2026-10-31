"""No threshold or effect is inferred from a mutation attempt."""

import pytest

from trinetra_eval.behavior import BehavioralTruth
from trinetra_eval.hidden import private_document


def data():
    return {
        "behavioural_state": "NO_MATERIAL_EFFECT",
        "criterion_ref": "sha256:" + "0" * 64,
        "observation_refs": ("sha256:" + "1" * 64,),
        "limitations": ("synthetic-only",),
    }


def test_failed_implantation_does_not_become_active_effect():
    record = BehavioralTruth(**data())
    assert private_document(record)["behavioural_state"] == "NO_MATERIAL_EFFECT"
    assert "NO_MATERIAL_EFFECT" not in repr(record)


@pytest.mark.parametrize(
    "change",
    [
        {"behavioural_state": "APPLIED"},
        {"criterion_ref": "unverified"},
        {"threshold": 0.5},
        {"observation_refs": ["sha256:" + "0" * 64]},
    ],
)
def test_no_mutation_state_threshold_or_malformed_evidence(change):
    with pytest.raises(ValueError):
        BehavioralTruth(**(data() | change))


def test_control_and_ambiguous_results_remain_distinct():
    for value in ("ACTIVE_EFFECT", "CONTROL_EXPLAINED_EFFECT", "AMBIGUOUS", "NOT_APPLICABLE"):
        assert (
            private_document(BehavioralTruth(**(data() | {"behavioural_state": value})))["behavioural_state"]
            == value
        )
