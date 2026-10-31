"""IMP-0305: closed signer request, independent of ReceiptId/StreamId schemas."""
import pytest
from pydantic import ValidationError

from trinetra.tiir.signer_request import TiirSignerRequestSchema


def request(**changes):
    fields = {"protocol_version": 1, "key_fingerprint": b"f" * 32,
              "purpose": "TIIR_SIGNING", "to_be_signed": b"exact signature structure"}
    return TiirSignerRequestSchema(**(fields | changes))


def test_exact_message_full_fingerprint_and_fixed_purpose():
    value = request()
    assert value.protocol_version == 1
    assert value.key_fingerprint == b"f" * 32
    assert value.purpose == "TIIR_SIGNING"
    assert value.to_be_signed == b"exact signature structure"
    with pytest.raises(ValidationError):
        value.purpose = "CAPSULE_SIGNING"


@pytest.mark.parametrize(("field", "value"), [
    ("protocol_version", 2), ("protocol_version", True), ("protocol_version", 1.0),
    ("key_fingerprint", b"f" * 16), ("key_fingerprint", b"f" * 31),
    ("key_fingerprint", b"f" * 33), ("key_fingerprint", "f" * 32),
    ("purpose", "CAPSULE_SIGNING"), ("purpose", "RELEASE_SIGNING"),
    ("purpose", "EVALUATION_COMMITMENT"), ("to_be_signed", "text"),
    ("to_be_signed", b"x" * 65537),
])
def test_key_hints_algorithm_negotiation_and_unbounded_messages_are_rejected(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize("field", ["key_path", "algorithm", "curve", "receipt_id", "stream_id", "kid"])
def test_no_path_or_identity_or_algorithm_controls_can_be_smuggled_in(field):
    with pytest.raises(ValidationError):
        request(**{field: "untrusted"})


def test_message_bound_is_a_precondition_not_an_ipc_frame_size_claim():
    # IMP-0306 must bound the COMPLETE encoded frame to 64 KiB before decoding.
    # The schema neither chooses a CBOR field map nor claims to validate IPC bytes.
    assert len(request(to_be_signed=b"x" * 65536).to_be_signed) == 65536
    assert request(to_be_signed=b"").to_be_signed == b""
