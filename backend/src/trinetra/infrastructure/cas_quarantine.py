"""CAS staged-write / quarantine directory (IMP-0047).

Untrusted bytes land here first and nowhere else. A staged file is created with a Core-generated name
(never caller-supplied), exclusively, without following symlinks, mode 0600, inside the verified
quarantine directory. Nothing in quarantine is evidence or an admitted asset; admission to the CAS is a
separate verified step (IMP-0048/0049). A staged file is discarded unless it is explicitly handed on, so
a failed or abandoned write cannot leave bytes that look admitted. Orphans from a crashed process are
reported and swept by age, never trusted.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
import time
from typing import TYPE_CHECKING, Final, Self

from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from trinetra.infrastructure.cas_layout import CasLayout

STAGE_PREFIX: Final = "stage-"
STAGE_SUFFIX: Final = ".part"
FILE_MODE: Final = 0o600
DEFAULT_MAX_BYTES: Final = 4 * 1024 * 1024 * 1024
_NAME_LEN: Final = len(STAGE_PREFIX) + 32 + len(STAGE_SUFFIX)


class QuarantineError(TrinetraError):
    code = "CAS_QUARANTINE_INVALID"
    default_message = "the CAS quarantine operation is invalid or untrustworthy"


def is_stage_name(name: str) -> bool:
    """True only for names this module could have generated."""
    body = name[len(STAGE_PREFIX) : -len(STAGE_SUFFIX)]
    return (
        len(name) == _NAME_LEN
        and name.startswith(STAGE_PREFIX)
        and name.endswith(STAGE_SUFFIX)
        and all(c in "0123456789abcdef" for c in body)
    )


class StagedWrite:
    """One exclusively-created quarantine file. Discarded on exit unless `release()` was called."""

    def __init__(self, path: Path, fd: int, max_bytes: int) -> None:
        self._path = path
        self._fd: int | None = fd
        self._max = max_bytes
        self._written = 0
        self._released = False

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def path(self) -> Path:
        return self._path

    @property
    def size(self) -> int:
        return self._written

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def write(self, chunk: bytes) -> None:
        if self._fd is None:
            msg = "the staged write is already closed"
            raise QuarantineError(msg)
        if self._written + len(chunk) > self._max:
            msg = "the staged write exceeds the configured size limit"
            raise QuarantineError(msg)
        view = memoryview(chunk)
        while view:
            count = os.write(self._fd, view)
            view = view[count:]
        self._written += len(chunk)

    def sync_and_close(self) -> None:
        """Make the bytes durable, then stop accepting writes. Idempotent."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def release(self) -> Path:
        """Hand the (closed, durable) file to the next stage; it is no longer discarded on exit."""
        self.sync_and_close()
        self._released = True
        return self._path

    def discard(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._released:
            self.discard()


class Quarantine:
    """Factory and janitor for the quarantine directory of a CAS layout."""

    def __init__(self, layout: CasLayout, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes < 0:
            msg = "max_bytes must not be negative"
            raise QuarantineError(msg)
        self._layout = layout
        self._max = max_bytes

    def begin(self) -> StagedWrite:
        directory = self._layout.quarantine
        self._layout.check_directory(directory)
        name = f"{STAGE_PREFIX}{secrets.token_hex(16)}{STAGE_SUFFIX}"
        path = directory / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(path, flags, FILE_MODE)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            os.close(fd)
            path.unlink()
            msg = "the staged file is not a private regular file"
            raise QuarantineError(msg)
        return StagedWrite(path, fd, self._max)

    def orphans(self) -> list[str]:
        """Names present in quarantine. Anything not generated here is reported as foreign, not trusted."""
        return sorted(entry.name for entry in self._layout.quarantine.iterdir())

    def sweep(self, *, older_than_seconds: float, now: float | None = None) -> list[str]:
        """Remove generated stage files older than the cutoff; foreign entries are never touched."""
        if older_than_seconds < 0:
            msg = "older_than_seconds must not be negative"
            raise QuarantineError(msg)
        cutoff = (time.time() if now is None else now) - older_than_seconds
        removed: list[str] = []
        for entry in sorted(self._layout.quarantine.iterdir()):
            if not is_stage_name(entry.name):
                continue
            info = entry.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                entry.unlink()
                removed.append(entry.name)
        return removed
