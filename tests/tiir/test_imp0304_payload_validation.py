"""IMP-0304: closed hostile-input boundary and actual ephemeral Ed25519 wire path."""

import copy

import cbor2
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given
from hypothesis import strategies as st
from payload_fixtures import sample_payload

from trinetra.domain.digests import DigestSha256
from trinetra.tiir.cose_profile import (
    decode_sign1,
    encode_protected_headers,
    encode_sign1,
    signature_structure,
)
from trinetra.tiir.deterministic_cbor import decode_cbor, encode_cbor
from trinetra.tiir.errors import TiirEncodingError, TiirError, TiirSchemaError, UnsupportedTiirVersion
from trinetra.tiir.key_identity import TrustedKeyIdentityVsKid
from trinetra.tiir.payload import EffectiveInputBinding
from trinetra.tiir.payload_validation import decode_payload, encode_payload
from trinetra.tiir.sequence import ReceiptSequenceNumber
from trinetra.tiir.tensor_digest import TensorDescriptor, TensorDigest


def test_roundtrip_keeps_non_v4_identifiers_and_full_uint64_values():
    payload = sample_payload(
        sequence=ReceiptSequenceNumber(sequence=2**64 - 1),
        previous_receipt_digest=DigestSha256.of(b"prev"),
        issued_at_unix_ms=2**64 - 1,
    )
    wire = encode_payload(payload)
    assert wire[:1] == b"\xb1"
    assert decode_payload(wire) == payload
    assert encode_payload(decode_payload(wire)) == wire
    assert not decode_payload(wire).receipt_id.is_uuid_v4


def test_tensor_roundtrip_preserves_identity_source_and_closed_metadata():
    tensor = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), bytes(12), source="RUNNER_REPORTED")
    effective = EffectiveInputBinding(observability_level="RUNTIME_REPORTED", tensor_digest=tensor)
    payload = sample_payload(effective_input=effective)
    assert decode_payload(encode_payload(payload)) == payload
    changed = payload.to_cbor_value()
    changed[11][2][7] = b"x" * 32
    with pytest.raises(TiirSchemaError):
        decode_payload(encode_cbor(changed))


@pytest.mark.parametrize("slot", [None, 10, 11, 12, 15, 16])
@pytest.mark.parametrize("change", ["extra", "missing"])
def test_unknown_and_missing_nested_or_top_level_keys_are_rejected(slot, change):
    value = copy.deepcopy(sample_payload().to_cbor_value())
    target = value if slot is None else value[slot]
    if change == "extra":
        target[23] = None
    else:
        del target[1]
    with pytest.raises(TiirSchemaError):
        decode_payload(encode_cbor(value))


@pytest.mark.parametrize(
    ("slot", "bad"),
    [
        (2, b"x" * 15),
        (3, "00000000-0000-0000-0000-000000000000"),
        (5, 0),
        (5, True),
        (6, b"x" * 32),
        (7, -1),
        (8, b"x" * 15),
        (9, b"x" * 16),
        (13, b"x" * 31),
        (14, "sha256:" + "0" * 64),
        (17, b"kid" * 4),
    ],
)
def test_malformed_scalar_bindings_and_genesis_do_not_get_coerced(slot, bad):
    value = sample_payload().to_cbor_value()
    value[slot] = bad
    with pytest.raises(TiirSchemaError):
        decode_payload(encode_cbor(value))


def test_version_failure_is_distinct_from_encoding_and_schema_failure():
    value = sample_payload().to_cbor_value()
    value[1] = 2
    with pytest.raises(UnsupportedTiirVersion):
        decode_payload(encode_cbor(value))
    value[1] = True
    with pytest.raises(TiirSchemaError):
        decode_payload(encode_cbor(value))
    wire = encode_payload(sample_payload())
    for malformed in (wire + b"\x00", bytes.fromhex("bf") + wire[1:] + b"\xff", b"\0" * 49153):
        with pytest.raises(TiirEncodingError):
            decode_payload(malformed)
    # A duplicate schema key must fail before dictionary conversion can erase it.
    with pytest.raises(TiirEncodingError):
        decode_payload(b"\xb2" + wire[1:] + b"\x01\x01")
    tagged_id = sample_payload().to_cbor_value()
    tagged_id[2] = cbor2.CBORTag(37, bytes(16))
    with pytest.raises(TiirEncodingError):
        decode_payload(cbor2.dumps(tagged_id, canonical=True))


def test_generated_key_cose_roundtrip_and_signature_valid_does_not_imply_expected_model():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    identity = TrustedKeyIdentityVsKid(raw_public_key=public.public_bytes_raw())
    payload = sample_payload(signing_key_fingerprint=identity.key_fingerprint)
    wire = encode_payload(payload)
    protected = encode_protected_headers(identity.kid)
    signature = private.sign(signature_structure(protected, wire))
    receipt = encode_sign1(protected, wire, signature)
    parsed_headers, parsed_payload, parsed_signature = decode_sign1(receipt)
    public.verify(parsed_signature, signature_structure(parsed_headers, parsed_payload))
    parsed = decode_payload(parsed_payload)
    assert parsed.model.model_content_digest != DigestSha256.of(b"independently expected model")
    altered = decode_cbor(parsed_payload)
    altered[12][2] = b"t" * 32
    with pytest.raises(InvalidSignature):
        public.verify(parsed_signature, signature_structure(parsed_headers, encode_cbor(altered)))


@given(st.binary(max_size=2048))
def test_arbitrary_bytes_raise_only_typed_protocol_failures(data):
    try:
        payload = decode_payload(data)
    except TiirError:
        return
    assert encode_payload(payload) == data


def test_tensor_map_is_closed_and_encoding_never_accepts_an_untyped_payload():
    tensor = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), bytes(12), source="RUNNER_REPORTED")
    payload = sample_payload(
        effective_input=EffectiveInputBinding(observability_level="RUNTIME_REPORTED", tensor_digest=tensor)
    )
    for extra in (True, False):
        value = payload.to_cbor_value()
        if extra:
            value[11][2][9] = None
        else:
            del value[11][2][1]
        with pytest.raises(TiirSchemaError):
            decode_payload(encode_cbor(value))
    with pytest.raises(TiirSchemaError):
        encode_payload(payload.to_cbor_value())


@pytest.mark.parametrize(
    ("slot", "key", "bad"),
    [
        (10, 3, -1),
        (12, 6, "HARDWARE_ATTESTED"),
        (15, 1, "latest"),
        (16, 5, "unknown_schema"),
        (16, 6, 10001),
        (16, 6, True),
    ],
)
def test_nested_binding_constraints_remain_enforced_after_wire_decode(slot, key, bad):
    value = sample_payload().to_cbor_value()
    value[slot][key] = bad
    with pytest.raises(TiirSchemaError):
        decode_payload(encode_cbor(value))
