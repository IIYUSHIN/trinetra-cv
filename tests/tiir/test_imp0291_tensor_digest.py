"""IMP-0291: exact bytes plus JCS descriptor; project vector cross-checked with Node."""
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.digests import DigestSha256
from trinetra.tiir.errors import TiirSchemaError
from trinetra.tiir.tensor_digest import TensorDescriptor, TensorDigest


def test_independent_node_known_answer_and_no_source_to_trust_shortcut():
    descriptor = TensorDescriptor(shape=(1, 3, 1, 1))
    value = TensorDigest.bind(descriptor, bytes(12), source="SUPERVISOR_CONSTRUCTED")
    assert value.tensor_bytes_sha256.hex == "15ec7bf0b50732b49f8228e07d24365338f9e3ab994b00af08e5a3bffe55fd8b"
    assert value.tensor_identity_digest.hex == (
        "3d58ec408a2d1c44177f2474b278b5c7dd76c18bb538b2d4042a134b65d2ad74"
    )
    reported = TensorDigest.bind(descriptor, bytes(12), source="RUNNER_REPORTED")
    assert value.tensor_identity_digest == reported.tensor_identity_digest
    assert reported.source == "RUNNER_REPORTED" and not hasattr(reported, "trusted")


@given(st.binary(min_size=12, max_size=12))
def test_all_byte_bits_are_hashed_without_float_conversion(data):
    value = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), data, source="RUNNER_REPORTED")
    assert value.tensor_bytes_sha256.raw == hashlib.sha256(data).digest()


def test_shape_changes_identity_even_when_bytes_are_identical():
    a = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 2)), bytes(24), source="RUNNER_REPORTED")
    b = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 2, 1)), bytes(24), source="RUNNER_REPORTED")
    assert a.tensor_bytes_sha256 == b.tensor_bytes_sha256
    assert a.tensor_identity_digest != b.tensor_identity_digest
    with pytest.raises(TiirSchemaError):
        a.evolve(tensor_bytes_sha256=DigestSha256.of(b"tampered"))
    with pytest.raises(TiirSchemaError):
        a.evolve(tensor_identity_digest=DigestSha256.of(b"tampered"))


@pytest.mark.parametrize("bad", [b"", bytes(11), bytes(13), bytearray(12), "x" * 12])
def test_wrong_size_and_nonbytes_are_not_silently_transformed(bad):
    with pytest.raises(TiirSchemaError):
        TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), bad, source="RUNNER_REPORTED")


@pytest.mark.parametrize("shape", [(0, 3, 1, 1), (1, 1, 1, 1), (2, 3, 1, 1), (1, 3, True, 1),
                                   (1, 3, 1), (1, 3, -1, 1), (1, 3, 1.0, 1)])
def test_shape_is_closed_strict_and_never_reshaped(shape):
    with pytest.raises((ValidationError, TiirSchemaError)):
        TensorDescriptor(shape=shape)


def test_digest_is_immutable_closed_and_source_is_required():
    value = TensorDigest.bind(TensorDescriptor(shape=(1, 3, 1, 1)), bytes(12), source="RUNNER_REPORTED")
    with pytest.raises(ValidationError):
        value.source = "SUPERVISOR_CONSTRUCTED"
    with pytest.raises(ValidationError):
        value.evolve(source="UNKNOWN")
