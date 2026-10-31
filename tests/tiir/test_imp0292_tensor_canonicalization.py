"""IMP-0292: preserve bits and reject unsupported interpretation/storage."""
import struct

import pytest
from pydantic import ValidationError

from trinetra.tiir.errors import TiirSchemaError
from trinetra.tiir.tensor_canonicalization import canonical_tensor_bytes
from trinetra.tiir.tensor_digest import TensorDescriptor


def test_raw_bits_and_typed_view_are_preserved_including_negative_zero_and_nan():
    data = bytes.fromhex("000000800000c07f0000807f")
    descriptor = TensorDescriptor(shape=(1, 3, 1, 1))
    view = memoryview(data).cast("f", shape=[1, 3, 1, 1])
    assert canonical_tensor_bytes(data, descriptor) == data
    assert canonical_tensor_bytes(view, descriptor) == data


@pytest.mark.parametrize(("field", "value"), [("dtype", "FLOAT64"), ("dtype", "float32"),
    ("layout", "NHWC"), ("layout", "nchw"), ("byte_order", "BIG_ENDIAN"), ("schema_version", True)])
def test_profile_vocabulary_is_closed_not_coerced(field, value):
    with pytest.raises(ValidationError):
        TensorDescriptor(**({"shape": (1, 3, 1, 1)} | {field: value}))


def test_no_silent_reshape_of_an_equally_sized_view():
    descriptor = TensorDescriptor(shape=(1, 3, 1, 2))
    view = memoryview(bytes(24)).cast("f", shape=[1, 3, 2, 1])
    with pytest.raises(TiirSchemaError):
        canonical_tensor_bytes(view, descriptor)


def test_wrong_dtype_mutable_and_strided_views_fail():
    descriptor = TensorDescriptor(shape=(1, 3, 1, 1))
    values = [memoryview(bytes(12)).cast("i", shape=[1, 3, 1, 1]),
              memoryview(bytearray(12)).cast("f", shape=[1, 3, 1, 1]),
              memoryview(bytes(24)).cast("f")[::2], bytearray(12), b"short"]
    for value in values:
        with pytest.raises(TiirSchemaError):
            canonical_tensor_bytes(value, descriptor)


def test_native_endian_support_is_explicit(monkeypatch):
    descriptor = TensorDescriptor(shape=(1, 3, 1, 1))
    monkeypatch.setattr("trinetra.tiir.tensor_canonicalization.NATIVE_BYTE_ORDER", "big")
    with pytest.raises(TiirSchemaError):
        canonical_tensor_bytes(memoryview(struct.pack("<fff", 1.0, 2.0, 3.0)).cast("f", shape=[1, 3, 1, 1]),
                               descriptor)
