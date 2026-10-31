"""CAS read/open by digest (IMP-0050).

Objects are addressed only by digest (the path is derived, never supplied). Opening is read-only,
refuses symlinks, and checks the structural invariants an admitted object must still have: a regular,
single-link, user-owned, non-writable file. This proves *shape*, not content; byte integrity is proven
by IMP-0052's verification, which `open_verified` runs on the very descriptor it then returns, so no
other file can be swapped in between the check and the read.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Final

from trinetra.errors import TrinetraError

if TYPE_CHECKING:
    from trinetra.domain.digests import DigestSha256
    from trinetra.infrastructure.cas_layout import CasLayout

READ_CHUNK: Final = 1024 * 1024


class ObjectMissingError(TrinetraError):
    code = "CAS_OBJECT_MISSING"
    default_message = "no CAS object exists for this digest"


class ObjectShapeError(TrinetraError):
    code = "CAS_OBJECT_MALFORMED"
    default_message = "the CAS object is not an immutable private regular file"


@dataclass(frozen=True)
class OpenedObject:
    """A read-only handle plus the size observed on that same handle."""

    stream: BinaryIO
    size: int

    def close(self) -> None:
        self.stream.close()


def check_shape(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        msg = "the object is not a regular file"
        raise ObjectShapeError(msg)
    if info.st_nlink != 1:
        msg = "the object has more than one hard link"
        raise ObjectShapeError(msg)
    if info.st_uid != os.geteuid():
        msg = "the object is not owned by the running user"
        raise ObjectShapeError(msg)
    if info.st_mode & 0o222:
        msg = "the object is writable"
        raise ObjectShapeError(msg)


def open_fd(layout: CasLayout, digest: DigestSha256 | str) -> tuple[int, os.stat_result]:
    """Open the object read-only and return (fd, fstat of that fd) after the shape checks."""
    path = layout.object_path(digest)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        msg = "no CAS object exists for this digest"
        raise ObjectMissingError(msg) from exc
    except OSError as exc:  # ELOOP for a symlink, EACCES, ...
        msg = "the object path cannot be opened as a plain file"
        raise ObjectShapeError(msg) from exc
    try:
        info = os.fstat(fd)
        check_shape(info)
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def open_object(layout: CasLayout, digest: DigestSha256 | str) -> OpenedObject:
    fd, info = open_fd(layout, digest)
    return OpenedObject(os.fdopen(fd, "rb"), info.st_size)


def read_object(layout: CasLayout, digest: DigestSha256 | str, *, max_bytes: int) -> bytes:
    """Read a whole object into memory, refusing anything larger than `max_bytes` before reading."""
    if max_bytes < 0:
        msg = "max_bytes must not be negative"
        raise ObjectShapeError(msg)
    opened = open_object(layout, digest)
    try:
        if opened.size > max_bytes:
            msg = "the object is larger than the caller's limit"
            raise ObjectShapeError(msg)
        return opened.stream.read(opened.size)
    finally:
        opened.close()
