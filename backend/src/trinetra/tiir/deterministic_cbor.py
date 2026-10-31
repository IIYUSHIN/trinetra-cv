"""IMP-0288: bounded TIIR-CBOR-v1 using cbor2, never home-grown key sorting.

Small positive map keys make RFC 8949 core deterministic and length-first
ordering identical. Schema validation remains a separate caller responsibility.
"""
import io

import cbor2

from trinetra.tiir.errors import TiirEncodingError

MAX_PAYLOAD_BYTES = 49152
MAX_RECEIPT_BYTES = 65536


def _limit(max_bytes: int) -> None:
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECEIPT_BYTES:
        raise TiirEncodingError("invalid CBOR byte limit")


def _validate(value: object, max_bytes: int) -> None:
    pending = [value]
    minimum_size = 0
    while pending:
        item = pending.pop()
        minimum_size += 1
        if item is None or type(item) is bool:
            pass
        elif type(item) is int:
            if not -(2**64) <= item < 2**64:
                raise TiirEncodingError("CBOR integer outside the 64-bit profile")
        elif type(item) is bytes:
            minimum_size += len(item)
        elif type(item) is str:
            if len(item) > max_bytes:
                raise TiirEncodingError("CBOR text exceeds byte limit")
            minimum_size += len(item.encode("utf-8"))
        elif isinstance(item, list | tuple):
            if len(item) > max_bytes:
                raise TiirEncodingError("CBOR array exceeds byte limit")
            pending.extend(item)
        elif type(item) is dict:
            if any(type(key) is not int or not 1 <= key <= 23 for key in item):
                raise TiirEncodingError("TIIR map keys must be integers 1..23")
            minimum_size += len(item)
            pending.extend(item.values())
        else:
            raise TiirEncodingError("unsupported TIIR CBOR value")
        if minimum_size > max_bytes:
            raise TiirEncodingError("CBOR resource limit exceeded")


def encode_cbor(value: object, *, max_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    """Encode restricted values with shortest, definite, deterministic encoding."""
    _limit(max_bytes)
    try:
        _validate(value, max_bytes)
        encoded = cbor2.dumps(value, canonical=True)
    except (cbor2.CBOREncodeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise TiirEncodingError("invalid TIIR CBOR value") from exc
    if len(encoded) > max_bytes:
        raise TiirEncodingError("CBOR resource limit exceeded")
    return encoded


def decode_cbor(data: bytes, *, max_bytes: int = MAX_PAYLOAD_BYTES) -> object:
    """Decode exactly one canonical item; reject duplicates before they are lost."""
    _limit(max_bytes)
    if type(data) is not bytes or not data or len(data) > max_bytes:
        raise TiirEncodingError("CBOR input must be bounded nonempty bytes")
    stream = io.BytesIO(data)
    try:
        value = cbor2.CBORDecoder(stream, allow_duplicate_keys=False, allow_indefinite=False).decode()
        if stream.tell() != len(data):
            raise TiirEncodingError("trailing CBOR bytes")
        canonical = encode_cbor(value, max_bytes=max_bytes)
    except (cbor2.CBORDecodeError, UnicodeError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise TiirEncodingError("invalid TIIR CBOR encoding") from exc
    if canonical != data:
        raise TiirEncodingError("non-deterministic TIIR CBOR encoding")
    return value
