"""IMP-0027 tests: DigestSha256 and ContentIdentity."""

from __future__ import annotations

import copy
import hashlib
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from trinetra.domain.digests import MAX_SIZE_BYTES, ContentIdentity, DigestSha256, DigestValueError

ABC = "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
EMPTY = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_known_answers_and_wire_roundtrip():
    assert DigestSha256.of(b"abc").to_wire() == ABC == str(DigestSha256.of(b"abc"))
    assert DigestSha256.of(b"").to_wire() == EMPTY
    d = DigestSha256.parse(ABC)
    assert d.hex == ABC[7:] and d.raw == hashlib.sha256(b"abc").digest() and d == DigestSha256.of(b"abc")
    assert repr(d) == f"DigestSha256({ABC!r})" and hash(d) == hash(DigestSha256.parse(ABC))


@pytest.mark.parametrize(
    "bad",
    ["", "sha256:", ABC.upper(), ABC[:-1], ABC + "0", ABC.replace("sha256:", "sha1:"), ABC[7:], " " + ABC,
     ABC + "\n", "sha256:" + "g" * 64, None, 5, b"x", ABC.encode(), DigestSha256.of(b"")],
)
def test_malformed_wire_rejected(bad):
    with pytest.raises(DigestValueError):
        DigestSha256.parse(bad)


@pytest.mark.parametrize("raw", [b"", b"x" * 31, b"x" * 33, bytearray(32), "x" * 32, None, 32])
def test_constructor_needs_exactly_32_raw_bytes(raw):
    with pytest.raises(DigestValueError):
        DigestSha256(raw)


def test_immutable_picklable_not_equal_to_plain_values():
    d = DigestSha256.of(b"abc")
    with pytest.raises(AttributeError):
        d._raw = b"\x00" * 32
    with pytest.raises(AttributeError):
        del d._raw
    assert pickle.loads(pickle.dumps(d)) == d
    assert copy.copy(d) is d and copy.deepcopy(d) is d
    assert (d == ABC) is False and (d == d.raw) is False and (d == d.hex) is False


# ---------------------------------------------------------- ContentIdentity
def test_content_identity_of_and_matches():
    ci = ContentIdentity.of(b"abc")
    assert ci.size_bytes == 3 and ci.digest.to_wire() == ABC
    assert ci.matches(b"abc") and not ci.matches(b"abd") and not ci.matches(b"abcd") and not ci.matches(b"")
    assert ContentIdentity.of(b"").size_bytes == 0


def test_same_digest_different_size_are_different_identities():
    a = ContentIdentity(digest=DigestSha256.of(b"abc"), size_bytes=3)
    b = ContentIdentity(digest=DigestSha256.of(b"abc"), size_bytes=4)
    assert a != b and not b.matches(b"abc")


def test_wire_form_and_canonical_digest_are_deterministic():
    ci = ContentIdentity.of(b"abc")
    assert ci.to_wire() == {"digest": ABC, "size_bytes": 3}
    assert ci.canonical_bytes() == f'{{"digest":"{ABC}","size_bytes":3}}'.encode()
    assert ContentIdentity(**ci.to_wire()) == ci


@pytest.mark.parametrize("size", [-1, MAX_SIZE_BYTES + 1, True, False, 1.0, "3", None])
def test_size_bounds_and_types(size):
    with pytest.raises(ValidationError):
        ContentIdentity(digest=DigestSha256.of(b""), size_bytes=size)


def test_size_boundary_values_accepted():
    for ok in (0, MAX_SIZE_BYTES):
        assert ContentIdentity(digest=DigestSha256.of(b""), size_bytes=ok).size_bytes == ok


@pytest.mark.parametrize("digest", ["md5:" + "0" * 32, ABC.upper(), "", 5, None, b"x" * 32])
def test_bad_digest_in_model_rejected(digest):
    with pytest.raises(ValidationError):
        ContentIdentity(digest=digest, size_bytes=1)


@given(st.binary(max_size=2048))
def test_property_identity_matches_its_own_data_and_roundtrips(data):
    ci = ContentIdentity.of(data)
    assert ci.matches(data) and ContentIdentity(**ci.to_wire()) == ci
    assert ci.digest.to_wire() == "sha256:" + hashlib.sha256(data).hexdigest()
