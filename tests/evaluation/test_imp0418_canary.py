"""Canary metadata stays private; only synthetic tokens are used here."""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra_eval.canary import LeakageCanary


@given(st.binary(min_size=16, max_size=16))
def test_exact_entropy_becomes_private_ascii_marker(raw):
    canary = LeakageCanary(lambda _n: raw)
    assert canary.evaluator_private_token() == b"TF-CANARY-" + raw.hex().encode()
    assert raw.hex() not in repr(canary)
    for export in (json.dumps, vars, lambda c: c.__getstate__()):
        with pytest.raises(TypeError):
            export(canary)


@pytest.mark.parametrize("size", [0, 1, 15, 17, 32])
def test_malformed_entropy_is_rejected(size):
    with pytest.raises(ValueError):
        LeakageCanary(lambda _n: bytes(size))


def test_generated_tokens_are_distinct_and_immutable():
    assert len({LeakageCanary().evaluator_private_token() for _ in range(100)}) == 100
    with pytest.raises(AttributeError):
        LeakageCanary().answer = "truth"
