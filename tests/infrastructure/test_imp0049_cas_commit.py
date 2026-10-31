"""IMP-0049 tests: commit is atomic, read-only, never overwrites, and re-verified from disk."""

from __future__ import annotations

import io
import os
import stat

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_admission import stream_to_quarantine
from trinetra.infrastructure.cas_commit import (
    CommitError,
    ObjectExistsError,
    commit_candidate,
    hash_file,
)
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import Quarantine


@pytest.fixture
def layout(state_root):
    lay = CasLayout.for_state_root(state_root)
    lay.ensure()
    return lay


@pytest.fixture
def quarantine(layout):
    return Quarantine(layout, max_bytes=10_000)


def _raise(exc):
    raise exc


def stage(q, data=b"payload"):
    return stream_to_quarantine(q, io.BytesIO(data))


def test_commit_publishes_at_the_digest_path_read_only(layout, quarantine):
    cand = stage(quarantine)
    done = commit_candidate(layout, cand)
    assert done.path == layout.object_path(DigestSha256.of(b"payload"))
    assert done.path.read_bytes() == b"payload"
    assert stat.S_IMODE(done.path.stat().st_mode) == 0o444
    assert done.size == 7
    assert done.path.stat().st_nlink == 1  # the quarantine name is gone
    assert quarantine.orphans() == []


def test_the_empty_object_commits(layout, quarantine):
    done = commit_candidate(layout, stage(quarantine, b""))
    assert done.size == 0
    assert done.path.read_bytes() == b""


def test_an_existing_object_is_never_overwritten(layout, quarantine):
    first = commit_candidate(layout, stage(quarantine))
    inode = first.path.stat().st_ino
    second = stage(quarantine)
    with pytest.raises(ObjectExistsError):
        commit_candidate(layout, second)
    assert first.path.stat().st_ino == inode
    assert first.path.read_bytes() == b"payload"
    assert second.staged.path.exists()  # left for the caller to discard
    second.discard()


def test_a_candidate_still_open_is_refused(layout, quarantine):
    cand = stage(quarantine)
    reopened = quarantine.begin()
    cand_like = type(cand)(reopened, cand.digest, cand.size)
    with pytest.raises(CommitError, match="still open"):
        commit_candidate(layout, cand_like)
    reopened.discard()
    cand.discard()


def test_tampered_staged_bytes_never_become_an_addressable_object(layout, quarantine):
    cand = stage(quarantine)
    os.chmod(cand.staged.path, 0o600)
    cand.staged.path.write_bytes(b"EVIL!!!")  # same size, different content, after hashing
    with pytest.raises(CommitError, match="does not match"):
        commit_candidate(layout, cand)
    assert not layout.object_path(cand.digest).exists()


def test_a_size_mismatch_is_detected(layout, quarantine):
    cand = stage(quarantine)
    cand.size += 1
    with pytest.raises(CommitError, match="does not match"):
        commit_candidate(layout, cand)
    assert not layout.object_path(cand.digest).exists()


def test_directories_are_fsynced_in_order(layout, quarantine, monkeypatch):
    seen = []
    real = os.fsync

    def spy(fd):
        seen.append(os.readlink(f"/proc/self/fd/{fd}"))
        real(fd)

    cand = stage(quarantine)
    monkeypatch.setattr(os, "fsync", spy)
    done = commit_candidate(layout, cand)
    shard = str(done.path.parent)
    assert seen[:4] == [shard, os.path.dirname(shard), str(layout.objects), str(layout.quarantine)]


def test_a_failure_after_link_removes_the_unverified_object(layout, quarantine, monkeypatch):
    cand = stage(quarantine)
    monkeypatch.setattr(os, "fsync", lambda _fd: _raise(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        commit_candidate(layout, cand)
    assert not layout.object_path(cand.digest).exists()


def test_a_crash_before_link_leaves_no_object(layout, quarantine, monkeypatch):
    cand = stage(quarantine)
    monkeypatch.setattr(os, "link", lambda *_a, **_k: _raise(OSError("killed")))
    with pytest.raises(OSError, match="killed"):
        commit_candidate(layout, cand)
    assert not layout.object_path(cand.digest).exists()
    assert cand.staged.path.exists()


def test_hash_file_matches_and_refuses_symlinks_and_directories(layout, tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"abc" * 1000)
    digest, size = hash_file(f)
    assert digest == DigestSha256.of(b"abc" * 1000)
    assert size == 3000
    link = tmp_path / "l"
    link.symlink_to(f)
    with pytest.raises(OSError):
        hash_file(link)
    with pytest.raises(CommitError, match="regular"):
        hash_file(tmp_path)


def test_hash_file_streams_across_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr("trinetra.infrastructure.cas_commit.READ_CHUNK", 7)
    f = tmp_path / "f"
    f.write_bytes(bytes(range(256)) * 3)
    assert hash_file(f)[0] == DigestSha256.of(bytes(range(256)) * 3)


def test_objects_from_different_content_live_in_different_paths(layout, quarantine):
    a = commit_candidate(layout, stage(quarantine, b"a"))
    b = commit_candidate(layout, stage(quarantine, b"b"))
    assert a.path != b.path


def test_directory_sync_refuses_a_file(tmp_path):
    from trinetra.infrastructure.cas_commit import _fsync_directory

    f = tmp_path / "f"
    f.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        _fsync_directory(f)
