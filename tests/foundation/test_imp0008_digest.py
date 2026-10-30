"""IMP-0008 tests: SHA-256 digest utility."""

from __future__ import annotations

import hashlib
import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trinetra.digest import (
    DIGEST_SIZE,
    MAX_CHUNK_SIZE,
    PREFIX,
    DigestError,
    Sha256Stream,
    digests_equal,
    format_digest,
    parse_digest,
    sha256_bytes,
    sha256_digest,
    sha256_file,
    sha256_hex,
    verify_digest,
)
from trinetra.errors import TrinetraError

# FIPS 180-4 / NIST CAVP example vectors, written as literals so the test does
# not rely on hashlib agreeing with itself.
VECTORS = [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
     "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"),
    (b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
     "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"),
    (b"a" * 1_000_000, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"),
]
ABC = VECTORS[1][1]


# ---------------------------------------------------------------- happy path
@pytest.mark.parametrize(("data", "hex_"), VECTORS)
def test_known_answer_vectors(data, hex_):
    assert sha256_hex(data) == hex_
    assert sha256_bytes(data).hex() == hex_
    assert sha256_digest(data) == PREFIX + hex_
    assert verify_digest(data, PREFIX + hex_)


def test_all_buffer_types_hash_identically():
    assert sha256_hex(bytearray(b"abc")) == ABC
    assert sha256_hex(memoryview(b"abc")) == ABC
    assert sha256_hex(memoryview(b"xabcx")[1:4]) == ABC


def test_format_parse_round_trip():
    raw = sha256_bytes(b"abc")
    assert parse_digest(format_digest(raw)) == raw
    assert format_digest(raw) == f"sha256:{ABC}"


def test_stream_matches_one_shot_for_any_chunking():
    data = b"The quick brown fox jumps over the lazy dog" * 1000
    for step in (1, 7, 64, 4096, len(data)):
        stream = Sha256Stream()
        for i in range(0, len(data), step):
            stream.update(data[i:i + step])
        assert stream.size == len(data)
        assert stream.finalize() == sha256_digest(data)


def test_empty_stream_is_the_empty_digest():
    assert Sha256Stream().finalize() == PREFIX + VECTORS[0][1]


def test_sha256_file_matches_in_memory(tmp_path):
    data = os.urandom(300_001)
    path = tmp_path / "blob.bin"
    path.write_bytes(data)
    assert sha256_file(path, max_bytes=len(data), chunk_size=4096) == (sha256_digest(data), len(data))
    assert sha256_file(str(path), max_bytes=10**9) == (sha256_digest(data), len(data))


