"""IMP-0048 tests: streaming admission computes identity from bytes and fails closed."""

from __future__ import annotations

import hashlib
import io

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_admission import AdmissionError, stream_to_quarantine
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import Quarantine


@pytest.fixture
def quarantine(state_root):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    return Quarantine(layout, max_bytes=1000)


def leftovers(q):
    return q.orphans()


def test_digest_and_size_come_from_the_bytes(quarantine):
    cand = stream_to_quarantine(quarantine, io.BytesIO(b"hello"))
    assert cand.digest == DigestSha256.of(b"hello")
    assert cand.digest.hex == hashlib.sha256(b"hello").hexdigest()
    assert cand.size == 5
    assert cand.staged.path.read_bytes() == b"hello"
    cand.discard()
    assert leftovers(quarantine) == []


def test_empty_content_is_admissible_and_has_the_empty_digest(quarantine):
    cand = stream_to_quarantine(quarantine, io.BytesIO(b""), expected_size=0)
    assert cand.size == 0
    assert cand.digest.hex == hashlib.sha256(b"").hexdigest()
    cand.discard()


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40, deadline=None)
@given(st.binary(max_size=500), st.integers(min_value=1, max_value=64))
def test_any_content_and_chunking_yield_the_same_identity(quarantine, data, chunk):
    cand = stream_to_quarantine(quarantine, io.BytesIO(data), chunk_size=chunk, expected_size=len(data))
    assert cand.digest == DigestSha256.of(data)
    assert cand.staged.path.read_bytes() == data
    cand.discard()


def test_a_matching_declared_digest_and_size_pass(quarantine):
    cand = stream_to_quarantine(
        quarantine, io.BytesIO(b"abc"), expected_digest=DigestSha256.of(b"abc"), expected_size=3
    )
    assert cand.size == 3
    cand.discard()


def test_a_wrong_declared_digest_fails_closed_and_leaves_nothing(quarantine):
    with pytest.raises(AdmissionError, match="declared digest"):
        stream_to_quarantine(quarantine, io.BytesIO(b"abc"), expected_digest=DigestSha256.of(b"abd"))
    assert leftovers(quarantine) == []


@pytest.mark.parametrize(("declared", "match"), [(2, "longer"), (4, "shorter")])
def test_a_wrong_declared_size_fails_closed(quarantine, declared, match):
    with pytest.raises(AdmissionError, match=match):
        stream_to_quarantine(quarantine, io.BytesIO(b"abc"), expected_size=declared)
    assert leftovers(quarantine) == []


def test_the_quarantine_size_limit_aborts_and_cleans_up(quarantine):
    with pytest.raises(Exception, match="size limit"):
        stream_to_quarantine(quarantine, io.BytesIO(b"x" * 1001), chunk_size=100)
    assert leftovers(quarantine) == []


def test_memory_is_bounded_by_the_chunk_size(quarantine):
    sizes = []

    class Spy(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    stream_to_quarantine(quarantine, Spy(b"y" * 300), chunk_size=64).discard()
    assert set(sizes) == {64}


class Liar:
    def __init__(self, value):
        self.value = value
        self.sent = False

    def read(self, size):
        if self.sent:
            return b""
        self.sent = True
        return self.value


@pytest.mark.parametrize(("value", "match"), [("text", "non-bytes"), (bytearray(b"ab"), "non-bytes"),
                                             (b"x" * 11, "more than requested"), (None, "non-bytes")])
def test_a_misbehaving_source_is_rejected(quarantine, value, match):
    with pytest.raises(AdmissionError, match=match):
        stream_to_quarantine(quarantine, Liar(value), chunk_size=10)
    assert leftovers(quarantine) == []


def test_a_source_that_raises_mid_stream_leaves_nothing(quarantine):
    class Boom:
        calls = 0

        def read(self, size):
            self.calls += 1
            if self.calls == 2:
                raise OSError("disk gone")
            return b"a" * size

    with pytest.raises(OSError, match="disk gone"):
        stream_to_quarantine(quarantine, Boom(), chunk_size=10)
    assert leftovers(quarantine) == []


@pytest.mark.parametrize("chunk", [0, -1, 64 * 1024 * 1024 + 1])
def test_chunk_size_is_range_checked_before_any_file_is_created(quarantine, chunk):
    with pytest.raises(AdmissionError, match="chunk_size"):
        stream_to_quarantine(quarantine, io.BytesIO(b"x"), chunk_size=chunk)
    assert leftovers(quarantine) == []


def test_negative_expected_size_is_refused(quarantine):
    with pytest.raises(AdmissionError, match="negative"):
        stream_to_quarantine(quarantine, io.BytesIO(b"x"), expected_size=-1)
    assert leftovers(quarantine) == []


def test_filename_plays_no_part_in_identity(quarantine):
    a = stream_to_quarantine(quarantine, io.BytesIO(b"same"))
    b = stream_to_quarantine(quarantine, io.BytesIO(b"same"))
    assert a.digest == b.digest
    assert a.staged.name != b.staged.name
    a.discard()
    b.discard()


@pytest.mark.parametrize("chunk", [1, 64 * 1024 * 1024])
def test_chunk_size_bounds_are_inclusive(quarantine, chunk):
    cand = stream_to_quarantine(quarantine, io.BytesIO(b"ok"), chunk_size=chunk)
    assert cand.size == 2
    cand.discard()


def test_the_candidate_is_closed_and_durable_before_it_is_returned(quarantine, monkeypatch):
    import os

    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    cand = stream_to_quarantine(quarantine, io.BytesIO(b"durable"))
    assert synced
    assert not cand.staged.is_open
    cand.discard()
