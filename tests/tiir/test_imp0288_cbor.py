"""IMP-0288: RFC Appendix A bytes and adversarial TIIR-CBOR-v1 inputs.

Exact scalar/array vectors from RFC 8949 Appendix A (also RFC 7049),
independently retrieved at github.com/cbor/test-vectors/blob/master/appendix_a.json.
"""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.tiir.deterministic_cbor import decode_cbor, encode_cbor
from trinetra.tiir.errors import TiirEncodingError


@pytest.mark.parametrize(("value", "wire"), [
    (0, "00"), (23, "17"), (24, "1818"), (1000, "1903e8"), (-1, "20"),
    (-1000, "3903e7"), (False, "f4"), (True, "f5"), (None, "f6"),
    (b"\x01\x02\x03\x04", "4401020304"), ("IETF", "6449455446"),
    ([1, 2, 3], "83010203"), ({1: 2, 3: 4}, "a201020304"),
    (2**64 - 1, "1bffffffffffffffff"), (-(2**64), "3bffffffffffffffff"),
])
def test_standard_known_answer_bytes(value, wire):
    expected = bytes.fromhex(wire)
    assert encode_cbor(value) == expected
    assert decode_cbor(expected) == value


@pytest.mark.parametrize("wire", [
    "a201010102", "a201010101", "9f01ff", "bf0101ff", "5f4101ff", "7f6161ff",
    "1800", "a202000100", "00ff", "c24101", "d8184100", "f7", "f90000",
    "fa00000000", "fb0000000000000000", "a1616100", "a10000", "a1181800",
    "61ff", "81", "ff", "a1f501", "d81c81d81d00",
])
def test_hostile_and_noncanonical_bytes_fail_closed(wire):
    with pytest.raises(TiirEncodingError):
        decode_cbor(bytes.fromhex(wire))


@pytest.mark.parametrize("value", [1.0, float("nan"), 2**64, -(2**64)-1,
                                      {True: 1}, {0: 1}, {24: 1}, {"1": 1}, {1, 2}])
def test_disallowed_types_and_integer_limits(value):
    with pytest.raises(TiirEncodingError):
        encode_cbor(value)


def test_resource_limits_and_deep_or_cyclic_inputs_are_typed():
    wire = encode_cbor(b"x" * 49149)
    assert len(wire) == 49152
    assert decode_cbor(wire) == b"x" * 49149
    for data in (wire + b"\0", b"\x81" * 1000 + b"\0", "00", bytearray(b"\0")):
        with pytest.raises(TiirEncodingError):
            decode_cbor(data)
    with pytest.raises(TiirEncodingError):
        encode_cbor(b"x" * 49150)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(TiirEncodingError):
        encode_cbor(cyclic)


values = st.recursive(st.none() | st.booleans() | st.integers(-(2**64), 2**64-1)
                      | st.binary(max_size=32) | st.text(max_size=32),
                      lambda child: st.lists(child, max_size=4)
                      | st.dictionaries(st.integers(1, 23), child, max_size=4), max_leaves=20)


@given(values)
def test_restricted_data_roundtrips_and_key_insertion_order_is_irrelevant(value):
    wire = encode_cbor(value)
    assert decode_cbor(wire) == value
    if isinstance(value, dict):
        assert encode_cbor(dict(reversed(list(value.items())))) == wire


@given(st.binary(max_size=256))
def test_arbitrary_bytes_never_escape_as_library_errors(wire):
    try:
        value = decode_cbor(wire)
    except TiirEncodingError:
        return
    assert encode_cbor(value) == wire


def test_fixed_unsorted_map_has_the_rfc_deterministic_order():
    assert encode_cbor({3: 4, 1: 2}) == bytes.fromhex("a201020304")
