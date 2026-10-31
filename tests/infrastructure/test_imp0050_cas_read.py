"""IMP-0050 tests: read-by-digest checks object shape and never follows or trusts paths."""

from __future__ import annotations

import io
import os

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_admission import stream_to_quarantine
from trinetra.infrastructure.cas_commit import commit_candidate
from trinetra.infrastructure.cas_layout import CasLayout, CasLayoutError
from trinetra.infrastructure.cas_quarantine import Quarantine
from trinetra.infrastructure.cas_read import (
    ObjectMissingError,
    ObjectShapeError,
    open_object,
    read_object,
)


@pytest.fixture
def layout(state_root):
    lay = CasLayout.for_state_root(state_root)
    lay.ensure()
    return lay


def put(layout, data):
    cand = stream_to_quarantine(Quarantine(layout, max_bytes=10_000), io.BytesIO(data))
    return commit_candidate(layout, cand)


def test_a_committed_object_reads_back_by_digest_or_wire_form(layout):
    done = put(layout, b"content")
    assert read_object(layout, done.digest, max_bytes=100) == b"content"
    assert read_object(layout, done.digest.to_wire(), max_bytes=100) == b"content"


def test_open_returns_a_read_only_stream_with_the_observed_size(layout):
    done = put(layout, b"content")
    opened = open_object(layout, done.digest)
    try:
        assert opened.size == 7
        assert opened.stream.read() == b"content"
        assert not opened.stream.writable()
    finally:
        opened.close()
    assert opened.stream.closed


def test_a_missing_object_is_reported_as_missing(layout):
    with pytest.raises(ObjectMissingError):
        open_object(layout, DigestSha256.of(b"never stored"))


def test_a_malformed_digest_never_touches_the_filesystem(layout):
    with pytest.raises(CasLayoutError):
        open_object(layout, "../../etc/passwd")


def test_the_size_limit_is_enforced_before_reading(layout):
    done = put(layout, b"0123456789")
    assert read_object(layout, done.digest, max_bytes=10) == b"0123456789"
    with pytest.raises(ObjectShapeError, match="larger"):
        read_object(layout, done.digest, max_bytes=9)
    with pytest.raises(ObjectShapeError, match="negative"):
        read_object(layout, done.digest, max_bytes=-1)


def test_the_empty_object_reads_as_empty(layout):
    done = put(layout, b"")
    assert read_object(layout, done.digest, max_bytes=0) == b""


def test_a_symlink_in_place_of_an_object_is_refused(layout, tmp_path):
    digest = DigestSha256.of(b"decoy")
    layout.shard_directory(digest)
    outside = tmp_path / "outside"
    outside.write_bytes(b"decoy")
    layout.object_path(digest).symlink_to(outside)
    with pytest.raises(ObjectShapeError, match="plain file"):
        open_object(layout, digest)


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda p: os.chmod(p, 0o644), "writable"),
    (lambda p: os.link(p, p.with_name("alias")), "hard link"),
])
def test_a_tampered_object_shape_is_refused(layout, mutate, match):
    done = put(layout, b"shape")
    mutate(done.path)
    with pytest.raises(ObjectShapeError, match=match):
        open_object(layout, done.digest)


def test_a_directory_at_the_object_path_is_refused(layout):
    digest = DigestSha256.of(b"dir")
    layout.shard_directory(digest)
    layout.object_path(digest).mkdir(mode=0o500)
    with pytest.raises(ObjectShapeError, match="regular"):
        open_object(layout, digest)


def test_a_foreign_owner_is_refused(layout, monkeypatch):
    done = put(layout, b"owner")
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 7)
    with pytest.raises(ObjectShapeError, match="owned"):
        open_object(layout, done.digest)


def test_no_descriptor_leaks_when_the_shape_check_fails(layout):
    done = put(layout, b"leak")
    os.chmod(done.path, 0o644)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        with pytest.raises(ObjectShapeError):
            open_object(layout, done.digest)
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("mode", [0o464, 0o446, 0o666])
def test_group_or_other_write_permission_is_refused(layout, mode):
    done = put(layout, b"perm")
    os.chmod(done.path, mode)
    with pytest.raises(ObjectShapeError, match="writable"):
        open_object(layout, done.digest)
