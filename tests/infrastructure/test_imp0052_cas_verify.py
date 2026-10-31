"""IMP-0052 tests: corruption is detected and classified; nothing is repaired or deleted."""

from __future__ import annotations

import io
import os

import pytest

from trinetra.domain.digests import DigestSha256
from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_store import CasStore
from trinetra.infrastructure.cas_verify import scan_objects, verify_object


@pytest.fixture
def store(state_root):
    return CasStore(CasLayout.for_state_root(state_root), max_bytes=100_000)


def add(store, data):
    return store.admit(io.BytesIO(data)).digest


def rewrite(path, data):
    os.chmod(path, 0o644)
    path.write_bytes(data)
    os.chmod(path, 0o444)


def test_an_intact_object_verifies(store):
    d = add(store, b"intact")
    r = verify_object(store.layout, d)
    assert (r.status, r.ok, r.size) == ("VERIFIED", True, 6)
    assert r.digest == d.to_wire()


def test_the_empty_object_verifies(store):
    assert verify_object(store.layout, add(store, b"")).ok


def test_a_flipped_bit_is_a_digest_mismatch(store):
    d = add(store, b"abcdefgh")
    rewrite(store.layout.object_path(d), b"abcdefgi")
    r = verify_object(store.layout, d)
    assert r.status == "DIGEST_MISMATCH"
    assert DigestSha256.of(b"abcdefgi").to_wire() in r.detail


def test_truncation_and_extension_are_detected(store):
    d = add(store, b"0123456789")
    rewrite(store.layout.object_path(d), b"01234")
    assert verify_object(store.layout, d).status == "DIGEST_MISMATCH"
    rewrite(store.layout.object_path(d), b"0123456789" * 2)
    assert verify_object(store.layout, d).status == "DIGEST_MISMATCH"


def test_a_missing_object_is_missing(store):
    r = verify_object(store.layout, DigestSha256.of(b"nothing"))
    assert (r.status, r.ok) == ("MISSING", False)


def test_a_malformed_shape_is_reported_not_raised(store):
    d = add(store, b"shape")
    os.chmod(store.layout.object_path(d), 0o644)
    assert verify_object(store.layout, d).status == "MALFORMED"


def test_verification_never_modifies_or_deletes(store):
    d = add(store, b"evidence")
    path = store.layout.object_path(d)
    rewrite(path, b"EVIDENCE")
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    verify_object(store.layout, d)
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == before


def test_io_errors_are_classified(store, monkeypatch):
    import trinetra.infrastructure.cas_verify as module

    d = add(store, b"io")

    def boom(_p):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(module, "hash_file", boom)
    r = verify_object(store.layout, d)
    assert r.status == "IO_ERROR"
    assert "Input/output" in r.detail


def test_an_unopenable_object_is_an_io_error(store, monkeypatch):
    import trinetra.infrastructure.cas_verify as module

    d = add(store, b"open")

    def boom(*_a):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(module, "open_fd", boom)
    assert verify_object(store.layout, d).status == "IO_ERROR"


def test_a_clean_store_scans_clean(store):
    for i in range(5):
        add(store, f"obj{i}".encode())
    report = scan_objects(store.layout)
    assert (report.verified, report.clean) == (5, True)


def test_an_empty_store_scans_clean(store):
    assert scan_objects(store.layout).clean


def test_scan_reports_every_problem_without_stopping(store):
    good, bad1, bad2 = add(store, b"good"), add(store, b"bad1"), add(store, b"bad2")
    rewrite(store.layout.object_path(bad1), b"BAD1")
    store.layout.object_path(bad2).unlink()
    store.layout.object_path(bad2).symlink_to(store.layout.object_path(good))
    report = scan_objects(store.layout)
    assert report.verified == 1
    assert {p.digest: p.status for p in report.problems} == {
        bad1.to_wire(): "DIGEST_MISMATCH", bad2.to_wire(): "MALFORMED",
    }
    assert not report.clean


def test_scan_reports_strays(store):
    d = add(store, b"anchor")
    leaf = store.layout.object_path(d).parent
    (leaf / "notes.txt").write_text("x")
    (leaf / ("f" * 64)).write_bytes(b"misplaced")  # right shape of name, wrong shard
    (store.layout.objects / "zz").mkdir()
    (store.layout.objects / "loose-file").write_text("x")
    (store.layout.objects / "ab").mkdir(exist_ok=True)
    (store.layout.objects / "ab" / "not-a-shard").mkdir()
    report = scan_objects(store.layout)
    stray = set(report.strays)
    assert stray >= {f"{d.hex[:2]}/{d.hex[2:4]}/notes.txt", f"{d.hex[:2]}/{d.hex[2:4]}/{'f' * 64}", "zz",
                     "loose-file", "ab/not-a-shard"}
    assert report.verified == 1
    assert not report.clean


def test_a_symlinked_shard_is_a_stray_not_followed(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.layout.objects / "ab").symlink_to(outside)
    report = scan_objects(store.layout)
    assert report.strays == ["ab"]


def test_no_descriptor_leaks_across_many_verifications(store):
    d = add(store, b"leak")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(30):
        verify_object(store.layout, d)
    assert len(os.listdir("/proc/self/fd")) == before


def test_a_size_that_changes_while_verifying_is_a_mismatch(store, monkeypatch):
    import trinetra.infrastructure.cas_verify as module

    d = add(store, b"racing")
    real = module.hash_file

    def lying(path):
        digest, size = real(path)
        return digest, size + 1

    monkeypatch.setattr(module, "hash_file", lying)
    assert verify_object(store.layout, d).status == "DIGEST_MISMATCH"


def test_a_symlinked_second_level_shard_is_a_stray(store, tmp_path):
    first = store.layout.objects / "ab"
    first.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (first / "cd").symlink_to(outside)
    assert scan_objects(store.layout).strays == ["ab/cd"]


@pytest.mark.parametrize(("first", "second", "name"), [
    ("ab", "cd", "efcd" + "0" * 60),  # first shard disagrees with the name
    ("ab", "cd", "ab" + "ee" + "0" * 60),  # second shard disagrees with the name
    ("ab", "cd", "abcd" + "z" * 60),  # prefix right, not hex
    ("ab", "cd", "abcd" + "0" * 59),  # wrong length
])
def test_misplaced_or_malformed_leaf_names_are_strays(store, first, second, name):
    leaf = store.layout.objects / first / second
    leaf.mkdir(parents=True)
    (leaf / name).write_bytes(b"x")
    assert scan_objects(store.layout).strays == [f"{first}/{second}/{name}"]


def test_a_correctly_placed_leaf_is_not_a_stray(store):
    leaf = store.layout.objects / "ab" / "cd"
    leaf.mkdir(parents=True)
    name = "abcd" + "0" * 60
    (leaf / name).write_bytes(b"not the right content")
    report = scan_objects(store.layout)
    assert report.strays == []
    assert [p.status for p in report.problems] == ["MALFORMED"]  # writable file: shape fails first
