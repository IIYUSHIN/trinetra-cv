"""IMP-0299: uint64 sequence, independent of the unresolved stream identity."""
import pytest
from pydantic import ValidationError

from trinetra.tiir.errors import ReceiptSequenceConflict
from trinetra.tiir.sequence import ReceiptSequenceNumber


@pytest.mark.parametrize("value", [1, 2, 2**53, 2**64 - 1])
def test_sequence_preserves_uint64_without_json_safe_truncation(value):
    number = ReceiptSequenceNumber(sequence=value)
    assert number.sequence == value
    assert number.to_cbor_value() == value


@pytest.mark.parametrize("value", [0, -1, 2**64, True, 1.0, "1", None])
def test_sequence_rejects_zero_overflow_and_coercion(value):
    with pytest.raises(ValidationError):
        ReceiptSequenceNumber(sequence=value)


def test_sequence_successor_does_not_mutate_and_never_wraps():
    first = ReceiptSequenceNumber(sequence=1)
    assert first.next().sequence == 2
    assert first.sequence == 1
    with pytest.raises(ReceiptSequenceConflict):
        ReceiptSequenceNumber(sequence=2**64 - 1).next()
    with pytest.raises(ValidationError):
        first.sequence = 0
