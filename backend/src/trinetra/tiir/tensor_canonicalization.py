"""IMP-0292: byte-exact closed MVP tensor materialization, ERR-CL-023.

No cast, reshape, endian conversion or float rounding is performed. Typed views
must already have the declared shape and float32 storage; caller/producer owns
measurement consistency while a read-only view is copied once.
"""
import sys

from trinetra.tiir.errors import TiirSchemaError
from trinetra.tiir.tensor_digest import TensorDescriptor

NATIVE_BYTE_ORDER = sys.byteorder


def canonical_tensor_bytes(data: bytes | memoryview, descriptor: TensorDescriptor) -> bytes:
    if type(descriptor) is not TensorDescriptor:
        raise TiirSchemaError("canonical tensor requires the closed MVP descriptor")
    if type(data) is bytes:
        raw = data
    elif type(data) is memoryview:
        if not data.readonly or not data.c_contiguous:
            raise TiirSchemaError("tensor view must be read-only and C contiguous")
        if NATIVE_BYTE_ORDER != "little" or data.format not in ("f", "<f", "@f", "=f") or data.itemsize != 4:
            raise TiirSchemaError("tensor view must already contain little-endian float32")
        if data.shape != descriptor.shape:
            raise TiirSchemaError("tensor view shape cannot be silently reinterpreted")
        raw = data.tobytes()
    else:
        raise TiirSchemaError("tensor must be bytes or a supported typed memoryview")
    if len(raw) != descriptor.byte_length:
        raise TiirSchemaError("tensor byte count does not match descriptor")
    return raw