def test_empty_file(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert sha256_file(path, max_bytes=0) == (PREFIX + VECTORS[0][1], 0)


def test_digests_equal_and_errors_are_trinetra_errors():
    assert digests_equal(PREFIX + ABC, PREFIX + ABC)
    assert not digests_equal(PREFIX + ABC, PREFIX + "0" * 64)
    assert issubclass(DigestError, TrinetraError) and DigestError.code == "DIGEST_INVALID"


# ----------------------------------------------------------------- boundary
def test_file_size_limit_is_inclusive(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"x" * 100)
    assert sha256_file(path, max_bytes=100)[1] == 100
    with pytest.raises(DigestError, match="larger"):
        sha256_file(path, max_bytes=99)


@pytest.mark.parametrize("chunk", [1, 2, 99, 100, 101, MAX_CHUNK_SIZE])
def test_chunk_size_boundaries_do_not_change_the_result(tmp_path, chunk):
    path = tmp_path / "f"
    path.write_bytes(b"y" * 100)
    assert sha256_file(path, max_bytes=100, chunk_size=chunk)[0] == sha256_digest(b"y" * 100)


@pytest.mark.parametrize("chunk", [0, -1, MAX_CHUNK_SIZE + 1, True, 1.5, "4096", None])
def test_bad_chunk_size_rejected(tmp_path, chunk):
    path = tmp_path / "f"
    path.write_bytes(b"z")
    with pytest.raises(DigestError, match="chunk_size"):
        sha256_file(path, max_bytes=10, chunk_size=chunk)  # type: ignore[arg-type]


@pytest.mark.parametrize("limit", [-1, True, 1.0, "9", None])
def test_bad_max_bytes_rejected(tmp_path, limit):
    path = tmp_path / "f"
    path.write_bytes(b"z")
    with pytest.raises(DigestError, match="max_bytes"):
        sha256_file(path, max_bytes=limit)  # type: ignore[arg-type]


def test_raw_digest_length_boundary():
    format_digest(b"\x00" * DIGEST_SIZE)
    for n in (0, 31, 33, 64):
        with pytest.raises(DigestError):
            format_digest(b"\x00" * n)


# ----------------------------------------------------------------- negative
@pytest.mark.parametrize("data", ["abc", None, 5, [1, 2], 1.5, object()])
def test_non_bytes_input_rejected(data):
    for fn in (sha256_bytes, sha256_hex, sha256_digest):
        with pytest.raises(DigestError, match="bytes-like"):
            fn(data)
    with pytest.raises(DigestError):
        Sha256Stream().update(data)


def test_non_contiguous_memoryview_rejected():
    with pytest.raises(DigestError, match="contiguous"):
        sha256_bytes(memoryview(b"abcdef")[::2])


@pytest.mark.parametrize(
    "text",
    [
        "", "sha256:", ABC, "SHA256:" + ABC, "sha256: " + ABC, " sha256:" + ABC, "sha256:" + ABC + " ",
        "sha256:" + ABC + "\n", "sha256:" + ABC.upper(), "sha256:" + ABC[:-1], "sha256:" + ABC + "0",
        "sha256:" + "g" * 64, "sha256:" + "٠" * 64, "sha-256:" + ABC, "sha512:" + ABC, "sha1:" + ABC[:40],
        "md5:" + ABC[:32], "sha256=" + ABC, "sha256:0x" + ABC[2:], "sha256:" + ABC[:32] + "\x00" + ABC[33:],
    ],
)
def test_malformed_wire_digests_rejected(text):
    with pytest.raises(DigestError):
        parse_digest(text)
    with pytest.raises(DigestError):
        digests_equal(text, PREFIX + ABC)
    with pytest.raises(DigestError):
        verify_digest(b"abc", text)


@pytest.mark.parametrize("value", [None, 5, b"sha256:" + ABC.encode(), bytearray(32)])
def test_non_string_wire_digest_rejected(value):
    with pytest.raises(DigestError, match="string"):
        parse_digest(value)


@pytest.mark.parametrize("raw", ["x" * 32, bytearray(32), memoryview(bytes(32)), None, 5])
def test_format_digest_requires_exact_bytes(raw):
    with pytest.raises(DigestError):
        format_digest(raw)


def test_verify_digest_detects_one_bit_changes():
    good = PREFIX + ABC
    assert verify_digest(b"abc", good)
    assert not verify_digest(b"abd", good)
    assert not verify_digest(b"abc ", good)
    flipped = PREFIX + ABC[:-1] + ("e" if ABC[-1] != "e" else "f")
    assert not verify_digest(b"abc", flipped)


def test_stream_cannot_be_reused_after_finalize():
    stream = Sha256Stream()
    stream.update(b"abc")
    assert stream.finalize() == PREFIX + ABC
    for action in (lambda: stream.update(b"x"), stream.finalize):
        with pytest.raises(DigestError, match="finalized"):
            action()


# --------------------------------------------------------- hostile files
def test_missing_file_symlink_directory_and_fifo_rejected(tmp_path):
    with pytest.raises(DigestError, match="cannot open"):
        sha256_file(tmp_path / "nope", max_bytes=10)
    real = tmp_path / "real"
    real.write_bytes(b"abc")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(DigestError, match="cannot open"):
        sha256_file(link, max_bytes=10)
    with pytest.raises(DigestError):
        sha256_file(tmp_path, max_bytes=10)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(DigestError, match="regular"):
        sha256_file(fifo, max_bytes=10)


def test_device_file_rejected():
    with pytest.raises(DigestError, match="regular"):
        sha256_file("/dev/zero", max_bytes=10)


def test_file_that_grows_during_read_is_caught(tmp_path, monkeypatch):
    path = tmp_path / "growing"
    path.write_bytes(b"a" * 10)
    real_read = os.read
    calls = {"n": 0}

    def growing_read(fd, n):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_read(fd, n)
        return b"b" * n  # file kept growing after the size check

    monkeypatch.setattr("trinetra.digest.os.read", growing_read)
    with pytest.raises(DigestError, match="larger"):
        sha256_file(path, max_bytes=10, chunk_size=4)


def test_read_error_is_wrapped(tmp_path, monkeypatch):
    path = tmp_path / "f"
    path.write_bytes(b"abc")

    def boom(fd, n):
        raise OSError(5, "I/O error")

    monkeypatch.setattr("trinetra.digest.os.read", boom)
    with pytest.raises(DigestError, match="cannot read"):
        sha256_file(path, max_bytes=10)


def test_no_file_descriptor_leak_on_rejection(tmp_path):
    before = len(os.listdir("/proc/self/fd"))
    path = tmp_path / "big"
    path.write_bytes(b"x" * 50)
    for _ in range(20):
        with pytest.raises(DigestError):
            sha256_file(path, max_bytes=1)
        with pytest.raises(DigestError):
            sha256_file(tmp_path, max_bytes=1)
    assert len(os.listdir("/proc/self/fd")) == before


# ------------------------------------------------------------------ properties
@given(st.binary(max_size=2048))
def test_property_agrees_with_hashlib_and_round_trips(data):
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()
    assert parse_digest(sha256_digest(data)) == hashlib.sha256(data).digest()
    assert verify_digest(data, sha256_digest(data))


@given(st.binary(max_size=1024), st.lists(st.integers(1, 200), min_size=1, max_size=8))
def test_property_streaming_is_chunking_invariant(data, cuts):
    stream, pos = Sha256Stream(), 0
    for cut in cuts:
        stream.update(data[pos:pos + cut])
        pos += cut
    stream.update(data[pos:])
    assert stream.finalize() == sha256_digest(data)


@given(st.binary(min_size=1, max_size=256), st.integers(0, 255))
def test_property_any_single_byte_change_breaks_verification(data, index):
    i = index % len(data)
    mutated = bytearray(data)
    mutated[i] ^= 0x01
    assert not verify_digest(bytes(mutated), sha256_digest(data))


@given(st.text(max_size=80))
def test_property_parse_accepts_only_canonical_form(text):
    try:
        raw = parse_digest(text)
    except DigestError:
        return
    assert format_digest(raw) == text
