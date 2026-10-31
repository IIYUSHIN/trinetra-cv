"""IMP-0290: exact content bytes, not filenames or describing records."""
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.assets import AssetId
from trinetra.tiir.errors import TiirSchemaError
from trinetra.tiir.original_input import OriginalInputBinding


def asset(byte=1):
    return AssetId.from_entropy(lambda size: bytes([byte]) * size)


def test_sha256_known_answer_for_exact_input_bytes():
    binding = OriginalInputBinding.from_bytes(asset(), b"abc")
    assert binding.content_digest.hex == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert binding.byte_size == 3 and binding.asset_id == asset()
    assert OriginalInputBinding.from_bytes(asset(), b"").byte_size == 0
    assert OriginalInputBinding.from_bytes(asset(), b"abc\n").content_digest != binding.content_digest


@given(st.binary(max_size=4096))
def test_entire_input_and_length_are_bound(data):
    binding = OriginalInputBinding.from_bytes(asset(), data)
    assert binding.content_digest.raw == hashlib.sha256(data).digest()
    assert binding.byte_size == len(data)
    same_content = OriginalInputBinding.from_bytes(asset(2), data)
    assert same_content.content_digest == binding.content_digest
    assert same_content.asset_id != binding.asset_id


@pytest.mark.parametrize("bad", [None, "abc", bytearray(b"abc"), 3])
def test_hashing_rejects_nonexact_byte_substrates(bad):
    with pytest.raises(TiirSchemaError):
        OriginalInputBinding.from_bytes(asset(), bad)


def test_metadata_is_closed_immutable_and_size_bounded():
    binding = OriginalInputBinding.from_bytes(asset(), b"abc")
    for value in (-1, True, 2**53):
        with pytest.raises(ValidationError):
            binding.evolve(byte_size=value)
    with pytest.raises(ValidationError):
        binding.byte_size = 0
    with pytest.raises(ValidationError):
        OriginalInputBinding(**binding.to_wire(), filename="image.png")
