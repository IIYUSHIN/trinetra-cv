"""IMP-0289: fixed protected headers and RFC 9052 section 4.4 Sig_structure."""
import cbor2
import pytest

from trinetra.tiir.cose_profile import (
    decode_sign1,
    encode_protected_headers,
    encode_sign1,
    signature_structure,
    validate_protected_headers,
)
from trinetra.tiir.errors import CoseHeaderError, CoseStructureError

KID = bytes(range(16))


def test_exact_protected_header_and_signature_structure_bytes():
    expected = bytes.fromhex(
        "a301270378226170706c69636174696f6e2f766e642e7472696e657472612e746969722b63626f720450"
    )
    # Content type has 34 ASCII bytes; exact fixture is independent of the encoder.
    assert encode_protected_headers(KID) == expected + KID
    assert validate_protected_headers(expected + KID) == KID
    protected = encode_protected_headers(KID)
    expected_tbs = b"\x84\x6aSignature1\x58" + bytes([len(protected)]) + protected + b"\x40\x41\x00"
    assert signature_structure(protected, b"\x00") == expected_tbs


@pytest.mark.parametrize("headers", [{1: -7, 3: "application/vnd.trinetra.tiir+cbor", 4: KID},
    {1: -8, 4: KID}, {1: -8, 3: "wrong", 4: KID},
    {1: -8, 3: "application/vnd.trinetra.tiir+cbor", 4: KID, 2: []},
    {1: -8, 3: "application/vnd.trinetra.tiir+cbor", 4: b"x" * 15}])
def test_headers_are_closed_and_algorithm_is_not_negotiated(headers):
    with pytest.raises(CoseHeaderError):
        validate_protected_headers(cbor2.dumps(headers, canonical=True))


def test_tagged_embedded_sign1_shape_and_exact_bytes():
    protected = encode_protected_headers(KID)
    signature = b"s" * 64
    wire = encode_sign1(protected, b"\x00", signature)
    assert wire == cbor2.dumps(cbor2.CBORTag(18, [protected, {}, b"\x00", signature]), canonical=True)
    parsed = decode_sign1(wire)
    assert parsed == (protected, b"\x00", signature)
    for changed in (wire[1:], b"\xd9\x00\x12" + wire[1:], wire + b"\x00"):
        with pytest.raises(CoseStructureError):
            decode_sign1(changed)


@pytest.mark.parametrize(("unprotected", "payload", "signature"), [
    ({4: KID}, b"x", b"s" * 64), ({}, None, b"s" * 64),
    ({}, b"x", b"s" * 63), ({}, b"x", b"s" * 65), ({}, b"x" * 49153, b"s" * 64),
])
def test_unprotected_headers_detachment_and_size_errors(unprotected, payload, signature):
    wire = cbor2.dumps(cbor2.CBORTag(18, [encode_protected_headers(KID), unprotected, payload, signature]))
    with pytest.raises(CoseStructureError):
        decode_sign1(wire)


def test_wire_length_and_wrong_types_are_typed():
    for value in (b"", "d2", b"\xd2" * 65537, b"\xd2\x80"):
        with pytest.raises(CoseStructureError):
            decode_sign1(value)
    for kid in (b"x" * 15, b"x" * 17, "x" * 16):
        with pytest.raises(CoseHeaderError):
            encode_protected_headers(kid)
