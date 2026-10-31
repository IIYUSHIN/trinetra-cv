"""IMP-0289: fixed COSE_Sign1 shape and protected headers, without signing policy."""
from trinetra.tiir.deterministic_cbor import MAX_PAYLOAD_BYTES, MAX_RECEIPT_BYTES, decode_cbor, encode_cbor
from trinetra.tiir.errors import CoseHeaderError, CoseStructureError, TiirEncodingError

CONTENT_TYPE = "application/vnd.trinetra.tiir+cbor"
TAG_18 = b"\xd2"


def encode_protected_headers(kid: bytes) -> bytes:
    if type(kid) is not bytes or len(kid) != 16:
        raise CoseHeaderError()
    return encode_cbor({1: -8, 3: CONTENT_TYPE, 4: kid})


def validate_protected_headers(protected: bytes) -> bytes:
    try:
        headers = decode_cbor(protected)
    except TiirEncodingError as exc:
        raise CoseHeaderError() from exc
    if type(headers) is not dict or set(headers) != {1, 3, 4}:
        raise CoseHeaderError()
    kid = headers[4]
    if type(kid) is not bytes or encode_protected_headers(kid) != protected:
        raise CoseHeaderError()
    return kid


def signature_structure(protected: bytes, payload: bytes) -> bytes:
    """RFC 9052 Signature1 bytes with empty external_aad; no claim of trust."""
    validate_protected_headers(protected)
    if type(payload) is not bytes or len(payload) > MAX_PAYLOAD_BYTES:
        raise CoseStructureError()
    return encode_cbor(["Signature1", protected, b"", payload], max_bytes=MAX_RECEIPT_BYTES)


def encode_sign1(protected: bytes, payload: bytes, signature: bytes) -> bytes:
    validate_protected_headers(protected)
    if type(payload) is not bytes or len(payload) > MAX_PAYLOAD_BYTES:
        raise CoseStructureError()
    if type(signature) is not bytes or len(signature) != 64:
        raise CoseStructureError()
    try:
        return TAG_18 + encode_cbor([protected, {}, payload, signature], max_bytes=MAX_RECEIPT_BYTES - 1)
    except TiirEncodingError as exc:
        raise CoseStructureError() from exc


def decode_sign1(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Parse canonical transport only; payload schema/signature/trust are separate."""
    if type(data) is not bytes or len(data) > MAX_RECEIPT_BYTES or not data.startswith(TAG_18):
        raise CoseStructureError()
    try:
        fields = decode_cbor(data[1:], max_bytes=MAX_RECEIPT_BYTES - 1)
    except TiirEncodingError as exc:
        raise CoseStructureError() from exc
    if type(fields) is not list or len(fields) != 4:
        raise CoseStructureError()
    protected, unprotected, payload, signature = fields
    if type(unprotected) is not dict or unprotected:
        raise CoseStructureError()
    if type(protected) is not bytes or type(payload) is not bytes or type(signature) is not bytes:
        raise CoseStructureError()
    if encode_sign1(protected, payload, signature) != data:
        raise CoseStructureError()
    return protected, payload, signature
