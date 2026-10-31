"""IMP-0300: chain binds exact preceding tagged COSE bytes, not payload or key ID."""
import hashlib

import cbor2
import pytest

from trinetra.tiir.errors import CoseStructureError, ReceiptBindingError
from trinetra.tiir.previous import PreviousReceiptDigest
from trinetra.tiir.sequence import ReceiptSequenceNumber


def receipt(signature=b"s" * 64):
    protected = cbor2.dumps({1: -8, 3: "application/vnd.trinetra.tiir+cbor", 4: b"k" * 16}, canonical=True)
    return cbor2.dumps(cbor2.CBORTag(18, [protected, {}, b"\x00", signature]), canonical=True)


def test_genesis_has_no_predecessor_and_later_sequences_require_one():
    first = ReceiptSequenceNumber(sequence=1)
    assert PreviousReceiptDigest.from_previous_receipt(first).to_cbor_value() is None
    with pytest.raises(ReceiptBindingError):
        PreviousReceiptDigest.from_previous_receipt(first, receipt())
    with pytest.raises(ReceiptBindingError):
        PreviousReceiptDigest.from_previous_receipt(ReceiptSequenceNumber(sequence=2))


def test_previous_digest_covers_exact_tagged_envelope_and_signature():
    wire = receipt()
    binding = PreviousReceiptDigest.from_previous_receipt(ReceiptSequenceNumber(sequence=2), wire)
    assert binding.to_cbor_value() == hashlib.sha256(wire).digest()
    other = PreviousReceiptDigest.from_previous_receipt(ReceiptSequenceNumber(sequence=2), receipt(b"t" * 64))
    assert other.to_cbor_value() != binding.to_cbor_value()
    assert binding.to_cbor_value() != hashlib.sha256(b"\x00").digest()


@pytest.mark.parametrize("bad", [b"", b"\x00", b"\xd2\x80", receipt()[1:], receipt() + b"x"])
def test_arbitrary_or_noncanonical_bytes_are_not_a_receipt_substrate(bad):
    with pytest.raises(CoseStructureError):
        PreviousReceiptDigest.from_previous_receipt(ReceiptSequenceNumber(sequence=2), bad)
