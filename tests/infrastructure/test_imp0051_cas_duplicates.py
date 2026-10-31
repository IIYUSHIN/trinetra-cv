"""IMP-0051 tests: identical content is idempotent; a disagreeing object fails closed, untouched."""

from __future__ import annotations

import io
import os
import threading

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore, DuplicateConflictError


@pytest.fixture
def store(state_root):
    return CasStore(CasLayout.for_state_root(state_root), max_bytes=10_000)


def test_first_admission_creates_then_repeat_is_idempotent(store):
    first = store.admit(io.BytesIO(b"same bytes"))
    second = store.admit(io.BytesIO(b"same bytes"))
    assert (first.created, second.created) == (True, False)
    assert first.digest == second.digest == DigestSha256.of(b"same bytes")
    assert first.size == second.size == 10
    path = store.layout.object_path(first.digest)
    assert path.read_bytes() == b"same bytes"
    assert store.layout.quarantine.exists()
    assert list(store.layout.quarantine.iterdir()) == []


def test_the_existing_object_is_not_rewritten(store):
    first = store.admit(io.BytesIO(b"keep me"))
    path = store.layout.object_path(first.digest)
    inode, mtime = path.stat().st_ino, path.stat().st_mtime_ns
    store.admit(io.BytesIO(b"keep me"))
    assert (path.stat().st_ino, path.stat().st_mtime_ns) == (inode, mtime)


def test_different_content_gets_a_different_object(store):
    a, b = store.admit(io.BytesIO(b"a")), store.admit(io.BytesIO(b"b"))
    assert a.digest != b.digest
    assert a.created and b.created


def test_a_corrupt_existing_object_is_a_conflict_and_is_left_untouched(store):
    first = store.admit(io.BytesIO(b"original"))
    path = store.layout.object_path(first.digest)
    os.chmod(path, 0o644)
    path.write_bytes(b"tampered")
    os.chmod(path, 0o444)
    with pytest.raises(DuplicateConflictError, match="does not match"):
        store.admit(io.BytesIO(b"original"))
    assert path.read_bytes() == b"tampered"  # evidence preserved, never silently repaired
    assert list(store.layout.quarantine.iterdir()) == []


def test_a_same_size_corruption_is_still_detected(store):
    first = store.admit(io.BytesIO(b"12345678"))
    path = store.layout.object_path(first.digest)
    os.chmod(path, 0o644)
    path.write_bytes(b"1234567X")
    os.chmod(path, 0o444)
    with pytest.raises(DuplicateConflictError):
        store.admit(io.BytesIO(b"12345678"))


def test_a_truncated_existing_object_is_a_conflict(store):
    first = store.admit(io.BytesIO(b"longer content"))
    path = store.layout.object_path(first.digest)
    os.chmod(path, 0o644)
    path.write_bytes(b"")
    os.chmod(path, 0o444)
    with pytest.raises(DuplicateConflictError):
        store.admit(io.BytesIO(b"longer content"))


def test_a_malformed_existing_object_is_a_conflict(store):
    first = store.admit(io.BytesIO(b"shape"))
    os.chmod(store.layout.object_path(first.digest), 0o644)
    with pytest.raises(DuplicateConflictError, match="sound"):
        store.admit(io.BytesIO(b"shape"))


def test_declared_digest_is_still_checked_on_the_duplicate_path(store):
    store.admit(io.BytesIO(b"x"))
    with pytest.raises(Exception, match="declared digest"):
        store.admit(io.BytesIO(b"x"), expected_digest=DigestSha256.of(b"y"))


def test_concurrent_admissions_converge_on_one_object(store):
    results, errors = [], []

    def worker():
        try:
            results.append(store.admit(io.BytesIO(b"race" * 1000)))
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(r.created for r in results) == [False] * 7 + [True]
    assert len({r.digest for r in results}) == 1
    assert list(store.layout.quarantine.iterdir()) == []


def test_failure_in_commit_discards_the_candidate(store, monkeypatch):
    import trinetra.infrastructure.cas_store as module

    def boom(*_a, **_k):
        raise OSError("link failed")

    monkeypatch.setattr(module, "commit_candidate", boom)
    with pytest.raises(OSError, match="link failed"):
        store.admit(io.BytesIO(b"x"))
    assert list(store.layout.quarantine.iterdir()) == []


def test_chunk_size_is_passed_through(store):
    assert store.admit(io.BytesIO(b"abcdef"), chunk_size=2).size == 6


def test_a_persistent_extra_hard_link_is_a_conflict(store, monkeypatch):
    import trinetra.infrastructure.cas_store as module

    monkeypatch.setattr(module, "SETTLE_SECONDS", 0.0)
    first = store.admit(io.BytesIO(b"linked"))
    path = store.layout.object_path(first.digest)
    os.link(path, path.with_name("alias"))
    with pytest.raises(DuplicateConflictError, match="sound"):
        store.admit(io.BytesIO(b"linked"))
    assert list(store.layout.quarantine.iterdir()) == []


def test_settling_waits_out_a_transient_second_link(store, monkeypatch):
    import trinetra.infrastructure.cas_store as module

    first = store.admit(io.BytesIO(b"transient"))
    path = store.layout.object_path(first.digest)
    alias = path.with_name("alias")
    os.link(path, alias)
    calls = []

    def fake_sleep(_s):
        calls.append(1)
        if len(calls) == 3:
            alias.unlink()

    monkeypatch.setattr(module.time, "sleep", fake_sleep)
    assert store.admit(io.BytesIO(b"transient")).created is False
    assert len(calls) == 3
