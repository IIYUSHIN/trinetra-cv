"""Core sole-writer process lock (IMP-0042).

Exactly one Core process may hold authoritative write authority for a state root. The lock is an
advisory `flock` on a file in the validated state root; the kernel releases it when the holder dies,
so a crash never leaves a stale lock behind. The pid written into the file is a diagnostic hint only
and is never trusted. `assert_held` re-proves the lock on demand: same process (a forked child does not
inherit authority), descriptor still open, and the lock file still the same inode (an unlinked and
recreated lock file would silently admit a second writer).

PROPOSED engineering decisions: file name `core-writer.lock`, mode 0600, owner = running user, a single
hard link, POSIX only (no portable fallback is faked: other platforms refuse with a clear error).
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import sys
import weakref
from typing import TYPE_CHECKING, Final, Self

from trinetra.errors import TrinetraError
from trinetra.infrastructure.sqlite import DatabaseConfig, validate_database_path
from trinetra.timeutil import SystemClock, format_utc, truncate_to_seconds

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

LOCK_FILENAME: Final = "core-writer.lock"
MAX_HINT_BYTES: Final = 128


class WriterLockHeldError(TrinetraError):
    code = "DB_WRITER_LOCK_HELD"
    default_message = "another Core process already holds the writer lock for this state root"


class WriterLockStateError(TrinetraError):
    code = "DB_WRITER_LOCK_STATE"
    default_message = "the writer lock is not held or its lock file is not trustworthy"


class WriterLockUnsupportedError(TrinetraError):
    code = "DB_WRITER_LOCK_UNSUPPORTED"
    default_message = "this platform has no supported advisory-lock primitive"


_LIVE: weakref.WeakSet[CoreWriterLock] = weakref.WeakSet()


def _after_fork_in_child() -> None:
    for lock in list(_LIVE):
        lock._drop_inherited_descriptor()  # the child never owns the parent's authority


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)


def _check_lock_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise WriterLockStateError("the lock path is not a regular file")
    if info.st_nlink != 1:
        raise WriterLockStateError("the lock file has extra hard links")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise WriterLockStateError("the lock file is not owned by the running user")


def _stamp() -> bytes:
    started = format_utc(truncate_to_seconds(SystemClock().now()))
    return f"pid={os.getpid()}\nstarted={started}\n".encode("ascii")


def _read_hint(fd: int) -> str | None:
    try:
        raw = os.pread(fd, MAX_HINT_BYTES, 0)
    except OSError:
        return None
    text = raw.decode("ascii", errors="replace")
    for line in text.splitlines():
        if line.startswith("pid=") and line[4:].isdigit() and len(line) <= 24:
            return line[4:]
    return None


class CoreWriterLock:
    """Held-lock handle. Construct with `acquire`; release with `release()` or a `with` block."""

    __slots__ = ("__weakref__", "_fd", "_inode", "_owner_pid", "_path")

    def __init__(self, path: Path, fd: int, inode: int) -> None:
        self._path = path
        self._fd: int | None = fd
        self._inode = inode
        self._owner_pid = os.getpid()
        _LIVE.add(self)

    @classmethod
    def acquire(cls, config: DatabaseConfig) -> Self:
        if sys.platform == "win32":
            raise WriterLockUnsupportedError

        database_path = validate_database_path(config)
        path = database_path.parent / LOCK_FILENAME
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise WriterLockStateError("the lock file is a symbolic link") from exc
            name = errno.errorcode.get(exc.errno or 0, "ERR")
            raise WriterLockStateError(f"cannot open the lock file: {name}") from exc
        try:
            info = os.fstat(fd)
            _check_lock_file(info)
            if info.st_mode & 0o077:
                os.fchmod(fd, 0o600)
            cls._flock_or_raise(fd)
            os.ftruncate(fd, 0)
            os.pwrite(fd, _stamp(), 0)  # diagnostic hint only: no fsync, never trusted
        except BaseException:
            os.close(fd)
            raise
        return cls(path, fd, info.st_ino)

    @staticmethod
    def _flock_or_raise(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = _read_hint(fd)
            details = {"holder_pid_hint": holder} if holder else None
            raise WriterLockHeldError(details=details) from exc

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        try:
            self.assert_held()
        except WriterLockStateError:
            return False
        return True

    def assert_held(self) -> None:
        """Raise unless this process still provably owns the lock."""
        fd = self._fd
        if fd is None:
            raise WriterLockStateError("the writer lock was released")
        if os.getpid() != self._owner_pid:
            raise WriterLockStateError("the writer lock belongs to a different process")
        try:
            held = os.fstat(fd)
            on_disk = os.stat(self._path, follow_symlinks=False)
        except OSError as exc:
            raise WriterLockStateError("the lock file disappeared") from exc
        if (held.st_ino, held.st_dev) != (on_disk.st_ino, on_disk.st_dev) or held.st_ino != self._inode:
            raise WriterLockStateError("the lock file was replaced")
        if held.st_nlink != 1:
            raise WriterLockStateError("the lock file has extra hard links")

    def _drop_inherited_descriptor(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:  # runs only in a forked child (see _after_fork_in_child)
            with contextlib.suppress(OSError):
                os.close(fd)

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> Self:
        self.assert_held()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


__all__ = [
    "LOCK_FILENAME",
    "CoreWriterLock",
    "WriterLockHeldError",
    "WriterLockStateError",
    "WriterLockUnsupportedError",
]
