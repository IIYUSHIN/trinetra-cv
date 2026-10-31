"""IMP-0047 tests: staged writes are exclusive, private, bounded, discarded unless handed on."""

from __future__ import annotations

import os
import re

import pytest

from trinetra.infrastructure.cas_layout import CasLayout
from trinetra.infrastructure.cas_quarantine import (
    Quarantine,
    QuarantineError,
    is_stage_name,
)


@pytest.fixture
def quarantine(state_root):
    layout = CasLayout.for_state_root(state_root)
    layout.ensure()
    return Quarantine(layout, max_bytes=100)


def test_a_staged_file_is_private_generated_and_inside_quarantine(quarantine):
    with quarantine.begin() as staged:
        assert re.fullmatch(r"stage-[0-9a-f]{32}\.part", staged.name)
        assert staged.path.parent == quarantine._layout.quarantine
        assert staged.path.stat().st_mode & 0o777 == 0o600
        assert is_stage_name(staged.name)


def test_names_are_unique(quarantine):
    with quarantine.begin() as a, quarantine.begin() as b:
        assert a.name != b.name


def test_written_bytes_land_in_the_file_and_are_counted(quarantine):
    with quarantine.begin() as staged:
        staged.write(b"abc")
        staged.write(b"")
        staged.write(b"def")
        assert staged.size == 6
        staged.sync_and_close()
        assert staged.path.read_bytes() == b"abcdef"


def test_the_size_limit_is_exact(quarantine):
    with quarantine.begin() as staged:
        staged.write(b"x" * 100)
        with pytest.raises(QuarantineError, match="size limit"):
            staged.write(b"y")
        assert staged.size == 100


def test_exiting_discards_unless_released(quarantine):
    with quarantine.begin() as staged:
        staged.write(b"gone")
        path = staged.path
    assert not path.exists()
    with quarantine.begin() as staged:
        staged.write(b"kept")
        path = staged.release()
    assert path.read_bytes() == b"kept"
    assert path in list(quarantine._layout.quarantine.iterdir())


def test_an_exception_discards_the_file(quarantine):
    with pytest.raises(RuntimeError), quarantine.begin() as staged:
        staged.write(b"x")
        path = staged.path
        raise RuntimeError
    assert not path.exists()


def test_writing_after_close_is_refused(quarantine):
    with quarantine.begin() as staged:
        staged.sync_and_close()
        staged.sync_and_close()  # idempotent
        assert not staged.is_open
        with pytest.raises(QuarantineError, match="closed"):
            staged.write(b"x")


def test_discard_is_idempotent(quarantine):
    staged = quarantine.begin()
    staged.discard()
    staged.discard()
    assert quarantine.orphans() == []


def test_a_planted_symlink_is_never_followed(quarantine, monkeypatch, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("precious")
    fixed = "0" * 32
    monkeypatch.setattr("secrets.token_hex", lambda _n: fixed)
    (quarantine._layout.quarantine / f"stage-{fixed}.part").symlink_to(victim)
    with pytest.raises(OSError):
        quarantine.begin()
    assert victim.read_text() == "precious"


@pytest.mark.parametrize(("field", "value"), [("st_nlink", 2), ("st_uid", 424242), ("st_mode", 0o040600)])
def test_an_untrustworthy_created_file_is_refused_and_removed(quarantine, monkeypatch, field, value):
    real = os.fstat

    class Fake:
        def __init__(self, info):
            self._info = info

        def __getattr__(self, name):
            return value if name == field else getattr(self._info, name)

    def fake(fd):
        return Fake(real(fd))

    monkeypatch.setattr(os, "fstat", fake)
    with pytest.raises(QuarantineError, match="private regular"):
        quarantine.begin()
    assert quarantine.orphans() == []


def test_a_group_writable_quarantine_is_refused(quarantine):
    os.chmod(quarantine._layout.quarantine, 0o770)  # noqa: S103 - deliberately unsafe fixture
    with pytest.raises(Exception, match="writable"):
        quarantine.begin()


@pytest.mark.parametrize(("name", "expected"), [
    ("stage-" + "a" * 32 + ".part", True), ("stage-" + "A" * 32 + ".part", False),
    ("stage-" + "a" * 31 + ".part", False), ("stage-" + "a" * 32 + ".bak", False),
    ("x-" + "a" * 32 + ".part", False), ("stage-" + "g" * 32 + ".part", False), ("", False),
])
def test_only_generated_names_are_recognised(name, expected):
    assert is_stage_name(name) is expected


def test_sweep_removes_only_old_generated_files(quarantine):
    old = quarantine.begin()
    old.release()
    new = quarantine.begin()
    new.release()
    foreign = quarantine._layout.quarantine / "notes.txt"
    foreign.write_text("keep")
    os.utime(old.path, (1000, 1000))
    os.utime(foreign, (1000, 1000))
    removed = quarantine.sweep(older_than_seconds=3600, now=100000)
    assert removed == [old.name]
    assert not old.path.exists()
    assert new.path.exists()
    assert foreign.exists()
    assert sorted(quarantine.orphans()) == sorted([new.name, "notes.txt"])


def test_sweep_cutoff_is_inclusive_of_the_boundary(quarantine):
    staged = quarantine.begin()
    staged.release()
    os.utime(staged.path, (5000, 5000))
    assert quarantine.sweep(older_than_seconds=100, now=5099) == []
    assert quarantine.sweep(older_than_seconds=100, now=5100) == [staged.name]


def test_negative_arguments_are_refused(quarantine):
    with pytest.raises(QuarantineError):
        quarantine.sweep(older_than_seconds=-1)
    with pytest.raises(QuarantineError):
        Quarantine(quarantine._layout, max_bytes=-1)


def test_sweep_does_not_remove_a_directory_with_a_stage_name(quarantine):
    fake = quarantine._layout.quarantine / ("stage-" + "b" * 32 + ".part")
    fake.mkdir(mode=0o700)
    os.utime(fake, (1000, 1000))
    assert quarantine.sweep(older_than_seconds=1, now=100000) == []
    assert fake.is_dir()


def test_short_writes_are_completed(quarantine, monkeypatch):
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real(fd, bytes(data[:2])))
    with quarantine.begin() as staged:
        staged.write(b"abcdefg")
        staged.sync_and_close()
        assert staged.path.read_bytes() == b"abcdefg"


def test_an_existing_regular_file_is_never_reused(quarantine, monkeypatch):
    fixed = "1" * 32
    monkeypatch.setattr("secrets.token_hex", lambda _n: fixed)
    planted = quarantine._layout.quarantine / f"stage-{fixed}.part"
    planted.write_bytes(b"attacker")
    with pytest.raises(FileExistsError):
        quarantine.begin()
    assert planted.read_bytes() == b"attacker"
