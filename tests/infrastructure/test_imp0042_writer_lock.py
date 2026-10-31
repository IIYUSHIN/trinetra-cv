"""IMP-0042 tests: Core sole-writer process lock."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import trinetra
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.writer_lock import (
    LOCK_FILENAME,
    CoreWriterLock,
    WriterLockHeldError,
    WriterLockStateError,
)

SRC = str(Path(trinetra.__file__).resolve().parent.parent)


@pytest.fixture
def config(tmp_path: Path) -> DatabaseConfig:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    return DatabaseConfig(root)


def child(code: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), str(root)],
        check=False, env=env, capture_output=True, timeout=timeout,
    )


ACQUIRE = """
import sys, os
from pathlib import Path
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.writer_lock import CoreWriterLock, WriterLockHeldError
try:
    lock = CoreWriterLock.acquire(DatabaseConfig(Path(sys.argv[1])))
except WriterLockHeldError as exc:
    print("HELD", exc.details.get("holder_pid_hint", ""))
    sys.exit(7)
print("ACQUIRED")
%s
"""


def test_acquire_creates_a_private_lock_file_under_the_state_root(config):
    with CoreWriterLock.acquire(config) as lock:
        assert lock.path == config.state_root.resolve() / LOCK_FILENAME
        mode = lock.path.stat().st_mode & 0o777
        assert mode == 0o600 and lock.held
        assert lock.path.read_text().startswith(f"pid={os.getpid()}\n")


def test_release_is_idempotent_and_blocks_further_use(config):
    lock = CoreWriterLock.acquire(config)
    lock.release()
    lock.release()
    assert lock.held is False
    with pytest.raises(WriterLockStateError, match="released"):
        lock.assert_held()


def test_the_lock_can_be_reacquired_after_release(config):
    CoreWriterLock.acquire(config).release()
    with CoreWriterLock.acquire(config):
        pass


def test_a_second_acquire_in_the_same_process_is_refused(config):
    with CoreWriterLock.acquire(config):
        with pytest.raises(WriterLockHeldError) as info:
            CoreWriterLock.acquire(config)
        assert info.value.code == "DB_WRITER_LOCK_HELD"
        assert info.value.details["holder_pid_hint"] == str(os.getpid())


def test_a_second_process_is_refused_while_the_first_holds(config):
    with CoreWriterLock.acquire(config):
        done = child(ACQUIRE % "", config.state_root)
        assert done.returncode == 7, done.stderr.decode()
        assert done.stdout.decode().startswith("HELD")


def test_a_hard_crash_never_leaves_a_stale_lock(config):
    done = child(ACQUIRE % "os._exit(9)", config.state_root)
    assert done.returncode == 9 and b"ACQUIRED" in done.stdout
    with CoreWriterLock.acquire(config):  # the kernel dropped the dead holder's lock
        pass


def test_exception_inside_with_still_releases(config):
    with pytest.raises(RuntimeError), CoreWriterLock.acquire(config):
        raise RuntimeError("boom")
    with CoreWriterLock.acquire(config):
        pass


def test_a_symlinked_lock_file_is_refused(config, tmp_path):
    target = tmp_path / "elsewhere"
    target.write_text("")
    (config.state_root / LOCK_FILENAME).symlink_to(target)
    with pytest.raises(WriterLockStateError, match="symbolic link"):
        CoreWriterLock.acquire(config)


def test_a_directory_in_place_of_the_lock_file_is_refused(config):
    (config.state_root / LOCK_FILENAME).mkdir()
    with pytest.raises(WriterLockStateError):
        CoreWriterLock.acquire(config)


def test_extra_hard_links_are_refused(config, tmp_path):
    original = tmp_path / "original"
    original.write_text("")
    os.link(original, config.state_root / LOCK_FILENAME)
    with pytest.raises(WriterLockStateError, match="hard links"):
        CoreWriterLock.acquire(config)


def test_a_loose_mode_is_tightened_to_0600(config):
    path = config.state_root / LOCK_FILENAME
    path.write_text("")
    path.chmod(0o666)
    with CoreWriterLock.acquire(config) as lock:
        assert lock.path.stat().st_mode & 0o777 == 0o600


def test_replacing_the_lock_file_is_detected(config):
    with CoreWriterLock.acquire(config) as lock:
        lock.path.unlink()
        with pytest.raises(WriterLockStateError, match="disappeared"):
            lock.assert_held()
        lock.path.write_text("")  # a new file at the same path is a different inode
        with pytest.raises(WriterLockStateError, match="replaced"):
            lock.assert_held()
        assert lock.held is False


FORK = """
import os, sys
from pathlib import Path
from trinetra.infrastructure.sqlite import DatabaseConfig
from trinetra.infrastructure.writer_lock import CoreWriterLock, WriterLockStateError
lock = CoreWriterLock.acquire(DatabaseConfig(Path(sys.argv[1])))
pid = os.fork()
if pid == 0:
    try:
        lock.assert_held()
    except WriterLockStateError:
        os._exit(0)      # the child correctly refuses to act as the writer
    os._exit(5)
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0, status
lock.assert_held()       # the parent still owns it
print("OK")
"""


def test_a_forked_child_does_not_inherit_write_authority(config):
    done = child(FORK, config.state_root)
    assert done.returncode == 0 and b"OK" in done.stdout, done.stderr.decode()


def test_a_forked_child_that_outlives_the_parent_does_not_keep_the_lock_alive(config):
    code = """
    import os, sys, time
    from pathlib import Path
    from trinetra.infrastructure.sqlite import DatabaseConfig
    from trinetra.infrastructure.writer_lock import CoreWriterLock
    lock = CoreWriterLock.acquire(DatabaseConfig(Path(sys.argv[1])))
    if os.fork() == 0:
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 1)
        os.dup2(null, 2)          # do not hold the test's pipes open
        time.sleep(4)             # orphaned child that would keep an inherited descriptor alive
        os._exit(0)
    os._exit(0)                   # parent dies; the lock must die with it
    """
    done = child(code, config.state_root)
    assert done.returncode == 0
    with CoreWriterLock.acquire(config):  # succeeds while the orphan is still running
        pass


def test_invalid_state_roots_are_refused_before_any_file_is_created(tmp_path):
    from trinetra.infrastructure.sqlite import DatabaseConfigError

    missing = DatabaseConfig(tmp_path / "missing")
    with pytest.raises(DatabaseConfigError):
        CoreWriterLock.acquire(missing)
    assert not (tmp_path / "missing").exists()


def test_assert_held_rejects_a_different_pid(config, monkeypatch):
    from trinetra.infrastructure import writer_lock as module

    with CoreWriterLock.acquire(config) as lock:
        other = os.getpid() + 1
        monkeypatch.setattr(module.os, "getpid", lambda: other)
        with pytest.raises(WriterLockStateError, match="different process"):
            lock.assert_held()


def test_a_hard_link_created_after_acquisition_is_detected(config, tmp_path):
    with CoreWriterLock.acquire(config) as lock:
        os.link(lock.path, tmp_path / "second-name")
        with pytest.raises(WriterLockStateError, match="hard links"):
            lock.assert_held()


def test_a_fifo_in_place_of_the_lock_file_is_refused(config):
    os.mkfifo(config.state_root / LOCK_FILENAME)
    with pytest.raises(WriterLockStateError, match="regular file"):
        CoreWriterLock.acquire(config)
