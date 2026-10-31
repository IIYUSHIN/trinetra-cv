"""IMP-0286: full key identity is distinct from a routing hint and from trust."""
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.tiir.key_identity import TrustedKeyIdentityVsKid


@given(st.binary(min_size=32, max_size=32))
def test_fingerprint_binds_all_public_key_bytes(public_key):
    key = TrustedKeyIdentityVsKid(raw_public_key=public_key)
    expected = hashlib.sha256(public_key).digest()
    assert key.key_fingerprint.raw == expected
    assert key.kid == expected[:16]
    assert len(key.key_fingerprint.raw) == 32 and len(key.kid) == 16


@pytest.mark.parametrize("bad", [b"", b"x" * 31, b"x" * 33, "x" * 32, bytearray(32), None])
def test_public_key_has_exact_binary_shape(bad):
    with pytest.raises(ValidationError):
        TrustedKeyIdentityVsKid(raw_public_key=bad)


def test_identity_is_not_a_trust_record_and_cannot_accept_a_kid_as_identity():
    key = TrustedKeyIdentityVsKid(raw_public_key=b"x" * 32)
    for extra in ({"kid": key.kid}, {"key_fingerprint": key.key_fingerprint}, {"trusted": True}):
        with pytest.raises(ValidationError):
            TrustedKeyIdentityVsKid(raw_public_key=b"x" * 32, **extra)
    with pytest.raises(ValidationError):
        key.raw_public_key = b"y" * 32
    assert key != TrustedKeyIdentityVsKid(raw_public_key=b"y" * 32)
