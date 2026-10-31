"""IMP-0302: signed local clock observation is not freshness or trusted time."""
import pytest
from pydantic import ValidationError

from trinetra.tiir.deterministic_cbor import decode_cbor, encode_cbor
from trinetra.tiir.issued_at import IssuedAtFreshnessFields


@pytest.mark.parametrize("value", [0, 1, 1000, 2**53, 2**64 - 1])
def test_timestamp_is_exact_unsigned_milliseconds_not_a_trust_result(value):
    timestamp = IssuedAtFreshnessFields(issued_at_unix_ms=value)
    assert timestamp.to_cbor_value() == value
    assert not hasattr(timestamp, "fresh")


@pytest.mark.parametrize("value", [-1, 2**64, True, 1.0, "1000", None])
def test_timestamp_rejects_overflow_negative_values_and_coercion(value):
    with pytest.raises(ValidationError):
        IssuedAtFreshnessFields(issued_at_unix_ms=value)


def test_observation_is_immutable_and_preserves_milliseconds():
    timestamp = IssuedAtFreshnessFields(issued_at_unix_ms=1793448000123)
    assert decode_cbor(encode_cbor(timestamp.to_cbor_value())) == 1793448000123
    with pytest.raises(ValidationError):
        timestamp.issued_at_unix_ms = 0
