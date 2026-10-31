"""Key purpose substitution must fail before crypto is attempted."""

import pytest

from trinetra_eval.key_purposes import EvaluationKeyPurpose as P
from trinetra_eval.key_purposes import registered_key_purposes, require_purpose


def test_each_authority_accepts_only_itself():
    for p in P:
        require_purpose(p, p)
        for q in P:
            if p is not q:
                with pytest.raises(ValueError):
                    require_purpose(p, q)


@pytest.mark.parametrize("bad", ["EVALUATION_COMMITMENT_SIGNING", None, 1])
def test_untyped_purposes_cannot_sign(bad):
    with pytest.raises(ValueError):
        require_purpose(bad, P.EVALUATION_COMMITMENT_SIGNING)


def test_production_authority_names_are_absent():
    assert registered_key_purposes() == frozenset(
        {"EVALUATION_COMMITMENT_SIGNING", "BLIND_TRUTH_ENCRYPTION", "FINAL_TRUTH_ENCRYPTION"}
    )
