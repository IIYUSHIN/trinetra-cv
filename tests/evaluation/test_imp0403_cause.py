"""Synthetic cause remains independent of effect and policy."""

import pytest

from trinetra_eval.cause import CauseTruth
from trinetra_eval.hidden import private_document


def test_mixed_cause_keeps_its_components_private():
    record = CauseTruth(
        cause_class="MIXED_CAUSE",
        components=("OPERATIONAL_SHIFT", "SEEDED_ADVERSE_MUTATION"),
        generation_record_ref="synthetic-run",
    )
    doc = private_document(record)
    assert doc["cause_class"] == "MIXED_CAUSE"
    assert doc["components"] == ["OPERATIONAL_SHIFT", "SEEDED_ADVERSE_MUTATION"]
    assert "OPERATIONAL_SHIFT" not in repr(record)


@pytest.mark.parametrize("bad", ["MALICIOUS_ACTOR", "ACTIVE_EFFECT", "APPLIED", None])
def test_intent_effect_and_mutation_are_not_cause_labels(bad):
    with pytest.raises(ValueError):
        CauseTruth(cause_class=bad, components=(), generation_record_ref="synthetic-run")


def test_benign_cause_needs_no_adverse_claim():
    doc = private_document(
        CauseTruth(
            cause_class="SEEDED_BENIGN_CONDITION", components=(), generation_record_ref="synthetic-run"
        )
    )
    assert "expected_claim" not in doc
