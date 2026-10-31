"""IMP-0285: provisional ERR-CL-019 fixtures, not independent standard vectors."""
import copy
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.base import DomainModel
from trinetra.domain.ids import OpaqueId
from trinetra.tiir.errors import TiirEncodingError, TiirSchemaError
from trinetra.tiir.identifiers import ReceiptId, StreamId

VECTORS = [
    (bytes(16), "00000000000040008000000000000000", "00000000-0000-4000-8000-000000000000"),
    (bytes(range(16)), "000102030405460788090a0b0c0d0e0f", "00010203-0405-4607-8809-0a0b0c0d0e0f"),
    (b"\xff" * 16, "ffffffffffff4fffbfffffffffffffff", "ffffffff-ffff-4fff-bfff-ffffffffffff"),
]


@pytest.mark.parametrize("kind", [ReceiptId, StreamId])
@pytest.mark.parametrize(("entropy", "hex_value", "text"), VECTORS)
def test_principal_builder_known_answers(kind, entropy, hex_value, text):
    calls = []
    def draw(size):
        calls.append(size)
        return entropy
    identity = kind.generate(draw)
    assert calls == [16]
    assert identity.raw == bytes.fromhex(hex_value)
    assert identity.to_text() == str(identity) == text
    assert identity.to_cbor() == bytes.fromhex("50" + hex_value)
    assert identity.is_uuid_v4
    assert kind.parse(text) == kind.from_cbor(identity.to_cbor()) == identity
    assert not isinstance(identity, OpaqueId)


@pytest.mark.parametrize("kind", [ReceiptId, StreamId])
@given(st.binary(min_size=16, max_size=16))
def test_decode_and_text_parse_accept_every_16_byte_value_without_version_gating(kind, raw):
    identity = kind(raw)
    assert kind.from_cbor(b"\x50" + raw) == identity
    assert kind.parse(identity.to_text()) == identity
    assert identity.is_uuid_v4 == (raw[6] >> 4 == 4 and raw[8] >> 6 == 2)
    assert identity.to_cbor_value() == raw


def test_non_v4_and_all_zero_are_valid_without_being_generation_outputs():
    zero = ReceiptId(bytes(16))
    assert not zero.is_uuid_v4
    assert ReceiptId.from_cbor(b"\x50" + bytes(16)) == zero
    assert ReceiptId.parse("00000000-0000-0000-0000-000000000000") == zero


@pytest.mark.parametrize("kind", [ReceiptId, StreamId])
@pytest.mark.parametrize("bad", [b"", b"x" * 15, b"x" * 17, bytearray(16), memoryview(bytes(16)),
                                 "0" * 16, 16, None])
def test_constructor_requires_exact_bytes_and_length(kind, bad):
    with pytest.raises(TiirSchemaError):
        kind(bad)


@pytest.mark.parametrize("bad", ["FFFFFFFF-FFFF-4FFF-BFFF-FFFFFFFFFFFF", "00000000000040008000000000000000",
    "{00000000-0000-4000-8000-000000000000}", "urn:uuid:00000000-0000-4000-8000-000000000000",
    " 00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000000\n",
    "00000000-0000-4000-8000-00000000000g", "00000000-0000-4000-8000-000000000000-", bytes(16)])
def test_text_spelling_is_strict_and_never_normalized(bad):
    with pytest.raises(TiirSchemaError):
        ReceiptId.parse(bad)


@pytest.mark.parametrize("wire", [b"\x4f" + bytes(15), b"\x51" + bytes(17), b"\x40",
    b"\xd8\x25\x50" + bytes(16), b"\x5f\x50" + bytes(16) + b"\xff",
    b"\x81\x50" + bytes(16), b"\x58\x10" + bytes(16), b"\x50" + bytes(16) + b"\x00",
    b"\x78\x24" + b"00000000-0000-4000-8000-000000000000"])
def test_signed_id_cbor_has_no_tags_text_arrays_or_alternate_encodings(wire):
    with pytest.raises((TiirEncodingError, TiirSchemaError)):
        ReceiptId.from_cbor(wire)


def test_strong_equality_hashing_and_ordering():
    low, high = ReceiptId(bytes(16)), ReceiptId(b"\xff" * 16)
    same = ReceiptId(bytes(16))
    stream = StreamId(bytes(16))
    assert low == same and hash(low) == hash(same)
    assert low != stream and stream != low
    assert low != bytes(16) and bytes(16) != low
    assert len({low, same, stream}) == 2
    assert low < high and low <= same and high > low and high >= low
    for other in (stream, bytes(16)):
        with pytest.raises(TypeError):
            _ = low < other
        with pytest.raises(TypeError):
            _ = low >= other
    assert copy.copy(low) == copy.deepcopy(low) == pickle.loads(pickle.dumps(low)) == low
    with pytest.raises(AttributeError):
        low.raw = b"x" * 16
    with pytest.raises(AttributeError):
        low._raw = b"x" * 16
    with pytest.raises(AttributeError):
        del low._raw
    with pytest.raises(AttributeError):
        low.__init__(b"x" * 16)


class IdentitySlots(DomainModel):
    receipt_id: ReceiptId
    stream_id: StreamId


def test_domain_slots_reject_cross_type_instances_and_use_canonical_text_only_in_json():
    receipt, stream = ReceiptId(bytes(16)), StreamId(bytes(16))
    slots = IdentitySlots(receipt_id=receipt, stream_id=stream)
    assert slots.to_wire() == {"receipt_id": str(receipt), "stream_id": str(stream)}
    assert IdentitySlots.model_validate_json(slots.model_dump_json()) == slots
    with pytest.raises(ValidationError):
        IdentitySlots(receipt_id=stream, stream_id=receipt)
    with pytest.raises(ValidationError):
        IdentitySlots(receipt_id=bytes(16), stream_id=stream)


@pytest.mark.parametrize("bad", [b"x" * 15, b"x" * 17, bytearray(16), "x" * 16])
def test_entropy_draw_is_validated_before_masking(bad):
    with pytest.raises(TiirSchemaError):
        ReceiptId.generate(lambda _size: bad)


def test_default_entropy_uses_os_cryptographic_source(monkeypatch):
    calls = []
    def draw(size):
        calls.append(size)
        return bytes(16)
    monkeypatch.setattr("trinetra.tiir.identifiers.os.urandom", draw)
    assert ReceiptId.generate().to_text() == VECTORS[0][2]
    assert calls == [16]
